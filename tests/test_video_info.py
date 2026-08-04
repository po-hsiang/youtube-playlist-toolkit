"""video_info 測試：ID 驗證、縮圖挑選與契約整形（純函式，無網路）。"""

import unittest

from youtube_toolkit import video_info


def thumb(url, width):
    return {"url": url, "width": width, "height": int(width * 9 / 16)}


def item(video_id="dQw4w9WgXcQ", title="影片標題", channel="頻道名", views="12345",
         duration="PT12M34S", live="none", published="2024-01-01T00:00:00Z", thumbnails=None):
    """組出一筆 videos.list 的原始回應項目（欄位取自真實 API）。"""
    return {
        "id": video_id,
        "snippet": {
            "title": title,
            "channelTitle": channel,
            "publishedAt": published,
            "liveBroadcastContent": live,
            "thumbnails": thumbnails if thumbnails is not None else {
                "default": thumb("https://i.ytimg.com/vi/x/default.jpg", 120),
                "high": thumb("https://i.ytimg.com/vi/x/hqdefault.jpg", 480),
            },
        },
        "statistics": {"viewCount": views},
        "contentDetails": {"duration": duration},
    }


class TestVideoIdValidation(unittest.TestCase):
    def test_valid_eleven_char_ids(self):
        for vid in ("dQw4w9WgXcQ", "a-b_c-d_e-f", "___________"):
            self.assertTrue(video_info.is_valid_video_id(vid), vid)

    def test_invalid_ids_rejected(self):
        for vid in ("", "short", "dQw4w9WgXcQQ", "dQw4w9WgXc!", "dQw4w9WgXc ", None):
            self.assertFalse(video_info.is_valid_video_id(vid), repr(vid))


class TestBestThumbnail(unittest.TestCase):
    def test_prefers_highest_resolution_by_priority(self):
        thumbnails = {
            "default": thumb("default.jpg", 120),
            "maxres": thumb("maxres.jpg", 1280),
            "high": thumb("hq.jpg", 480),
        }
        self.assertEqual(video_info.best_thumbnail_url(thumbnails), "maxres.jpg")

    def test_falls_back_down_the_priority_list(self):
        thumbnails = {"default": thumb("default.jpg", 120), "medium": thumb("mq.jpg", 320)}
        self.assertEqual(video_info.best_thumbnail_url(thumbnails), "mq.jpg")

    def test_unknown_keys_fall_back_to_widest(self):
        thumbnails = {"custom_a": thumb("a.jpg", 200), "custom_b": thumb("b.jpg", 900)}
        self.assertEqual(video_info.best_thumbnail_url(thumbnails), "b.jpg")

    def test_no_thumbnails_is_none(self):
        self.assertIsNone(video_info.best_thumbnail_url({}))


class TestToVideoInfo(unittest.TestCase):
    def test_contract_fields_for_normal_video(self):
        """欄位名是跨服務契約，這個測試就是契約本身——欄位改名必須先改下游。"""
        result = video_info.to_video_info(item())

        self.assertEqual(result, {
            "video_id": "dQw4w9WgXcQ",
            "title": "影片標題",
            "channel": "頻道名",
            "published_at": "2024-01-01T00:00:00Z",
            "duration": "PT12M34S",
            "duration_seconds": 754,
            "is_live": False,
            "views": 12345,
            "thumbnail_url": "https://i.ytimg.com/vi/x/hqdefault.jpg",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        })

    def test_live_flag_from_broadcast_content(self):
        result = video_info.to_video_info(item(live="live", duration="PT1H2M3S"))
        self.assertTrue(result["is_live"])

    def test_live_inferred_from_zero_duration(self):
        # 進行中直播的 contentDetails.duration 可能是 P0D，即使 flag 還沒更新也要判成直播
        result = video_info.to_video_info(item(duration="P0D"))
        self.assertTrue(result["is_live"])
        self.assertEqual(result["duration_seconds"], 0)

    def test_normal_video_is_not_live(self):
        self.assertFalse(video_info.to_video_info(item(duration="PT45S"))["is_live"])

    def test_missing_optional_fields_do_not_crash(self):
        result = video_info.to_video_info({"id": "bare_item_id"})

        self.assertEqual(result["title"], "N/A")
        self.assertEqual(result["views"], 0)
        self.assertIsNone(result["thumbnail_url"])
        self.assertTrue(result["is_live"])  # 無時長 → 0 秒 → 依規格視為直播（保守方向）


if __name__ == "__main__":
    unittest.main()
