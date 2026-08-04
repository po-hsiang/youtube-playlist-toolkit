"""mcp_server 測試：快取行為、搜尋與隨機抽歌邏輯（假 client，無網路、無伺服器）。"""

import asyncio
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starlette.requests import Request

from youtube_toolkit import audio_extract
from youtube_toolkit.mcp_server import (
    SongCache,
    perform_random,
    perform_refresh,
    perform_search,
    perform_transcript,
    perform_trending,
    perform_video_info,
    playlists_overview,
    random_target_names,
    rest_audio,
)
from youtube_toolkit.song_search import MAX_RANDOM_COUNT


def video(title, channel="頻道", views=10):
    return {
        "video_id": "x",
        "title": title,
        "channel": channel,
        "views": views,
        "url": f"https://youtu.be/{title}",
    }


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


class TestRandomTargetNames(unittest.TestCase):
    def test_empty_uses_configured_default_playlist(self):
        # 預設只鎖定一份清單，避免冷快取時把 13 份全載進來
        self.assertEqual(random_target_names(""), ["YTMusic"])

    def test_star_means_all_playlists(self):
        self.assertEqual(len(random_target_names("*")), 13)

    def test_explicit_name_is_used_as_is(self):
        self.assertEqual(random_target_names("Live"), ["Live"])


class TestPerformRandom(unittest.TestCase):
    YTMUSIC_ID = "PLLUffVVIYEV8J2P4Tp-rkEYZEtMHHkm7o"  # playlists.toml 裡的「YTMusic」

    def _cache(self, count=12):
        songs = [video(f"歌曲{i}", channel=f"頻道{i}") for i in range(1, count + 1)]
        return SongCache(client=FakeClient({self.YTMUSIC_ID: songs}), ttl_minutes=60)

    def test_defaults_to_one_song_from_configured_playlist(self):
        result = perform_random(cache=self._cache(), rng=random.Random(42))

        self.assertEqual(result["returned"], 1)
        self.assertEqual(len(result["songs"]), 1)
        self.assertEqual(result["songs"][0]["playlist"], "YTMusic")
        self.assertEqual(result["candidates"], 12)

    def test_song_carries_url_for_bot_playback(self):
        song = perform_random(cache=self._cache(), rng=random.Random(42))["songs"][0]

        # 機器人只要 url 就能點播，其餘欄位供顯示
        self.assertTrue(song["url"].startswith("https://youtu.be/"))
        self.assertIn("title", song)
        self.assertIn("channel", song)
        self.assertIn("position", song)

    def test_count_returns_distinct_songs(self):
        result = perform_random(count=5, cache=self._cache(), rng=random.Random(42))

        self.assertEqual(result["returned"], 5)
        self.assertEqual(len({s["position"] for s in result["songs"]}), 5)  # 不重複

    def test_count_is_clamped_to_max(self):
        result = perform_random(count=999, cache=self._cache(count=50), rng=random.Random(42))
        self.assertEqual(result["returned"], MAX_RANDOM_COUNT)

    def test_count_beyond_candidates_returns_all_without_padding(self):
        result = perform_random(count=10, cache=self._cache(count=3), rng=random.Random(42))

        self.assertEqual(result["returned"], 3)
        self.assertEqual(len({s["position"] for s in result["songs"]}), 3)

    def test_keyword_narrows_candidates(self):
        cache = SongCache(
            client=FakeClient(
                {
                    self.YTMUSIC_ID: [
                        video("Monsters", channel="頻道A"),
                        video("另一首", channel="Monsters樂團"),
                        video("無關的歌", channel="頻道B"),
                    ]
                }
            ),
            ttl_minutes=60,
        )

        result = perform_random(count=10, keyword="monsters", cache=cache, rng=random.Random(42))

        self.assertEqual(result["candidates"], 2)  # 歌名命中 1 + 頻道命中 1，不分大小寫
        self.assertEqual(result["returned"], 2)

    def test_keyword_too_short_returns_error(self):
        result = perform_random(keyword="M", cache=self._cache(), rng=random.Random(42))
        self.assertIn("error", result)

    def test_no_candidates_is_empty_not_error(self):
        cache = SongCache(client=FakeClient({self.YTMUSIC_ID: []}), ttl_minutes=60)

        result = perform_random(cache=cache, rng=random.Random(42))

        self.assertNotIn("error", result)  # 空清單不是錯誤，呼叫端檢查 songs 即可
        self.assertEqual(result["songs"], [])
        self.assertEqual(result["returned"], 0)

    def test_same_seed_reproduces_same_pick(self):
        cache = self._cache()

        first = perform_random(count=3, cache=cache, rng=random.Random(7))
        second = perform_random(count=3, cache=cache, rng=random.Random(7))

        self.assertEqual(first["songs"], second["songs"])

    def test_picks_vary_across_calls(self):
        """沒注入 rng 時要真的隨機——12 首抽 30 次不該全部相同。"""
        cache = self._cache()

        picked = {perform_random(cache=cache)["songs"][0]["position"] for _ in range(30)}

        self.assertGreater(len(picked), 1)

    def test_random_does_not_spend_quota_on_repeat_calls(self):
        client = FakeClient({self.YTMUSIC_ID: [video("歌")]})
        cache = SongCache(client=client, ttl_minutes=60)

        perform_random(cache=cache)
        perform_random(cache=cache)

        self.assertEqual(client.fetch_count, 1)  # 抽歌走快取，不重抓

    def test_unknown_playlist_name_raises_with_hint(self):
        with self.assertRaises(KeyError) as ctx:
            perform_random(playlist="不存在的清單", cache=self._cache())
        self.assertIn("可用名稱", str(ctx.exception))


