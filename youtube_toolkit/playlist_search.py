"""歌單關鍵字搜尋（CLI）。

預設先向本機的 yt-mcp 伺服器查詢——清單在伺服器記憶體快取中，因此搜尋是
毫秒級且**不消耗 YouTube API 配額**；伺服器沒開時自動退回直接呼叫 YouTube API
（要重新載入整份清單，YTMusic 約 42 units）。兩條路徑共用 song_search 的比對邏輯。

執行方式：
    uv run yt-playlist-search CAPPER              # 搜尋預設清單（playlists.toml 的 target）
    uv run yt-playlist-search CAPPER --all        # 搜尋全部清單
    uv run yt-playlist-search CAPPER -p Japanese  # 指定清單
    uv run yt-playlist-search CAPPER -n 10        # 最多顯示 10 筆
    uv run yt-playlist-search --dump              # 印出整份清單（不搜尋）
    uv run yt-playlist-search CAPPER --no-server  # 略過快取，直接呼叫 YouTube API
"""

import argparse
import logging
import sys
from typing import Any, Dict, List, Optional

import requests

from youtube_toolkit import config, playlists
from youtube_toolkit.log_utils import set_console_level
from youtube_toolkit.song_search import DEFAULT_LIMIT, MIN_KEYWORD_LENGTH, search_playlists
from youtube_toolkit.youtube_client import YouTubeClient

MAX_MESSAGE_LENGTH = 1900  # Discord 分段上限（為 2,000 字元上限預留空間）

# 探活要快（伺服器沒開就立刻退回 API）；查詢要慢（冷快取時伺服器需載入整份清單，
# 若這裡先逾時而退回 API，兩邊會各抓一次、配額付雙倍）
HEALTH_TIMEOUT_SECONDS = 1.5
SEARCH_TIMEOUT_SECONDS = 120.0


def _server_is_up(base_url: str) -> bool:
    try:
        return requests.get(f"{base_url}/health", timeout=HEALTH_TIMEOUT_SECONDS).ok
    except requests.RequestException:
        return False


