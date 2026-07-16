"""youtube_client 模組單元測試：以假 service 驗證分頁、防呆與配額記帳。

執行：uv run python -m unittest discover -s tests -v
"""

import unittest

from youtube_toolkit.quota_manager import QuotaManager, QuotaSoftLimitExceeded
from youtube_toolkit.youtube_client import QuotaCost, YouTubeClient


class FakeRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class FakePlaylistItems:
    """兩頁分頁回應；update 呼叫會被記錄下來供斷言。"""

    def __init__(self, pages, update_calls):
        self._pages = pages
        self.update_calls = update_calls

    def list(self, **kwargs):
        page_index = int(kwargs.get("pageToken") or 0)
        return FakeRequest(self._pages[page_index])

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return FakeRequest({})


class FakeVideos:
    def __init__(self, details_by_id, list_calls):
        self._details_by_id = details_by_id
        self.list_calls = list_calls

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        requested_ids = kwargs["id"].split(",")
        items = [self._details_by_id[vid] for vid in requested_ids if vid in self._details_by_id]
        return FakeRequest({"items": items})


class FakeSearch:
    def __init__(self, search_calls):
        self.search_calls = search_calls

    def list(self, **kwargs):
        self.search_calls.append(kwargs)
        return FakeRequest({"items": []})


class FakeService:
    def __init__(self, pages=None, details_by_id=None):
        self.update_calls = []
        self.videos_list_calls = []
        self.search_calls = []
        self._playlist_items = FakePlaylistItems(pages or [], self.update_calls)
        self._videos = FakeVideos(details_by_id or {}, self.videos_list_calls)
        self._search = FakeSearch(self.search_calls)

    def playlistItems(self):
        return self._playlist_items

    def videos(self):
        return self._videos

    def search(self):
        return self._search


def playlist_item(item_id, video_id, title="song"):
    return {
        "id": item_id,
        "snippet": {"title": title, "resourceId": {"kind": "youtube#video", "videoId": video_id}},
    }


def video_detail(video_id, title="song", channel="channel", views=100, with_statistics=True):
    item = {"id": video_id, "snippet": {"title": title, "channelTitle": channel}}
    if with_statistics:
        item["statistics"] = {"viewCount": str(views)}
    return item


class TestFetchPlaylistEntries(unittest.TestCase):
    def setUp(self):
        pages = [
            {
                "items": [
                    playlist_item("pi-1", "v1"),
                    # 非影片項目（例如頻道）應被過濾
                    {"id": "pi-x", "snippet": {"title": "not a video", "resourceId": {"kind": "youtube#channel"}}},
                ],
                "nextPageToken": "1",
            },
            {"items": [playlist_item("pi-2", "v2")]},
        ]
        self.service = FakeService(pages=pages)
        self.quota = QuotaManager(daily_limit=100, soft_limit=80)
        self.client = YouTubeClient(self.service, self.quota)

    def test_paginates_and_filters_non_videos(self):
        entries = self.client.fetch_playlist_entries("PL-test")
        self.assertEqual(
            [(e["playlist_item_id"], e["video_id"]) for e in entries],
            [("pi-1", "v1"), ("pi-2", "v2")],
        )

    def test_quota_charged_per_page(self):
        self.client.fetch_playlist_entries("PL-test")
        self.assertEqual(self.quota.used, 2 * QuotaCost.LIST)


class TestFetchVideoDetails(unittest.TestCase):
    def test_skips_videos_without_statistics(self):
        # v2 是私人／已下架影片：沒有 statistics，必須被跳過而不是 KeyError（重構前的實際 bug）
        service = FakeService(
            details_by_id={
                "v1": video_detail("v1", views=42),
                "v2": video_detail("v2", with_statistics=False),
            }
        )
        client = YouTubeClient(service, QuotaManager(daily_limit=100, soft_limit=80))
        details = client.fetch_video_details(["v1", "v2"])
        self.assertEqual(details["v1"]["views"], 42)
        self.assertNotIn("v2", details)

    def test_batches_of_fifty(self):
        video_ids = [f"v{i}" for i in range(120)]
        service = FakeService(details_by_id={vid: video_detail(vid) for vid in video_ids})
        quota = QuotaManager(daily_limit=100, soft_limit=80)
        client = YouTubeClient(service, quota)

        details = client.fetch_video_details(video_ids)

        self.assertEqual(len(details), 120)
        self.assertEqual(len(service.videos_list_calls), 3)  # 50 + 50 + 20
        self.assertEqual(quota.used, 3 * QuotaCost.LIST)


class TestFetchPlaylistVideos(unittest.TestCase):
    def test_merges_entries_with_details_and_skips_missing(self):
        pages = [{"items": [playlist_item("pi-1", "v1"), playlist_item("pi-2", "v2", title="私人影片")]}]
        service = FakeService(pages=pages, details_by_id={"v1": video_detail("v1", title="歌", views=7)})
        client = YouTubeClient(service, QuotaManager(daily_limit=100, soft_limit=80))

        videos = client.fetch_playlist_videos("PL-test")

        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["url"], "https://youtu.be/v1")
        self.assertEqual(videos[0]["views"], 7)


class TestQuotaAccounting(unittest.TestCase):
    def test_move_playlist_item_charges_50_and_builds_body(self):
        service = FakeService()
        quota = QuotaManager(daily_limit=100, soft_limit=80)
        client = YouTubeClient(service, quota)

        client.move_playlist_item("pi-1", "PL-test", "v1", position=3)

        self.assertEqual(quota.used, QuotaCost.UPDATE)
        body = service.update_calls[0]["body"]
        self.assertEqual(body["id"], "pi-1")
        self.assertEqual(body["snippet"]["position"], 3)
        self.assertEqual(body["snippet"]["resourceId"]["videoId"], "v1")

    def test_search_charges_100_and_passes_params(self):
        service = FakeService()
        quota = QuotaManager(daily_limit=1000, soft_limit=800)
        client = YouTubeClient(service, quota)

        client.search_videos(part="snippet", q="關鍵字", maxResults=5)

        self.assertEqual(quota.used, QuotaCost.SEARCH)
        self.assertEqual(service.search_calls[0]["q"], "關鍵字")

    def test_soft_limit_fuse_blocks_before_api_call(self):
        service = FakeService()
        quota = QuotaManager(daily_limit=100, soft_limit=80, initial_used=50)
        client = YouTubeClient(service, quota)

        with self.assertRaises(QuotaSoftLimitExceeded):
            client.move_playlist_item("pi-1", "PL-test", "v1", position=0)

        self.assertEqual(service.update_calls, [])  # 熔斷必須發生在 API 呼叫「之前」
        self.assertEqual(quota.used, 50)  # 且不得多扣


if __name__ == "__main__":
    unittest.main()
