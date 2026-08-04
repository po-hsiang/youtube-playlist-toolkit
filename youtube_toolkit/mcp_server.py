"""yt-mcp：把 YouTube 唯讀查詢開放給多個 AI Agent 同時使用的 MCP ＋ REST 伺服器。

**提供兩類來源完全不同的資料**，工具描述刻意寫明差異，避免 agent 叫錯工具：

1. **使用者自己的播放清單**——search_songs／random_song／list_playlists／refresh_playlist
   走記憶體快取，載入後查詢 0 配額。
2. **YouTube 公開資料**——trending_videos（發燒影片榜）、get_video_info（單片中繼資料）
   即時查詢、不快取，1 unit／次；get_video_transcript（字幕全文）走網頁端資料，
   **0 配額**。皆與使用者的播放清單無關。

- MCP 端點（Streamable HTTP）：http://<host>:<port>/mcp
- REST 端點（給非 AI 服務，與 MCP 共用同一份快取與 client）：
  GET /health、GET /playlists、GET /search?q=...&playlist=...&limit=...、
  GET /random?playlist=...&count=...&q=...、GET /trending?category=...&limit=...&region=...、
  GET /video/{video_id}、GET /transcript/{video_id}?max_chars=...、GET /refresh?playlist=...
- 安全邊界：只用 API Key、**唯讀**——不暴露任何寫入功能（排序／清除只能在本機手動執行）。
- 快取：清單首次被查詢時載入並常駐記憶體，TTL（預設 6 小時）過期自動重抓。
  抓取一律經 QuotaManager 記帳（與其他工具共用 quota_state.json，合併計算）。

執行方式：uv run yt-mcp（設定見 .env 的 MCP_HOST / MCP_PORT / MCP_CACHE_TTL_MINUTES）
"""

import random
import threading
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Dict, List, Optional

from anyio import to_thread
from googleapiclient.errors import HttpError
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from youtube_toolkit import config, playlists, transcript, trending, video_info
from youtube_toolkit.log_utils import logger
from youtube_toolkit.song_search import (
    DEFAULT_LIMIT,
    DEFAULT_RANDOM_COUNT,
    MIN_KEYWORD_LENGTH,
    pick_random_songs,
    search_playlists,
)
from youtube_toolkit.youtube_client import YouTubeClient

DEFAULT_RESULT_LIMIT = DEFAULT_LIMIT

ALL_PLAYLISTS = "*"  # playlist 參數傳這個＝跨所有清單
RANDOM_TARGET_SECTION = "random_song"  # playlists.toml 中抽歌預設清單的區段名

_CLIENT: Optional[YouTubeClient] = None
_CLIENT_LOCK = threading.Lock()


def shared_client() -> YouTubeClient:
    """全伺服器共用同一個 client（延遲建立：匯入模組時不觸發 API Key 檢查）。

    **必須共用**：QuotaManager 只在建構時載入狀態檔，兩個實例會各記各的計數、
    互相覆蓋 quota_state.json，導致配額被低估。
    """
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = YouTubeClient.for_public_data()
        return _CLIENT


class SongCache:
    """執行緒安全的清單快取：載入一次、TTL 內重複查詢不耗配額。"""

    def __init__(self, client: Optional[YouTubeClient] = None, ttl_minutes: Optional[int] = None):
        self._client = client
        self._ttl = timedelta(minutes=config.MCP_CACHE_TTL_MINUTES if ttl_minutes is None else ttl_minutes)
        self._lock = threading.Lock()
        self._store: Dict[str, Dict[str, Any]] = {}  # 清單名稱 -> {"videos": [...], "fetched_at": datetime}

    @property
    def client(self) -> YouTubeClient:
        return self._client or shared_client()

    def get_videos(self, name: str, force: bool = False) -> List[Dict[str, Any]]:
        playlist_id = playlists.get_playlist_id(name)  # 名稱不存在會拋 KeyError（含可用名稱）
        with self._lock:
            entry = self._store.get(name)
            if entry and not force and datetime.now() - entry["fetched_at"] < self._ttl:
                return entry["videos"]
            logger.info(f"[Cache] 載入清單「{name}」...")
            videos = self.client.fetch_playlist_videos(playlist_id)
            self._store[name] = {"videos": videos, "fetched_at": datetime.now()}
            return videos

    def status(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                name: {
                    "songs": len(entry["videos"]),
                    "fetched_at": entry["fetched_at"].isoformat(timespec="seconds"),
                }
                for name, entry in self._store.items()
            }

    def cached_names(self) -> List[str]:
        with self._lock:
            return list(self._store)


CACHE = SongCache()


# ── 核心邏輯（純函式，MCP 工具與 REST 路由共用）──────────────


