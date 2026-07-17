"""mcp_server 測試：快取行為與搜尋邏輯（假 client，無網路、無伺服器）。"""

import unittest

from youtube_toolkit.mcp_server import SongCache, perform_refresh, perform_search, playlists_overview


def video(title, channel="頻道", views=10):
    return {"video_id": "x", "title": title, "channel": channel, "views": views, "url": "https://youtu.be/x"}


class FakeClient:
    """回傳預先設定的清單內容，並記錄抓取次數。"""

    def __init__(self, videos_by_playlist_id):
        self._videos = videos_by_playlist_id
        self.fetch_count = 0

    def fetch_playlist_videos(self, playlist_id):
        self.fetch_count += 1
        return list(self._videos.get(playlist_id, []))


class TestSongCache(unittest.TestCase):
    LIVE_ID = "PLLUffVVIYEV_8vV5tNnViOQaNhDrrbcr9"  # playlists.toml 裡的「Live」

    def test_second_read_hits_cache_without_refetch(self):
        client = FakeClient({self.LIVE_ID: [video("歌")]})
        cache = SongCache(client=client, ttl_minutes=60)

        cache.get_videos("Live")
        cache.get_videos("Live")

        self.assertEqual(client.fetch_count, 1)  # 快取核心價值：TTL 內只抓一次

    def test_expired_ttl_triggers_refetch(self):
        client = FakeClient({self.LIVE_ID: [video("歌")]})
        cache = SongCache(client=client, ttl_minutes=0)  # TTL=0 → 每次都過期

        cache.get_videos("Live")
        cache.get_videos("Live")

        self.assertEqual(client.fetch_count, 2)

    def test_force_refresh_bypasses_ttl(self):
        client = FakeClient({self.LIVE_ID: [video("歌")]})
        cache = SongCache(client=client, ttl_minutes=60)

        cache.get_videos("Live")
        cache.get_videos("Live", force=True)

        self.assertEqual(client.fetch_count, 2)

    def test_unknown_playlist_name_raises_with_hint(self):
        cache = SongCache(client=FakeClient({}), ttl_minutes=60)
        with self.assertRaises(KeyError) as ctx:
            cache.get_videos("不存在的清單")
        self.assertIn("可用名稱", str(ctx.exception))


class TestPerformSearch(unittest.TestCase):
    LIVE_ID = "PLLUffVVIYEV_8vV5tNnViOQaNhDrrbcr9"

    def _cache(self):
        client = FakeClient(
            {
                self.LIVE_ID: [
                    video("Monsters (Cover)", channel="頻道A", views=100),
                    video("另一首歌", channel="Monsters樂團", views=50),
                    video("無關的歌", channel="頻道B", views=10),
                ]
            }
        )
        return SongCache(client=client, ttl_minutes=60)

    def test_matches_title_and_channel_case_insensitive(self):
        result = perform_search("monsters", playlist="Live", cache=self._cache())
        self.assertEqual(result["total_matches"], 2)  # 歌名命中 1 + 頻道命中 1
        self.assertEqual(result["returned"], 2)
        self.assertEqual(result["results"][0]["position"], 1)

    def test_keyword_too_short_returns_error(self):
        result = perform_search("M", playlist="Live", cache=self._cache())
        self.assertIn("error", result)

    def test_limit_caps_results_but_total_is_accurate(self):
        result = perform_search("monsters", playlist="Live", limit=1, cache=self._cache())
        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["total_matches"], 2)  # 歌名命中 1 + 頻道命中 1，limit 只砍回傳不砍統計

    def test_search_results_carry_url_for_agents(self):
        result = perform_search("Monsters", playlist="Live", cache=self._cache())
        self.assertTrue(all(r["url"].startswith("https://youtu.be/") for r in result["results"]))


class TestOverviewAndRefresh(unittest.TestCase):
    LIVE_ID = "PLLUffVVIYEV_8vV5tNnViOQaNhDrrbcr9"

    def test_overview_lists_all_configured_playlists(self):
        cache = SongCache(client=FakeClient({self.LIVE_ID: [video("歌")]}), ttl_minutes=60)
        cache.get_videos("Live")

        overview = playlists_overview(cache=cache)
        by_name = {p["name"]: p for p in overview["playlists"]}

        self.assertEqual(len(by_name), 13)  # playlists.toml 全部清單都要列出
        self.assertTrue(by_name["Live"]["cached"])
        self.assertEqual(by_name["Live"]["songs"], 1)
        self.assertFalse(by_name["YTMusic"]["cached"])  # 未查詢過的清單不會被動載入

    def test_refresh_all_only_touches_cached_playlists(self):
        client = FakeClient({self.LIVE_ID: [video("歌")]})
        cache = SongCache(client=client, ttl_minutes=60)
        cache.get_videos("Live")

        result = perform_refresh(cache=cache)

        self.assertEqual(result["refreshed"], {"Live": 1})
        self.assertEqual(client.fetch_count, 2)  # 初載 + 重抓，不會去抓其他 12 份清單


if __name__ == "__main__":
    unittest.main()
