"""歌曲搜尋／隨機抽選核心：MCP 伺服器與 CLI 共用同一套比對語意。

純函式、不碰網路——歌曲來源由呼叫端以 get_videos 注入
（伺服器傳入記憶體快取，CLI 退回模式傳入直接打 API 的取用器）。
"""

import random
from typing import Any, Callable, Dict, List, Optional, Sequence

MIN_KEYWORD_LENGTH = 2
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
DEFAULT_RANDOM_COUNT = 1
MAX_RANDOM_COUNT = 10


def is_match(video: Dict[str, Any], lowered_keyword: str) -> bool:
    """比對歌名與頻道名稱，不分大小寫。"""
    return lowered_keyword in video["title"].lower() or lowered_keyword in video["channel"].lower()


def as_song(playlist: str, position: int, video: Dict[str, Any]) -> Dict[str, Any]:
    """統一的單曲輸出格式（搜尋結果與隨機抽歌共用，客戶端只需認得一種形狀）。"""
    return {
        "playlist": playlist,
        "position": position,
        "title": video["title"],
        "channel": video["channel"],
        "views": video["views"],
        "url": video["url"],
    }


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
                results.append(as_song(name, position, video))

    return {
        "keyword": keyword,
        "searched_playlists": list(playlist_names),
        "total_matches": total_matches,
        "returned": len(results),
        "results": results,
    }


def pick_random_songs(
    playlist_names: Sequence[str],
    get_videos: Callable[[str], List[Dict[str, Any]]],
    count: int = DEFAULT_RANDOM_COUNT,
    keyword: str = "",
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """從清單中隨機抽出 count 首**不重複**的歌。

    keyword 留空＝整份清單都是候選；有給則只從命中的歌曲中抽（比對語意與 search 完全一致）。
    候選數不足 count 時回傳現有的全部（不補齊、不重複）；完全沒有候選時 songs 為空陣列，
    不視為錯誤——呼叫端只要檢查 songs 是否為空即可。

    rng 可注入以取得可重現的結果（測試用）。
    """
    keyword = keyword.strip()
    if keyword and len(keyword) < MIN_KEYWORD_LENGTH:
        return {"error": f"關鍵字需至少 {MIN_KEYWORD_LENGTH} 個字元"}
    count = max(1, min(int(count), MAX_RANDOM_COUNT))

    lowered = keyword.lower()
    candidates = [
        as_song(name, position, video)
        for name in playlist_names
        for position, video in enumerate(get_videos(name), start=1)
        if not keyword or is_match(video, lowered)
    ]

    picker = rng or random.Random()
    songs = picker.sample(candidates, min(count, len(candidates)))

    return {
        "playlists": list(playlist_names),
        "keyword": keyword,
        "candidates": len(candidates),
        "returned": len(songs),
        "songs": songs,
    }