def perform_search(
    keyword: str, playlist: str = "", limit: int = DEFAULT_RESULT_LIMIT, cache: Optional[SongCache] = None
) -> Dict[str, Any]:
    cache = cache or CACHE
    target_names = [playlist] if playlist else list(playlists.load_all())
    return search_playlists(keyword, target_names, cache.get_videos, limit)


def random_target_names(playlist: str) -> List[str]:
    """決定抽歌要從哪些清單抽。

    留空＝playlists.toml 的 [random_song].target（預設只鎖定一份清單：跨全部 13 份
    會在冷快取時一次載入約 2,400 首，又慢又耗配額）；"*"＝全部清單；其餘＝指定名稱。
    """
    if playlist == ALL_PLAYLISTS:
        return list(playlists.load_all())
    if playlist:
        return [playlist]
    return [playlists.tool_target(RANDOM_TARGET_SECTION)[0]]


def perform_random(
    playlist: str = "",
    count: int = DEFAULT_RANDOM_COUNT,
    keyword: str = "",
    cache: Optional[SongCache] = None,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    cache = cache or CACHE
    return pick_random_songs(random_target_names(playlist), cache.get_videos, count, keyword, rng)


def perform_trending(
    category: str = "all",
    limit: int = trending.DEFAULT_LIMIT,
    region: str = "",
    client: Optional[YouTubeClient] = None,
) -> Dict[str, Any]:
    """查發燒影片榜。

    **刻意不快取**：成本只有 1 unit，而榜單約每 15 分鐘就換一批——
    快取省不了什麼，卻會回過期的名次。未知類別會拋 ValueError。
    """
    category = (category or "all").strip().lower()
    category_id = trending.resolve_category(category)
    region = (region or config.TRENDING_REGION).strip().upper()
    limit = max(1, min(int(limit), trending.MAX_LIMIT))

    items = (client or shared_client()).fetch_most_popular(region, limit, category_id)
    return trending.build_result(region, category, items)


def perform_video_info(video_id: str, client: Optional[YouTubeClient] = None) -> Dict[str, Any]:
    """查單一影片的中繼資料（1 unit，不快取——下游要拿它判斷「現在」是不是直播）。

    video_id 格式不合法拋 ValueError、查無影片（含私人）拋 KeyError，
    args[0] 皆為契約錯誤碼字串（INVALID_VIDEO_ID / VIDEO_NOT_FOUND）。
    """
    if not video_info.is_valid_video_id(video_id):
        raise ValueError(video_info.INVALID_VIDEO_ID)
    item = (client or shared_client()).fetch_video(video_id)
    if item is None:
        raise KeyError(video_info.VIDEO_NOT_FOUND)
    return video_info.to_video_info(item)


def perform_transcript(
    video_id: str, max_chars: Optional[int] = None, api: Optional[Any] = None
) -> Dict[str, Any]:
    """抓影片字幕純文字（0 配額——走網頁端資料，不經 YouTube Data API）。

    video_id 格式不合法拋 ValueError(INVALID_VIDEO_ID)；
    其餘錯誤由 transcript 模組拋 TranscriptUnavailable(404 類)／TranscriptUpstreamError(502)。
    """
    if not video_info.is_valid_video_id(video_id):
        raise ValueError(video_info.INVALID_VIDEO_ID)
    return transcript.fetch_transcript(video_id, max_chars, api=api)


def playlists_overview(cache: Optional[SongCache] = None) -> Dict[str, Any]:
    cache = cache or CACHE
    cached = cache.status()
    return {
        "playlists": [
            {"name": name, "cached": name in cached, **cached.get(name, {})}
            for name in playlists.load_all()
        ],
        "cache_ttl_minutes": config.MCP_CACHE_TTL_MINUTES,
    }


def perform_refresh(playlist: str = "", cache: Optional[SongCache] = None) -> Dict[str, Any]:
    cache = cache or CACHE
    names = [playlist] if playlist else cache.cached_names()
    refreshed = {name: len(cache.get_videos(name, force=True)) for name in names}
    return {"refreshed": refreshed}


# ── MCP 工具 ─────────────────────────────────────────────

mcp = FastMCP(
    "yt-music-search",
    host=config.MCP_HOST,
    port=config.MCP_PORT,
    stateless_http=True,  # 多個 agent 同時查詢，無狀態最穩
)


@mcp.tool()
def search_songs(keyword: str, playlist: str = "", limit: int = DEFAULT_RESULT_LIMIT) -> Dict[str, Any]:
    """在 YouTube 歌單中搜尋歌曲（比對歌名與頻道名稱，不分大小寫）。

    keyword：至少 2 個字元。playlist：留空＝搜尋全部清單，或指定名稱（見 list_playlists）。
    limit：回傳筆數上限（預設 50）。查詢走本地快取，不耗 YouTube API 配額。
    """
    return perform_search(keyword, playlist, limit)


@mcp.tool()
def random_song(
    playlist: str = "", count: int = DEFAULT_RANDOM_COUNT, keyword: str = ""
) -> Dict[str, Any]:
    """從播放清單隨機抽歌（點歌／推薦歌曲時使用）。

    playlist：留空＝預設清單（playlists.toml 的 [random_song].target），
    `*`＝所有清單，或指定名稱（見 list_playlists）。
    count：抽幾首不重複的歌（1～10，預設 1）。
    keyword：只從歌名／頻道名稱命中的歌曲中抽（選填，至少 2 個字元）。
    抽選走本地快取，不耗 YouTube API 配額；候選為空時 songs 會是空陣列。
    """
    return perform_random(playlist, count, keyword)


@mcp.tool()
def trending_videos(
    category: str = "all", limit: int = trending.DEFAULT_LIMIT, region: str = ""
) -> Dict[str, Any]:
    """查 YouTube 官方發燒影片榜（Trending）。

    ⚠️ 這是**公開的地區榜單，與使用者自己的播放清單無關**——
    要找使用者收藏的歌請改用 search_songs 或 random_song。

    category：all／music／gaming／film／sports／comedy／entertainment／news／tech（預設 all）。
    limit：取前幾名（1～50，預設 3）。
    region：ISO 3166-1 兩碼國碼，預設 TW（台灣榜，不是全球榜）。
    即時查詢不走快取，成本 1 unit；部分地區沒有某些類別的榜單。
    """
    return perform_trending(category, limit, region)


@mcp.tool()
def get_video_info(video_id: str) -> Dict[str, Any]:
    """查單一 YouTube 影片的中繼資料（時長、是否直播中、觀看數、縮圖）。

    用途：播放前的時長預檢、婉拒直播、組 embed 呈現。任何公開影片都可查，
    不限於使用者的播放清單。video_id 是網址 watch?v= 後面的 11 碼。
    即時查詢不走快取，成本 1 unit。
    錯誤以 {"error": "INVALID_VIDEO_ID"|"VIDEO_NOT_FOUND"} 回傳（私人影片視同查無）。
    """
    try:
        return perform_video_info(video_id)
    except (ValueError, KeyError) as e:
        return {"error": e.args[0]}


@mcp.tool()
def get_video_transcript(
    video_id: str, max_chars: int = transcript.DEFAULT_MCP_MAX_CHARS
) -> Dict[str, Any]:
    """抓 YouTube 影片的字幕純文字（給 LLM 摘要影片內容用）。

    字幕軌自動挑選：人工上傳優先（繁中 → 英文 → 任一語言），再退自動生成字幕。
    video_id 是網址 watch?v= 後面的 11 碼。走網頁端資料，**不消耗 API 配額**。
    max_chars：超過即截斷並標 truncated: true（預設 8000，避免塞爆 context；
    char_count 一律是完整字幕長度）。傳 0 表示不截斷——字幕可能長達數十萬字元，慎用。
    錯誤以 {"error": "INVALID_VIDEO_ID"|"VIDEO_NOT_FOUND"|"NO_TRANSCRIPT"|
    "TRANSCRIPT_UPSTREAM_ERROR"} 回傳；UPSTREAM 表示被 YouTube 暫時限流，可稍後重試。
    """
    try:
        return perform_transcript(video_id, max_chars if max_chars > 0 else None)
    except (ValueError, transcript.TranscriptUnavailable, transcript.TranscriptUpstreamError) as e:
        return {"error": e.args[0]}


@mcp.tool()
def list_playlists() -> Dict[str, Any]:
    """列出所有可搜尋的播放清單，以及各清單的快取狀態（歌曲數、上次載入時間）。"""
    return playlists_overview()


@mcp.tool()
def refresh_playlist(playlist: str = "") -> Dict[str, Any]:
    """強制重新抓取清單內容（清單有新增歌曲時使用）。留空＝重抓所有已快取的清單。"""
    return perform_refresh(playlist)


# ── REST 路由（非 AI 服務用；抓取可能阻塞，丟到 worker thread）─────


class BadRequest(Exception):
    """查詢參數格式錯誤 → 400。"""


def _int_param(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name, str(default))
    try:
        return int(raw)
    except ValueError:
        raise BadRequest(f"{name} 必須是整數（收到「{raw}」）") from None


def _error_response(e: Exception, status_code: int) -> JSONResponse:
    # 用 args[0] 而非 str(e)：KeyError 的 str() 會多包一層引號，洩漏 Python repr
    return JSONResponse({"error": e.args[0] if e.args else "請求無法處理"}, status_code=status_code)


@mcp.custom_route("/health", methods=["GET"])
async def rest_health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "cached_playlists": CACHE.cached_names()})


