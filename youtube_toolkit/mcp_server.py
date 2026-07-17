"""yt-mcp：把歌單搜尋開放給多個 AI Agent 同時查詢的 MCP ＋ REST 伺服器。

- MCP 端點（Streamable HTTP）：http://<host>:<port>/mcp
  工具：search_songs / list_playlists / refresh_playlist
- REST 端點（給非 AI 服務，與 MCP 共用同一份快取）：
  GET /health、GET /playlists、GET /search?q=...&playlist=...&limit=...、GET /refresh?playlist=...
- 安全邊界：只用 API Key、**唯讀**——不暴露任何寫入功能（排序／清除只能在本機手動執行）。
- 快取：清單首次被查詢時載入並常駐記憶體，TTL（預設 6 小時）過期自動重抓；
  之後所有查詢都是本地搜尋、不耗 YouTube 配額。抓取一律經 QuotaManager 記帳
  （與其他工具共用 quota_state.json，合併計算）。

執行方式：uv run yt-mcp（設定見 .env 的 MCP_HOST / MCP_PORT / MCP_CACHE_TTL_MINUTES）
"""

import threading
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Dict, List, Optional

from anyio import to_thread
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from youtube_toolkit import config, playlists
from youtube_toolkit.log_utils import logger
from youtube_toolkit.youtube_client import YouTubeClient

MIN_KEYWORD_LENGTH = 2
DEFAULT_RESULT_LIMIT = 50


class SongCache:
    """執行緒安全的清單快取：載入一次、TTL 內重複查詢不耗配額。"""

    def __init__(self, client: Optional[YouTubeClient] = None, ttl_minutes: Optional[int] = None):
        self._client = client
        self._ttl = timedelta(minutes=config.MCP_CACHE_TTL_MINUTES if ttl_minutes is None else ttl_minutes)
        self._lock = threading.Lock()
        self._store: Dict[str, Dict[str, Any]] = {}  # 清單名稱 -> {"videos": [...], "fetched_at": datetime}

    @property
    def client(self) -> YouTubeClient:
        # 延遲建立：匯入模組（例如跑測試）時不觸發 API Key 檢查
        if self._client is None:
            self._client = YouTubeClient.for_public_data()
        return self._client

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
    keyword = keyword.strip()
    if len(keyword) < MIN_KEYWORD_LENGTH:
        return {"error": f"關鍵字需至少 {MIN_KEYWORD_LENGTH} 個字元"}
    limit = max(1, min(int(limit), 500))

    target_names = [playlist] if playlist else list(playlists.load_all())
    lowered = keyword.lower()
    results: List[Dict[str, Any]] = []
    total_matches = 0

    for name in target_names:
        for position, video in enumerate(cache.get_videos(name), start=1):
            if lowered in video["title"].lower() or lowered in video["channel"].lower():
                total_matches += 1
                if len(results) < limit:
                    results.append(
                        {
                            "playlist": name,
                            "position": position,
                            "title": video["title"],
                            "channel": video["channel"],
                            "views": video["views"],
                            "url": video["url"],
                        }
                    )

    return {
        "keyword": keyword,
        "searched_playlists": target_names,
        "total_matches": total_matches,
        "returned": len(results),
        "results": results,
    }


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
def list_playlists() -> Dict[str, Any]:
    """列出所有可搜尋的播放清單，以及各清單的快取狀態（歌曲數、上次載入時間）。"""
    return playlists_overview()


@mcp.tool()
def refresh_playlist(playlist: str = "") -> Dict[str, Any]:
    """強制重新抓取清單內容（清單有新增歌曲時使用）。留空＝重抓所有已快取的清單。"""
    return perform_refresh(playlist)


# ── REST 路由（非 AI 服務用；抓取可能阻塞，丟到 worker thread）─────


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
    limit = request.query_params.get("limit", str(DEFAULT_RESULT_LIMIT))
    try:
        result = await to_thread.run_sync(partial(perform_search, keyword, playlist, int(limit)))
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    status = 400 if "error" in result else 200
    return JSONResponse(result, status_code=status)


@mcp.custom_route("/refresh", methods=["GET", "POST"])
async def rest_refresh(request: Request) -> JSONResponse:
    playlist = request.query_params.get("playlist", "")
    try:
        result = await to_thread.run_sync(partial(perform_refresh, playlist))
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse(result)


def main() -> None:
    logger.info(
        f"yt-mcp 啟動：MCP=http://{config.MCP_HOST}:{config.MCP_PORT}/mcp，"
        f"REST=/health /playlists /search /refresh，快取 TTL {config.MCP_CACHE_TTL_MINUTES} 分鐘"
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
