"""歌曲搜尋核心：MCP 伺服器與 CLI 共用同一套比對語意。

純函式、不碰網路——歌曲來源由呼叫端以 get_videos 注入
（伺服器傳入記憶體快取，CLI 退回模式傳入直接打 API 的取用器）。
"""

from typing import Any, Callable, Dict, List, Sequence

MIN_KEYWORD_LENGTH = 2
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def is_match(video: Dict[str, Any], lowered_keyword: str) -> bool:
    """比對歌名與頻道名稱，不分大小寫。"""
    return lowered_keyword in video["title"].lower() or lowered_keyword in video["channel"].lower()


def search_playlists(
    keyword: str,
    playlist_names: Sequence[str],
    get_videos: Callable[[str], List[Dict[str, Any]]],
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """在多份清單中搜尋關鍵字，回傳統一格式的結果。

    keyword 太短時回傳 {"error": ...}。limit 只限制回傳筆數，
    total_matches 仍是完整命中數（讓使用者知道還有多少沒顯示）。
    """
    keyword = keyword.strip()
    if len(keyword) < MIN_KEYWORD_LENGTH:
        return {"error": f"關鍵字需至少 {MIN_KEYWORD_LENGTH} 個字元"}
    limit = max(1, min(int(limit), MAX_LIMIT))

    lowered = keyword.lower()
    results: List[Dict[str, Any]] = []
    total_matches = 0

    for name in playlist_names:
        for position, video in enumerate(get_videos(name), start=1):
            if not is_match(video, lowered):
                continue
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
        "searched_playlists": list(playlist_names),
        "total_matches": total_matches,
        "returned": len(results),
        "results": results,
    }