class FakeTrendingClient:
    """記錄 fetch_most_popular 收到的參數，回傳固定榜單。"""

    def __init__(self, items=None):
        self._items = items if items is not None else [{"id": "hot1"}, {"id": "hot2"}]
        self.calls = []

    def fetch_most_popular(self, region_code, max_results, category_id=""):
        self.calls.append({"region": region_code, "limit": max_results, "category_id": category_id})
        return self._items[:max_results]


class TestPerformTrending(unittest.TestCase):
    def test_defaults_to_taiwan_all_categories_top_three(self):
        client = FakeTrendingClient()

        result = perform_trending(client=client)

        self.assertEqual(client.calls[0], {"region": "TW", "limit": 3, "category_id": ""})
        self.assertEqual(result["region"], "TW")
        self.assertEqual(result["category"], "all")

    def test_friendly_category_is_translated_to_id(self):
        client = FakeTrendingClient()

        perform_trending(category="music", client=client)
        perform_trending(category="gaming", client=client)

        self.assertEqual(client.calls[0]["category_id"], "10")
        self.assertEqual(client.calls[1]["category_id"], "20")

    def test_category_is_normalised_before_lookup(self):
        client = FakeTrendingClient()
        result = perform_trending(category="  MUSIC ", client=client)

        self.assertEqual(client.calls[0]["category_id"], "10")
        self.assertEqual(result["category"], "music")

    def test_region_is_upper_cased(self):
        client = FakeTrendingClient()
        perform_trending(region="jp", client=client)
        self.assertEqual(client.calls[0]["region"], "JP")

    def test_limit_is_clamped_to_valid_range(self):
        client = FakeTrendingClient(items=[{"id": f"v{i}"} for i in range(60)])

        perform_trending(limit=0, client=client)
        perform_trending(limit=999, client=client)

        self.assertEqual(client.calls[0]["limit"], 1)
        self.assertEqual(client.calls[1]["limit"], 50)

    def test_unknown_category_raises_with_valid_options(self):
        with self.assertRaises(ValueError) as ctx:
            perform_trending(category="音樂", client=FakeTrendingClient())
        self.assertIn("music", str(ctx.exception))

    def test_result_is_ranked_and_labelled_as_public_chart(self):
        client = FakeTrendingClient()

        result = perform_trending(limit=2, client=client)

        self.assertEqual([v["rank"] for v in result["videos"]], [1, 2])
        self.assertIn("非使用者的播放清單", result["source"])


class FakeVideoClient:
    """回傳預先設定的單一影片 item；查無時回 None（同真實 client 行為）。"""

    def __init__(self, item=None):
        self._item = item
        self.requested_ids = []

    def fetch_video(self, video_id):
        self.requested_ids.append(video_id)
        return self._item


