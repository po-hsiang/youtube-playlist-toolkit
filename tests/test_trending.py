"""trending 測試：類別對照、時長解析與榜單整形（純函式，無網路）。"""

import unittest

from youtube_toolkit import trending


def item(video_id="abc", title="影片", channel="頻道", views="1000", likes="50",
         duration="PT3M39S", published="2026-07-24T10:00:00Z", category="10", live="none"):
    """組出一筆 videos.list 的原始回應項目（欄位取自實測結果）。"""
    snippet = {
        "title": title,
        "channelTitle": channel,
        "publishedAt": published,
        "categoryId": category,
        "liveBroadcastContent": live,
    }
    statistics = {"viewCount": views}
    if likes is not None:
        statistics["likeCount"] = likes
    return {
        "id": video_id,
        "snippet": snippet,
        "statistics": statistics,
        "contentDetails": {"duration": duration},
    }


class TestResolveCategory(unittest.TestCase):
    def test_friendly_names_map_to_ids(self):
        self.assertEqual(trending.resolve_category("music"), "10")
        self.assertEqual(trending.resolve_category("gaming"), "20")

    def test_all_means_no_filter(self):
        self.assertEqual(trending.resolve_category("all"), "")

    def test_empty_defaults_to_all(self):
        self.assertEqual(trending.resolve_category(""), "")

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(trending.resolve_category("  Music "), "10")

    def test_raw_numeric_id_passes_through(self):
        self.assertEqual(trending.resolve_category("28"), "28")  # 對照表以外的類別逃生門

    def test_unknown_name_lists_valid_options(self):
        with self.assertRaises(ValueError) as ctx:
            trending.resolve_category("音樂")
        self.assertIn("music", str(ctx.exception))


class TestDuration(unittest.TestCase):
    def test_minutes_and_seconds(self):
        self.assertEqual(trending.duration_seconds("PT3M39S"), 219)
        self.assertEqual(trending.format_duration(219), "3:39")

    def test_hours_are_padded(self):
        self.assertEqual(trending.duration_seconds("PT9H29M49S"), 34189)
        self.assertEqual(trending.format_duration(34189), "9:29:49")

    def test_seconds_only(self):
        self.assertEqual(trending.duration_seconds("PT45S"), 45)
        self.assertEqual(trending.format_duration(45), "0:45")

    def test_live_stream_placeholder_is_zero(self):
        self.assertEqual(trending.duration_seconds("P0D"), 0)  # 進行中的直播
        self.assertEqual(trending.format_duration(0), "")

    def test_unparsable_is_zero_not_crash(self):
        self.assertEqual(trending.duration_seconds("垃圾"), 0)
        self.assertEqual(trending.duration_seconds(""), 0)


class TestToTrendingVideos(unittest.TestCase):
    def test_rank_follows_api_order(self):
        videos = trending.to_trending_videos([item(video_id="a"), item(video_id="b")])
        self.assertEqual([v["rank"] for v in videos], [1, 2])
        self.assertEqual(videos[0]["video_id"], "a")

    def test_maps_all_display_fields(self):
        video = trending.to_trending_videos([item(video_id="xyz", title="歌", channel="台")])[0]

        self.assertEqual(video["title"], "歌")
        self.assertEqual(video["channel"], "台")
        self.assertEqual(video["views"], 1000)
        self.assertEqual(video["likes"], 50)
        self.assertEqual(video["published_at"], "2026-07-24")  # 只取日期，agent 不需要時分秒
        self.assertEqual(video["duration"], "3:39")
        self.assertEqual(video["url"], "https://youtu.be/xyz")

    def test_hidden_like_count_becomes_none(self):
        video = trending.to_trending_videos([item(likes=None)])[0]
        self.assertIsNone(video["likes"])  # 頻道可隱藏讚數，不能當成 0

    def test_live_flag_and_duration_let_client_filter_streams(self):
        stream = trending.to_trending_videos([item(duration="P0D", live="live")])[0]

        self.assertTrue(stream["is_live"])
        self.assertEqual(stream["duration_seconds"], 0)

    def test_missing_fields_do_not_crash(self):
        video = trending.to_trending_videos([{"id": "bare"}])[0]

        self.assertEqual(video["title"], "N/A")
        self.assertEqual(video["views"], 0)
        self.assertIsNone(video["likes"])


class TestBuildResult(unittest.TestCase):
    def test_result_states_it_is_not_the_users_playlist(self):
        result = trending.build_result("TW", "music", [item()])

        self.assertIn("非使用者的播放清單", result["source"])  # 免得 agent 講錯資料來源
        self.assertEqual(result["region"], "TW")
        self.assertEqual(result["category"], "music")
        self.assertEqual(result["returned"], 1)

    def test_empty_chart_is_not_an_error(self):
        result = trending.build_result("TW", "music", [])
        self.assertEqual(result["videos"], [])
        self.assertEqual(result["returned"], 0)


if __name__ == "__main__":
    unittest.main()
