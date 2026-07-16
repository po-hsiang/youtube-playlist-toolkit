"""影片關鍵字搜尋工具：search.list 包裝（注意：每次呼叫 100 units）。

改用與其他工具相同的 googleapiclient（底層內建逾時與 HttpError 錯誤處理），
並經 QuotaManager 記帳。執行方式：python -m youtube_toolkit.video_search
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from youtube_toolkit.youtube_client import YouTubeClient

SEARCH_WINDOW_DAYS = 180  # 只搜尋近 N 天內發布的影片
YOUTUBE_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class YouTubeSearchHandler:
    def __init__(self, client: Optional[YouTubeClient] = None):
        self.client = client or YouTubeClient.for_public_data()

    def search_by_keyword(
        self, keyword: str, results_count: int = 50, search_order: str = "relevance"
    ) -> Dict[str, Any]:
        """搜尋近 SEARCH_WINDOW_DAYS 天內的影片。

        search_order 可用：relevance / date / rating / title / viewCount。
        """
        published_after, published_before = _time_window(days=SEARCH_WINDOW_DAYS)
        return self.client.search_videos(
            part="snippet",
            q=keyword,
            maxResults=results_count,
            type="video",
            order=search_order,
            publishedAfter=published_after,
            publishedBefore=published_before,
            safeSearch="strict",
        )


def _time_window(days: int) -> Tuple[str, str]:
    """回傳 (published_after, published_before)，YouTube API 要求的 UTC RFC3339 格式。"""
    now = datetime.now(timezone.utc)
    n_days_ago = now - timedelta(days=days)
    return n_days_ago.strftime(YOUTUBE_TIME_FORMAT), now.strftime(YOUTUBE_TIME_FORMAT)


def main() -> None:
    youtube_search = YouTubeSearchHandler()
    result = youtube_search.search_by_keyword("羅傑")
    print(f"result: {result}\n")


if __name__ == "__main__":
    main()
