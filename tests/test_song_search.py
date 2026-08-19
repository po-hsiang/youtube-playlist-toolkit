"""song_search 測試：跨語言標籤比對、命中排序與查無結果提示（純函式，無網路）。

測試資料刻意抄自真實歌單的形狀：Topic 頻道的歌名／頻道名全是羅馬字，
藝人的母語名只存在於標籤裡——這正是「搜周興哲搜不到」的成因。
"""

import random
import unittest

from youtube_toolkit.song_search import (
    MATCH_CHANNEL,
    MATCH_TAG,
    MATCH_TITLE,
    NO_MATCH_HINT,
    match_field,
    pick_random_songs,
    search_playlists,
    tag_matcher,
)


def video(title, channel="頻道", views=10, tags=None):
    song = {
        "video_id": "x",
        "title": title,
        "channel": channel,
        "views": views,
        "url": f"https://youtu.be/{title}",
    }
    if tags is not None:
        song["tags"] = tags
    return song


def getter(videos_by_name):
    """把 {清單名稱: [影片]} 包成 search_playlists 要的 get_videos。"""
    return lambda name: list(videos_by_name.get(name, []))


# 真實資料樣本（tags 取自實際 API 回應）
ERIC = video(
    'All For You ("Spider-Man: Brand New Day" Taiwan End Credit Song)',
    channel="Eric Chou - Topic",
    views=1395152,
    tags=["Eric Chou", "周兴哲", "周興哲", "All For You"],
)
YORUSHIKA = video(
    "Just a Sunny Day for You",
    channel="Yorushika - Topic",
    tags=["Yorushika", "ヨルシカ", "ただ君に晴れ"],
)
IU = video("Through the Night", channel="IU - Topic", tags=["IU", "아이유(IU)", "밤편지"])
# 別人的 MV 掛上宣傳性標籤：標籤比對的已知副作用
AIMER_MV = video("蝴蝶結 中文字幕版 MV", channel="音樂搬運頻道", tags=["Aimer", "RADWIMPS", "ONE OK ROCK"])


class TestTagMatcher(unittest.TestCase):
    def test_cjk_keyword_uses_substring(self):
        match = tag_matcher("周興哲")
        self.assertTrue(match("周興哲"))
        self.assertTrue(match("周興哲 eric chou"))

    def test_latin_keyword_requires_word_boundary(self):
        match = tag_matcher("iu")
        self.assertFalse(match("studio"))  # 子字串比對會誤命中，詞邊界擋掉
        self.assertFalse(match("liu"))
        self.assertTrue(match("iu"))
        self.assertTrue(match("아이유(iu)"))  # 括號即邊界，韓文標籤仍命中

    def test_short_latin_keyword_does_not_match_inside_word(self):
        self.assertFalse(tag_matcher("ai")("aimer"))
        self.assertTrue(tag_matcher("ai")("ai"))

    def test_regex_special_characters_are_escaped(self):
        match = tag_matcher("c++")
        self.assertTrue(match("c++"))
        self.assertFalse(match("cxx"))

    def test_multi_word_latin_keyword(self):
        match = tag_matcher("eric chou")
        self.assertTrue(match("eric chou"))
        self.assertFalse(match("frederic chou"))


class TestMatchField(unittest.TestCase):
    def test_title_wins_over_channel_and_tag(self):
        song = video("Yorushika 專輯", channel="Yorushika - Topic", tags=["Yorushika"])
        self.assertEqual(match_field(song, "yorushika"), MATCH_TITLE)

    def test_channel_wins_over_tag(self):
        self.assertEqual(match_field(YORUSHIKA, "yorushika"), MATCH_CHANNEL)

    def test_native_name_only_in_tags(self):
        self.assertEqual(match_field(ERIC, "周興哲"), MATCH_TAG)
        self.assertEqual(match_field(YORUSHIKA, "ヨルシカ"), MATCH_TAG)
        self.assertEqual(match_field(IU, "아이유"), MATCH_TAG)

    def test_no_match_returns_empty_string(self):
        self.assertEqual(match_field(ERIC, "五月天"), "")

    def test_video_without_tags_key_does_not_crash(self):
        legacy = video("歌", channel="頻道")  # 舊快取沒有 tags 欄位
        self.assertEqual(match_field(legacy, "歌"), MATCH_TITLE)
        self.assertEqual(match_field(legacy, "周興哲"), "")

    def test_video_with_null_tags_does_not_crash(self):
        self.assertEqual(match_field(video("歌", tags=None), "周興哲"), "")