@mcp.custom_route("/playlists", methods=["GET"])
async def rest_playlists(_: Request) -> JSONResponse:
    return JSONResponse(playlists_overview())


@mcp.custom_route("/search", methods=["GET"])
async def rest_search(request: Request) -> JSONResponse:
    keyword = request.query_params.get("q", "")
    playlist = request.query_params.get("playlist", "")
    try:
        limit = _int_param(request, "limit", DEFAULT_RESULT_LIMIT)
        result = await to_thread.run_sync(partial(perform_search, keyword, playlist, limit))
    except BadRequest as e:
        return _error_response(e, 400)
    except KeyError as e:
        return _error_response(e, 404)
    status = 400 if "error" in result else 200
    return JSONResponse(result, status_code=status)


@mcp.custom_route("/random", methods=["GET"])
async def rest_random(request: Request) -> JSONResponse:
    playlist = request.query_params.get("playlist", "")
    keyword = request.query_params.get("q", "")
    try:
        count = _int_param(request, "count", DEFAULT_RANDOM_COUNT)
        result = await to_thread.run_sync(partial(perform_random, playlist, count, keyword))
    except BadRequest as e:
        return _error_response(e, 400)
    except KeyError as e:
        return _error_response(e, 404)
    status = 400 if "error" in result else 200
    return JSONResponse(result, status_code=status)