class TestPerformVideoInfo(unittest.TestCase):
    def _item(self, **overrides):
        base = {
            "id": "dQw4w9WgXcQ",
            "snippet": {
                "title": "影片標題",
                "channelTitle": "頻道名",
                "publishedAt": "2024-01-01T00:00:00Z",
                "liveBroadcastContent": "none",
                "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/x/hqdefault.jpg", "width": 480}},
            },
            "statistics": {"viewCount": "12345"},
            "contentDetails": {"duration": "PT12M34S"},
        }
        base.update(overrides)
        return base

    def test_success_returns_contract_shape(self):
        client = FakeVideoClient(self._item())

        result = perform_video_info("dQw4w9WgXcQ", client=client)

        self.assertEqual(client.requested_ids, ["dQw4w9WgXcQ"])
        self.assertEqual(result["duration_seconds"], 754)
        self.assertFalse(result["is_live"])
        self.assertEqual(result["url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_live_stream_is_flagged(self):
        item = self._item(contentDetails={"duration": "P0D"})
        item["snippet"]["liveBroadcastContent"] = "live"

        result = perform_video_info("dQw4w9WgXcQ", client=FakeVideoClient(item))

        self.assertTrue(result["is_live"])

    def test_invalid_id_raises_before_spending_quota(self):
        client = FakeVideoClient(self._item())

        with self.assertRaises(ValueError) as ctx:
            perform_video_info("not-11", client=client)

        self.assertEqual(ctx.exception.args[0], "INVALID_VIDEO_ID")
        self.assertEqual(client.requested_ids, [])  # 格式錯就不該打 API 燒配額

    def test_missing_video_raises_not_found(self):
        with self.assertRaises(KeyError) as ctx:
            perform_video_info("aaaaaaaaaaa", client=FakeVideoClient(None))
        self.assertEqual(ctx.exception.args[0], "VIDEO_NOT_FOUND")


class TestPerformTranscript(unittest.TestCase):
    class _FakeApi:
        def __init__(self):
            self.listed = []

        def list(self, video_id):
            self.listed.append(video_id)

            class Track:
                language, language_code, is_generated = "English", "en", False

                @staticmethod
                def fetch():
                    class S:
                        text = "hello world"

                    return [S()]

            return [Track()]

    def test_invalid_id_raises_before_touching_upstream(self):
        api = self._FakeApi()

        with self.assertRaises(ValueError) as ctx:
            perform_transcript("not-11", api=api)

        self.assertEqual(ctx.exception.args[0], "INVALID_VIDEO_ID")
        self.assertEqual(api.listed, [])  # 格式錯就不該打上游

    def test_valid_id_returns_contract_shape(self):
        result = perform_transcript("dQw4w9WgXcQ", api=self._FakeApi())

        self.assertEqual(result["language_code"], "en")
        self.assertEqual(result["text"], "hello world")
        self.assertFalse(result["truncated"])


class TestRestAudioRoute(unittest.TestCase):
    """路由層：/audio 的錯誤分流與「回應送出後清暫存」的 background task。

    直接以假 Request 呼叫路由 coroutine（extract_audio 一律 mock 掉，不碰網路）。
    """

    VID = "dQw4w9WgXcQ"

    def _call(self, video_id):
        scope = {
            "type": "http",
            "method": "GET",
            "path": f"/audio/{video_id}",
            "headers": [],
            "query_string": b"",
            "path_params": {"video_id": video_id},
        }

        async def invoke():
            response = await rest_audio(Request(scope))
            if response.background is not None:
                await response.background()  # 模擬「回應送出後」的清理時機
            return response

        return asyncio.run(invoke())

    def _body(self, response):
        return json.loads(response.body)

    def test_invalid_id_rejected_without_spawning_ytdlp(self):
        with mock.patch("youtube_toolkit.audio_extract.extract_audio") as fake:
            response = self._call("not-11")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._body(response), {"error": "INVALID_VIDEO_ID"})
        fake.assert_not_called()  # 格式錯就不該起子程序

    def test_extraction_errors_map_to_their_status_codes(self):
        cases = (
            (audio_extract.AudioUnavailable(), 404, "VIDEO_NOT_FOUND"),
            (audio_extract.AudioLiveStream(), 400, "LIVE_STREAM"),
            (audio_extract.AudioTooLong(), 413, "AUDIO_TOO_LONG"),
            (audio_extract.AudioExtractError(), 502, "AUDIO_EXTRACT_FAILED"),
        )
        for exc, status, code in cases:
            with mock.patch("youtube_toolkit.audio_extract.extract_audio", side_effect=exc):
                response = self._call(self.VID)
            self.assertEqual(response.status_code, status)
            self.assertEqual(self._body(response), {"error": code})

    def test_unexpected_exception_returns_502_not_bare_500(self):
        with mock.patch(
            "youtube_toolkit.audio_extract.extract_audio", side_effect=RuntimeError("boom")
        ):
            response = self._call(self.VID)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(self._body(response), {"error": "AUDIO_EXTRACT_FAILED"})

    def test_success_streams_ogg_and_cleans_workdir_after_send(self):
        workdir = Path(tempfile.mkdtemp(prefix="test-audio-route-"))
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)
        audio_path = workdir / f"{self.VID}.ogg"
        audio_path.write_bytes(b"OggS fake opus")

        with mock.patch("youtube_toolkit.audio_extract.extract_audio", return_value=audio_path):
            response = self._call(self.VID)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "audio/ogg")
        self.assertIn(f"{self.VID}.ogg", response.headers["content-disposition"])
        self.assertFalse(workdir.exists())  # background task 已把整個暫存目錄清掉


if __name__ == "__main__":
    unittest.main()
