"""playlist_search CLI 測試：快取優先、連不上時退回 API、輸入錯誤不浪費配額。"""

import unittest
from unittest import mock

import requests

from youtube_toolkit.playlist_search import search_via_api, search_via_server


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, videos):
        self._videos = videos
        self.fetch_count = 0

    def fetch_playlist_videos(self, playlist_id):
        self.fetch_count += 1
        return list(self._videos)


def video(title, channel="頻道", views=10):
    return {"video_id": "x", "title": title, "channel": channel, "views": views, "url": "https://youtu.be/x"}


HEALTHY = FakeResponse(200, {"status": "ok"})  # /health 的回應（探活用）


class TestSearchViaServer(unittest.TestCase):
    def test_returns_payload_when_server_responds(self):
        payload = {"keyword": "abc", "total_matches": 1, "returned": 1, "results": [], "searched_playlists": []}
        with mock.patch(
            "youtube_toolkit.playlist_search.requests.get", side_effect=[HEALTHY, FakeResponse(200, payload)]
        ):
            self.assertEqual(search_via_server("abc", "Live"), payload)

    def test_passes_query_parameters(self):
        with mock.patch(
            "youtube_toolkit.playlist_search.requests.get", side_effect=[HEALTHY, FakeResponse(200, {})]
        ) as fake_get:
            search_via_server("abc", "Live", limit=7)
        self.assertEqual(fake_get.call_args.kwargs["params"], {"q": "abc", "playlist": "Live", "limit": 7})

    def test_search_gets_generous_timeout_health_gets_short_one(self):
        # 冷快取時伺服器要載入整份清單；查詢逾時太短會退回 API，導致兩邊各抓一次
        with mock.patch(
            "youtube_toolkit.playlist_search.requests.get", side_effect=[HEALTHY, FakeResponse(200, {})]
        ) as fake_get:
            search_via_server("abc", "Live")
        health_timeout = fake_get.call_args_list[0].kwargs["timeout"]
        search_timeout = fake_get.call_args_list[1].kwargs["timeout"]
        self.assertLessEqual(health_timeout, 2)
        self.assertGreaterEqual(search_timeout, 60)

    def test_returns_none_when_server_unreachable(self):
        # 伺服器沒開 → 探活立刻失敗 → 回 None，讓呼叫端安靜地退回直接呼叫 API
        with mock.patch(
            "youtube_toolkit.playlist_search.requests.get", side_effect=requests.ConnectionError("refused")
        ):
            self.assertIsNone(search_via_server("abc", "Live"))

    def test_returns_none_on_server_side_error(self):
        with mock.patch(
            "youtube_toolkit.playlist_search.requests.get", side_effect=[HEALTHY, FakeResponse(500)]
        ):
            self.assertIsNone(search_via_server("abc", "Live"))

    def test_unknown_playlist_raises_instead_of_falling_back(self):
        # 清單名稱打錯是使用者輸入問題；退回 API 重跑只會得到同樣錯誤又白燒配額
        response = FakeResponse(404, {"error": "找不到播放清單「打錯字」。可用名稱：Live、YTMusic"})
        with mock.patch(
            "youtube_toolkit.playlist_search.requests.get", side_effect=[HEALTHY, response]
        ):
            with self.assertRaises(KeyError) as ctx:
                search_via_server("abc", "打錯字")
        self.assertIn("可用名稱", str(ctx.exception))


class TestSearchViaApi(unittest.TestCase):
    def test_returns_same_shape_as_server(self):
        client = FakeClient([video("Monsters"), video("其他歌", channel="Monsters樂團"), video("無關")])

        result = search_via_api("monsters", playlist="Live", client=client)

        self.assertEqual(result["keyword"], "monsters")
        self.assertEqual(result["searched_playlists"], ["Live"])
        self.assertEqual(result["total_matches"], 2)  # 歌名命中 1 + 頻道命中 1
        self.assertEqual(result["results"][0]["playlist"], "Live")
        self.assertEqual(client.fetch_count, 1)

    def test_short_keyword_returns_error_without_fetching(self):
        client = FakeClient([video("Monsters")])
        result = search_via_api("M", playlist="Live", client=client)
        self.assertIn("error", result)
        self.assertEqual(client.fetch_count, 0)  # 連抓都不該抓，省配額

    def test_empty_playlist_searches_every_configured_playlist(self):
        client = FakeClient([video("Monsters")])
        result = search_via_api("monsters", playlist="", client=client)
        self.assertEqual(len(result["searched_playlists"]), 13)
        self.assertEqual(client.fetch_count, 13)


if __name__ == "__main__":
    unittest.main()