def search_via_server(
    keyword: str, playlist: str = "", limit: int = DEFAULT_LIMIT, base_url: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """向本機 yt-mcp 查詢（0 配額）。連不上伺服器時回傳 None，由呼叫端退回 API。

    清單名稱錯誤（404）會拋 KeyError——那是使用者的輸入問題，
    退回 API 重跑一次只會得到同樣的錯誤，還白白消耗配額。
    """
    base = (base_url or config.MCP_BASE_URL).rstrip("/")
    if not _server_is_up(base):
        return None

    params = {"q": keyword, "playlist": playlist, "limit": limit}
    try:
        response = requests.get(f"{base}/search", params=params, timeout=SEARCH_TIMEOUT_SECONDS)
    except requests.RequestException:
        return None

    if response.status_code == 404:
        raise KeyError(response.json().get("error", "找不到指定的播放清單"))
    if not response.ok:
        return None
    return response.json()


def search_via_api(
    keyword: str, playlist: str = "", limit: int = DEFAULT_LIMIT, client: Optional[YouTubeClient] = None
) -> Dict[str, Any]:
    """直接呼叫 YouTube API 搜尋（會載入整份清單，消耗配額）。"""
    client = client or YouTubeClient.for_public_data()
    target_names = [playlist] if playlist else list(playlists.load_all())

    def get_videos(name: str) -> List[Dict[str, Any]]:
        return client.fetch_playlist_videos(playlists.get_playlist_id(name))

    return search_playlists(keyword, target_names, get_videos, limit)


def print_results(payload: Dict[str, Any], source: str) -> None:
    keyword = payload["keyword"]
    scope = "、".join(payload["searched_playlists"]) or "全部清單"
    total = payload["total_matches"]

    if total == 0:
        print(f"\n🔍「{keyword}」在 {scope} 中沒有找到符合的歌曲。（{source}）")
        return

    shown = payload["returned"]
    suffix = f"，顯示前 {shown} 筆" if shown < total else ""
    print(f"\n🔍「{keyword}」在 {scope} 共 {total} 首{suffix}　（{source}）\n")
    for index, item in enumerate(payload["results"], start=1):
        print(f"{index:>3}. 【{item['channel']}】{item['title']}")
        print(f"     {item['url']}　{item['views']:,} 觀看　{item['playlist']} 第 {item['position']} 首")


class YouTubeAPIHandler:
    """載入整份播放清單後提供本地搜尋，結果分段（≤1,900 字元）方便貼到 Discord。

    CLI 已改用上方的 search_via_server／search_via_api；本類別保留給
    需要「分段字串輸出」的呼叫端（例如聊天機器人）與 --dump 使用。
    """

    def __init__(
        self,
        client: Optional[YouTubeClient] = None,
        playlist_id: Optional[str] = None,
        verbose: bool = False,
    ):
        self.client = client or YouTubeClient.for_public_data()
        if playlist_id is None:
            name, playlist_id = playlists.tool_target("playlist_search")
            if verbose:
                print(f"目標清單：{name}（{playlist_id}）")
        self.song_list = self._load_song_list(playlist_id, verbose)

    def _load_song_list(self, playlist_id: str, verbose: bool = False) -> List[Dict[str, Any]]:
        videos = self.client.fetch_playlist_videos(playlist_id)
        videos.sort(key=lambda vid: vid["views"], reverse=True)  # 依觀看次數排序
        videos.sort(key=lambda vid: (vid["channel"], vid["title"]))  # 先依頻道再依影片標題排序

        if verbose:
            for index, video in enumerate(videos):
                print(
                    f"{index + 1} url: {video['url']}, views: {video['views']}, "
                    f"歌名: {video['title']}, 頻道: {video['channel']}"
                )
            print(f"total: {len(videos)}")
        return videos

    def search_keyword_in_song_list(self, keyword: str) -> List[str]:
        if len(keyword) < MIN_KEYWORD_LENGTH:
            return [f"搜尋請大於等於{MIN_KEYWORD_LENGTH}個字"]
        matched_songs = [song for song in self.song_list if self._is_keyword_matched(song, keyword)]
        answer = self._generate_song_list_response(matched_songs)
        return self._generate_search_result_message(keyword, len(matched_songs), answer)

    @staticmethod
    def _is_keyword_matched(song: Dict[str, Any], keyword: str) -> bool:
        keyword = keyword.lower()
        return keyword in song["title"].lower() or keyword in song["channel"].lower()

    @staticmethod
    def _generate_song_list_response(songs: List[Dict[str, Any]]) -> List[str]:
        """把符合的歌組成訊息，超過長度上限就切成下一段。"""
        result = ""
        answer: List[str] = []
        for index, song in enumerate(songs):
            current_song = f"{index + 1}.《{song['channel']}》{song['title']}\n"
            if len(result) + len(current_song) >= MAX_MESSAGE_LENGTH:
                answer.append(result)
                result = ""
            result += current_song
        if result:
            answer.append(result)
        return answer

    @staticmethod
    def _generate_search_result_message(keyword: str, count: int, answer: List[str]) -> List[str]:
        if answer:
            answer[0] = f"\n歌單內標題含有「{keyword}」的歌共有{count}首：\n" + answer[0]
        return answer


def _resolve_target(args: argparse.Namespace) -> str:
    """決定搜尋範圍：--all → 空字串（全部）；-p → 指定清單；否則用 playlists.toml 的預設。"""
    if args.all:
        return ""
    if args.playlist:
        return args.playlist
    name, _ = playlists.tool_target("playlist_search")
    return name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="在 YouTube 歌單中搜尋歌曲（優先使用 yt-mcp 快取，0 配額）"
    )
    parser.add_argument("keyword", nargs="?", help=f"搜尋關鍵字（至少 {MIN_KEYWORD_LENGTH} 個字元）")
    parser.add_argument("-p", "--playlist", default="", help="指定清單名稱（預設為 playlists.toml 的 target）")
    parser.add_argument("-a", "--all", action="store_true", help="搜尋全部清單")
    parser.add_argument("-n", "--limit", type=int, default=DEFAULT_LIMIT, help=f"最多顯示幾筆（預設 {DEFAULT_LIMIT}）")
    parser.add_argument("--dump", action="store_true", help="印出整份清單（不搜尋）")
    parser.add_argument("--no-server", action="store_true", help="略過 yt-mcp 快取，直接呼叫 YouTube API")
    parser.add_argument("-v", "--verbose", action="store_true", help="顯示逐筆配額等除錯訊息")
    args = parser.parse_args()

    set_console_level(logging.DEBUG if args.verbose else logging.INFO)

    try:
        target = _resolve_target(args)
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ 設定錯誤：{e}")
        sys.exit(1)

    if args.dump:
        playlist_id = playlists.get_playlist_id(target) if target else None
        YouTubeAPIHandler(playlist_id=playlist_id, verbose=True)
        return

    if not args.keyword:
        parser.error("請提供搜尋關鍵字，或用 --dump 印出整份清單")

    payload: Optional[Dict[str, Any]] = None
    source = ""
    try:
        if not args.no_server:
            payload = search_via_server(args.keyword, target, args.limit)
            if payload is not None:
                source = "yt-mcp 快取，0 units"
            else:
                print("（yt-mcp 伺服器未回應，改為直接呼叫 YouTube API）")

        if payload is None:
            if not target:
                print("（搜尋全部清單需載入 13 份清單，約 115 units）")
            payload = search_via_api(args.keyword, target, args.limit)
            source = "直接呼叫 API"
    except KeyError as e:
        print(f"❌ {e.args[0] if e.args else e}")
        sys.exit(1)

    if "error" in payload:
        print(f"❌ {payload['error']}")
        sys.exit(1)

    print_results(payload, source)


if __name__ == "__main__":
    main()