class TestSearchAcrossLanguages(unittest.TestCase):
    def _search(self, keyword, limit=50):
        return search_playlists(keyword, ["清單"], getter({"清單": [ERIC, YORUSHIKA, IU]}), limit)

    def test_chinese_name_finds_romanised_song(self):
        result = self._search("周興哲")
        self.assertEqual(result["total_matches"], 1)
        self.assertEqual(result["results"][0]["channel"], "Eric Chou - Topic")
        self.assertEqual(result["results"][0]["matched_on"], MATCH_TAG)

    def test_simplified_chinese_tag_also_works(self):
        self.assertEqual(self._search("周兴哲")["total_matches"], 1)

    def test_japanese_and_korean_names(self):
        self.assertEqual(self._search("ヨルシカ")["total_matches"], 1)
        self.assertEqual(self._search("아이유")["total_matches"], 1)

    def test_romanised_name_still_works(self):
        result = self._search("Eric Chou")
        self.assertEqual(result["total_matches"], 1)
        self.assertEqual(result["results"][0]["matched_on"], MATCH_CHANNEL)

    def test_promotional_tags_are_a_known_side_effect(self):
        """他人 MV 上的宣傳標籤會被命中，但 matched_on=tag 讓呼叫端看得出來。"""
        result = search_playlists("RADWIMPS", ["清單"], getter({"清單": [AIMER_MV]}))
        self.assertEqual(result["total_matches"], 1)
        self.assertEqual(result["results"][0]["matched_on"], MATCH_TAG)


class TestResultOrdering(unittest.TestCase):
    def _videos(self):
        return {
            "清單": [
                video("A", channel="其他頻道", tags=["Yorushika"]),  # 標籤命中
                video("B", channel="Yorushika - Topic"),  # 頻道命中
                video("Yorushika 精選", channel="其他頻道"),  # 歌名命中
            ]
        }

    def test_direct_matches_come_before_tag_matches(self):
        result = search_playlists("yorushika", ["清單"], getter(self._videos()))
        self.assertEqual([r["matched_on"] for r in result["results"]], [MATCH_CHANNEL, MATCH_TITLE, MATCH_TAG])

    def test_limit_prefers_direct_matches(self):
        result = search_playlists("yorushika", ["清單"], getter(self._videos()), limit=2)
        self.assertEqual(result["returned"], 2)
        self.assertEqual(result["total_matches"], 3)  # limit 只砍回傳不砍統計
        self.assertNotIn(MATCH_TAG, [r["matched_on"] for r in result["results"]])

    def test_tag_matches_fill_the_remaining_slots(self):
        only_tags = {"清單": [video(f"歌{i}", tags=["Yorushika"]) for i in range(5)]}
        result = search_playlists("yorushika", ["清單"], getter(only_tags), limit=3)
        self.assertEqual(result["returned"], 3)
        self.assertEqual(result["total_matches"], 5)


class TestNoMatchHint(unittest.TestCase):
    def test_zero_matches_carries_hint(self):
        result = search_playlists("五月天", ["清單"], getter({"清單": [ERIC]}))
        self.assertEqual(result["total_matches"], 0)
        self.assertEqual(result["hint"], NO_MATCH_HINT)

    def test_hint_absent_when_something_matched(self):
        self.assertNotIn("hint", search_playlists("周興哲", ["清單"], getter({"清單": [ERIC]})))

    def test_too_short_keyword_is_an_error_not_a_hint(self):
        result = search_playlists("周", ["清單"], getter({"清單": [ERIC]}))
        self.assertIn("error", result)
        self.assertNotIn("hint", result)


class TestRandomSharesMatchSemantics(unittest.TestCase):
    def test_keyword_matches_via_tags(self):
        result = pick_random_songs(["清單"], getter({"清單": [ERIC, YORUSHIKA]}), keyword="周興哲",
                                   rng=random.Random(0))
        self.assertEqual(result["candidates"], 1)
        self.assertEqual(result["songs"][0]["matched_on"], MATCH_TAG)

    def test_empty_candidates_carry_hint(self):
        result = pick_random_songs(["清單"], getter({"清單": [ERIC]}), keyword="五月天")
        self.assertEqual(result["songs"], [])
        self.assertEqual(result["hint"], NO_MATCH_HINT)

    def test_no_keyword_means_no_hint_and_blank_matched_on(self):
        result = pick_random_songs(["清單"], getter({"清單": [ERIC]}), rng=random.Random(0))
        self.assertNotIn("hint", result)
        self.assertEqual(result["songs"][0]["matched_on"], "")


if __name__ == "__main__":
    unittest.main()
