"""發燒影片榜（YouTube 官方 chart=mostPopular）的類別對照與資料整形。

純函式、不碰網路——原始 API items 由呼叫端注入，與 song_search 相同的分工：
youtube_client 負責取得資料，本模組負責解讀與整形。

**這裡處理的是 YouTube 公開榜單，與使用者自己的播放清單無關。**
"""

import re
from typing import Any, Dict, List, Sequence

DEFAULT_LIMIT = 3
MAX_LIMIT = 50  # videos.list 單頁上限；榜單前 50 名以外意義不大，不做分頁

# 友善名稱 → YouTube videoCategoryId（空字串＝不過濾）。
# 警告：ID 合法不代表該地區有榜——實測 TW 的 29（非營利）回 404。
CATEGORIES = {
    "all": "",
    "music": "10",
    "gaming": "20",
    "film": "1",
    "sports": "17",
    "comedy": "23",
    "entertainment": "24",
    "news": "25",
    "tech": "28",
}

# ISO 8601 時長，例：PT3M39S、PT9H29M49S、P0D（進行中的直播）
_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def resolve_category(name: str) -> str:
    """友善名稱 → videoCategoryId；也接受直接給數字 ID（對照表以外的類別）。"""
    name = (name or "all").strip().lower()
    if name in CATEGORIES:
        return CATEGORIES[name]
    if name.isdigit():
        return name
    raise ValueError(
        f"未知的類別「{name}」。可用：{'、'.join(CATEGORIES)}（或直接給 videoCategoryId 數字）"
    )


def duration_seconds(iso_duration: str) -> int:
    """ISO 8601 時長 → 秒數；無法解析或進行中的直播（P0D）回 0。"""
    match = _ISO_DURATION.match(iso_duration or "")
    if not match:
        return 0
    part = {key: int(value) for key, value in match.groupdict(default="0").items()}
    return part["days"] * 86400 + part["hours"] * 3600 + part["minutes"] * 60 + part["seconds"]


def format_duration(total_seconds: int) -> str:
    """秒數 → 人看的時長（3:39 / 9:29:49）；0 秒回空字串（直播中或無時長）。"""
    if total_seconds <= 0:
        return ""
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def to_trending_videos(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """原始 API items → 榜單格式（含名次；API 回傳順序即名次）。"""
    videos: List[Dict[str, Any]] = []
    for rank, item in enumerate(items, start=1):
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        seconds = duration_seconds(item.get("contentDetails", {}).get("duration", ""))
        likes = statistics.get("likeCount")  # 頻道可隱藏讚數，此時欄位不存在
        videos.append(
            {
                "rank": rank,
                "video_id": item["id"],
                "title": snippet.get("title", "N/A"),
                "channel": snippet.get("channelTitle", "N/A"),
                "views": int(statistics.get("viewCount", 0)),
                "likes": int(likes) if likes is not None else None,
                "published_at": snippet.get("publishedAt", "")[:10],
                "duration": format_duration(seconds),
                "duration_seconds": seconds,  # 讓客戶端能濾掉數小時的實況存檔
                "is_live": snippet.get("liveBroadcastContent") == "live",
                "category_id": snippet.get("categoryId", ""),
                "url": f"https://youtu.be/{item['id']}",
            }
        )
    return videos


def build_result(region: str, category: str, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    videos = to_trending_videos(items)
    return {
        "source": "YouTube 發燒影片（官方公開榜單，非使用者的播放清單）",
        "region": region,
        "category": category,
        "returned": len(videos),
        "videos": videos,
    }
