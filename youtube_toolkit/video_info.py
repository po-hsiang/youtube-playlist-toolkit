"""單一影片中繼資料的驗證與整形（給下游 Discord 機器人做時長預檢／直播婉拒／embed）。

純函式、不碰網路——原始 API item 由呼叫端注入，與 trending／song_search 相同分工。
輸出欄位名是跨服務契約（下游機器人依名取值），**不可改名**。
"""

import re
from typing import Any, Dict, Optional

from youtube_toolkit.trending import duration_seconds

# YouTube 影片 ID 固定 11 碼：字母、數字、- 與 _
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# snippet.thumbnails 各解析度的優先序（高→低）
_THUMBNAIL_PRIORITY = ("maxres", "standard", "high", "medium", "default")

INVALID_VIDEO_ID = "INVALID_VIDEO_ID"
VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"


def is_valid_video_id(video_id: str) -> bool:
    return bool(_VIDEO_ID_RE.match(video_id or ""))


def best_thumbnail_url(thumbnails: Dict[str, Any]) -> Optional[str]:
    """取解析度最高的可用縮圖；非標準鍵名則退回以寬度挑選。"""
    for key in _THUMBNAIL_PRIORITY:
        entry = thumbnails.get(key)
        if entry and entry.get("url"):
            return entry["url"]
    with_url = [t for t in thumbnails.values() if isinstance(t, dict) and t.get("url")]
    if not with_url:
        return None
    return max(with_url, key=lambda t: t.get("width", 0))["url"]


def to_video_info(item: Dict[str, Any]) -> Dict[str, Any]:
    """原始 videos.list item → 契約格式。

    is_live 判定：liveBroadcastContent 為 "live"，或時長為 0 秒——
    進行中直播的 contentDetails.duration 可能是 P0D。
    """
    snippet = item.get("snippet", {})
    duration = item.get("contentDetails", {}).get("duration", "")
    seconds = duration_seconds(duration)
    return {
        "video_id": item["id"],
        "title": snippet.get("title", "N/A"),
        "channel": snippet.get("channelTitle", "N/A"),
        "published_at": snippet.get("publishedAt", ""),
        "duration": duration,
        "duration_seconds": seconds,
        "is_live": snippet.get("liveBroadcastContent") == "live" or seconds == 0,
        "views": int(item.get("statistics", {}).get("viewCount", 0)),
        "thumbnail_url": best_thumbnail_url(snippet.get("thumbnails", {})),
        "url": f"https://www.youtube.com/watch?v={item['id']}",
    }
