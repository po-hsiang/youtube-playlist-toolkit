"""影片關鍵字搜尋工具：呼叫 search.list REST 端點（注意：每次 100 units）。

執行方式：python -m youtube_toolkit.video_search
"""

from datetime import datetime, timedelta
import requests

from youtube_toolkit import config


class YouTubeSearchHandler:
    def __init__(self):
        self.API_KEY = config.require_api_key()
        self.API_URL = "https://www.googleapis.com/youtube/v3/search"

    def search_by_keyword(self, keyword, results_count=50, search_order="relevance"):
        search_params = self._build_search_params(keyword, results_count, search_order)
        search_results = self._get_data_by_api(search_params)
        return search_results

    def _get_data_by_api(self, params):
        response = requests.get(self.API_URL, params=params)
        return response.json()

    def _build_search_params(self, keyword, results_count, search_order):
        # search_order: relevance, date, rating, title, viewCount
        published_after, published_before = self._get_time_period_for_youtube(180)
        return {
            "key": self.API_KEY,
            "q": keyword,
            "part": "snippet",
            "maxResults": results_count,
            "type": "video",
            "order": search_order,
            "publishedAfter": published_after,
            "publishedBefore": published_before,
            "safeSearch": "strict",
        }

    def _get_time_period_for_youtube(self, days_ago):
        current_time = datetime.utcnow()
        n_days_ago = current_time - timedelta(days=days_ago)
        published_after = n_days_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
        published_before = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        return published_after, published_before


def main():
    youtube_search = YouTubeSearchHandler()
    result = youtube_search.search_by_keyword("羅傑")
    print(f"result: {result}\n")


if __name__ == "__main__":
    main()
