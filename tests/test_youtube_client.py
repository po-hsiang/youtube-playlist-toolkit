"""youtube_client 模組單元測試：以假 service 驗證分頁、防呆、配額記帳與讀取重試。

執行：uv run python -m unittest discover -s tests -v
"""

import json
import unittest
from unittest import mock

import httplib2
from googleapiclient.errors import HttpError

from youtube_toolkit.quota_manager import QuotaManager, QuotaSoftLimitExceeded
from youtube_toolkit.youtube_client import MAX_READ_ATTEMPTS, QuotaCost, YouTubeClient


def http_error(status, reason="backendError"):
    resp = httplib2.Response({"status": status})
    content = json.dumps(
        {"error": {"code": status, "message": reason, "errors": [{"reason": reason}]}}
    ).encode()
    return HttpError(resp, content)


class FakeRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class FakePlaylistItems:
    """兩頁分頁回應；update / delete 呼叫會被記錄下來供斷言。"""

    def __init__(self, pages, update_calls, delete_calls):
        self._pages = pages
        self.update_calls = update_calls
        self.delete_calls = delete_calls

    def list(self, **kwargs):
        page_index = int(kwargs.get("pageToken") or 0)
        return FakeRequest(self._pages[page_index])

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return FakeRequest({})

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return FakeRequest({})


class FakeVideos:
    def __init__(self, details_by_id, list_calls, chart_items=None):
        self._details_by_id = details_by_id
        self._chart_items = chart_items or []
        self.list_calls = list_calls

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        if "chart" in kwargs:  # 發燒榜：不是依 id 查，直接回榜單
            return FakeRequest({"items": self._chart_items})
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
    def __init__(self, pages=None, details_by_id=None, chart_items=None):
        self.update_calls = []
        self.delete_calls = []
        self.videos_list_calls = []
        self.search_calls = []
        self._playlist_items = FakePlaylistItems(pages or [], self.update_calls, self.delete_calls)
        self._videos = FakeVideos(details_by_id or {}, self.videos_list_calls, chart_items)
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


class TestFetchMostPopular(unittest.TestCase):
    def setUp(self):
        self.service = FakeService(chart_items=[{"id": "hot1"}, {"id": "hot2"}])
        self.quota = QuotaManager(daily_limit=100, soft_limit=80)
        self.client = YouTubeClient(self.service, self.quota)

    def test_sends_chart_and_region(self):
        self.client.fetch_most_popular("TW", 3)
        params = self.service.videos_list_calls[0]

        self.assertEqual(params["chart"], "mostPopular")
        self.assertEqual(params["regionCode"], "TW")
        self.assertEqual(params["maxResults"], 3)
        self.assertNotIn("videoCategoryId", params)  # 沒指定類別就不要送這個參數

    def test_category_id_is_forwarded(self):
        self.client.fetch_most_popular("TW", 3, "10")
        self.assertEqual(self.service.videos_list_calls[0]["videoCategoryId"], "10")

    def test_max_results_capped_at_api_page_limit(self):
        self.client.fetch_most_popular("TW", 999)
        self.assertEqual(self.service.videos_list_calls[0]["maxResults"], 50)

    def test_costs_one_unit(self):
        self.client.fetch_most_popular("TW", 3)
        self.assertEqual(self.quota.used, QuotaCost.LIST)  # 整份榜單只要 1 unit

    def test_returns_raw_items_for_the_trending_module_to_shape(self):
        self.assertEqual(self.client.fetch_most_popular("TW", 3), [{"id": "hot1"}, {"id": "hot2"}])


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

    def test_delete_playlist_item_charges_50(self):
        service = FakeService()
        quota = QuotaManager(daily_limit=1000, soft_limit=800)
        client = YouTubeClient(service, quota)

        client.delete_playlist_item("pi-dead")

        self.assertEqual(quota.used, QuotaCost.DELETE)
        self.assertEqual(service.delete_calls, [{"id": "pi-dead"}])

    def test_delete_fuse_blocks_before_api_call(self):
        service = FakeService()
        quota = QuotaManager(daily_limit=100, soft_limit=80, initial_used=50)
        client = YouTubeClient(service, quota)

        with self.assertRaises(QuotaSoftLimitExceeded):
            client.delete_playlist_item("pi-dead")

        self.assertEqual(service.delete_calls, [])


class FlakyRequest:
    """execute() 依序拋出注入的例外，用完後回傳正常結果。"""

    def __init__(self, failures, response):
        self._failures = list(failures)
        self._response = response
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return self._response


@mock.patch("youtube_toolkit.youtube_client.time.sleep")  # 測試不真的等待
class TestReadRetry(unittest.TestCase):
    def _client(self):
        quota = QuotaManager(daily_limit=100, soft_limit=80)
        return YouTubeClient(FakeService(), quota), quota

    def test_transient_errors_retried_and_each_attempt_charged(self, _sleep):
        client, quota = self._client()
        request = FlakyRequest([http_error(500), http_error(503)], {"ok": True})

        result = client._execute_with_retry(request, QuotaCost.LIST, "flaky-read")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.calls, 3)
        self.assertEqual(quota.used, 3)  # 失敗的請求同樣消耗配額，逐次記帳

    def test_non_retryable_error_raises_immediately(self, _sleep):
        client, quota = self._client()
        request = FlakyRequest([http_error(404, "notFound")], {"ok": True})

        with self.assertRaises(HttpError):
            client._execute_with_retry(request, QuotaCost.LIST, "not-found")

        self.assertEqual(request.calls, 1)  # 404 不重試

    def test_gives_up_after_max_attempts(self, _sleep):
        client, _ = self._client()
        request = FlakyRequest([http_error(500)] * MAX_READ_ATTEMPTS, {"ok": True})

        with self.assertRaises(HttpError):
            client._execute_with_retry(request, QuotaCost.LIST, "always-500")

        self.assertEqual(request.calls, MAX_READ_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