@mcp.custom_route("/video/{video_id}", methods=["GET"])
async def rest_video(request: Request) -> JSONResponse:
    video_id = request.path_params["video_id"]
    try:
        result = await to_thread.run_sync(partial(perform_video_info, video_id))
    except ValueError as e:
        return _error_response(e, 400)  # {"error": "INVALID_VIDEO_ID"}
    except KeyError as e:
        return _error_response(e, 404)  # {"error": "VIDEO_NOT_FOUND"}
    return JSONResponse(result)


@mcp.custom_route("/transcript/{video_id}", methods=["GET"])
async def rest_transcript(request: Request) -> JSONResponse:
    video_id = request.path_params["video_id"]
    try:
        max_chars: Optional[int] = _int_param(request, "max_chars", 0)  # REST 預設不截斷
        result = await to_thread.run_sync(
            partial(perform_transcript, video_id, max_chars if max_chars > 0 else None)
        )
    except BadRequest as e:
        return _error_response(e, 400)
    except ValueError as e:
        return _error_response(e, 400)  # {"error": "INVALID_VIDEO_ID"}
    except transcript.TranscriptUnavailable as e:
        return _error_response(e, 404)  # {"error": "VIDEO_NOT_FOUND"|"NO_TRANSCRIPT"}
    except transcript.TranscriptUpstreamError as e:
        return _error_response(e, 502)  # {"error": "TRANSCRIPT_UPSTREAM_ERROR"}，可重試
    return JSONResponse(result)


@mcp.custom_route("/trending", methods=["GET"])
async def rest_trending(request: Request) -> JSONResponse:
    category = request.query_params.get("category", "all")
    region = request.query_params.get("region", "")
    try:
        limit = _int_param(request, "limit", trending.DEFAULT_LIMIT)
        result = await to_thread.run_sync(partial(perform_trending, category, limit, region))
    except (BadRequest, ValueError) as e:
        return _error_response(e, 400)
    except HttpError as e:
        # 類別 ID 合法不代表該地區有榜（實測 TW 的 29 回 404、不存在的 ID 回 400），
        # 這兩種都是「查不到」而非伺服器錯誤，不能讓 agent 收到 500。
        return JSONResponse(
            {
                "error": f"查不到 {region or config.TRENDING_REGION} 的「{category}」發燒榜"
                f"（YouTube 回 HTTP {e.resp.status}）。請改用 all 或換一個類別／地區。"
            },
            status_code=404,
        )
    return JSONResponse(result)


@mcp.custom_route("/refresh", methods=["GET", "POST"])
async def rest_refresh(request: Request) -> JSONResponse:
    playlist = request.query_params.get("playlist", "")
    try:
        result = await to_thread.run_sync(partial(perform_refresh, playlist))
    except KeyError as e:
        return _error_response(e, 404)
    return JSONResponse(result)


def main() -> None:
    logger.info(
        f"yt-mcp 啟動：MCP=http://{config.MCP_HOST}:{config.MCP_PORT}/mcp，"
        f"REST=/health /playlists /search /random /trending /video/{{id}} /transcript/{{id}} /refresh，"
        f"快取 TTL {config.MCP_CACHE_TTL_MINUTES} 分鐘，發燒榜地區 {config.TRENDING_REGION}"
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
