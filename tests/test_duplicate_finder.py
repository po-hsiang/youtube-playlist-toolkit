"""duplicate_finder 測試：標題正規化與分組邏輯（純函式，無網路）。"""

import unittest

from youtube_toolkit.duplicate_finder import find_potential_duplicates, normalize_title


def video(title, views=0):
    return {"title": title, "channel": "ch", "views": views, "url": "https://youtu.be/x"}


class TestNormalizeTitle(unittest.TestCase):
    def test_keeps_bracket_content(self):
        # 括號內容（歌名）必須保留，只移除括號符號本身
        result = normalize_title("周杰倫 Jay Chou【七里香 Common Jasmine Orange】Official MV")
        self.assertIn("七里香", result)
        self.assertNotIn("【", result)
        self.assertNotIn("official", result)
        self.assertNotIn("mv", result)

    def test_removes_promo_noise(self):
        self.assertEqual(normalize_title("歌名 (Official Music Video)"), "歌名")
        self.assertEqual(normalize_title("歌名 Lyric Video"), "歌名")
        self.assertEqual(normalize_title("歌名【官方完整版】"), "歌名")
        self.assertEqual(normalize_title("歌名 官方歌詞版 HD"), "歌名")

    def test_removes_feat_token_but_keeps_artist(self):
        self.assertEqual(normalize_title("Song (feat. Alice)"), "song alice")

    def test_keeps_semantic_words(self):
        # live / cover 有語意（現場版≠原版），不可移除
        self.assertIn("live", normalize_title("歌名 Live"))
        self.assertIn("cover", normalize_title("歌名 Cover"))

    def test_collapses_whitespace_and_lowercases(self):
        self.assertEqual(normalize_title("  A   B  "), "a b")


class TestFindPotentialDuplicates(unittest.TestCase):
    def test_classic_substring_case_still_works(self):
        videos = [
            video("周杰倫 Jay Chou【七里香 Common Jasmine Orange】Official MV", views=1000),
            video("七里香", views=10),
            video("不相關的歌", views=5),
        ]
        groups = find_potential_duplicates(videos)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)
        self.assertEqual(groups[0][0]["views"], 1000)  # 觀看數高的排第一（建議保留）

    def test_normalization_unlocks_previously_missed_pair(self):
        # 重構前的純子字串比對抓不到這組：「(Official MV)」vs「Lyric Video」互不包含
        videos = [
            video("告白氣球 (Official MV)", views=500),
            video("告白氣球 Lyric Video", views=50),
        ]
        groups = find_potential_duplicates(videos)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_no_duplicates_returns_empty(self):
        videos = [video("歌一"), video("歌二"), video("歌三")]
        self.assertEqual(find_potential_duplicates(videos), [])

    def test_titles_reduced_to_noise_only_are_ignored(self):
        # 正規化後變空／過短的標題不可互相亂配對
        videos = [video("Official MV"), video("MV"), video("正常的歌名")]
        self.assertEqual(find_potential_duplicates(videos), [])

    def test_grouped_video_not_picked_up_twice(self):
        videos = [video("七里香", views=3), video("七里香 MV", views=2), video("七里香 Official MV", views=1)]
        groups = find_potential_duplicates(videos)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)


if __name__ == "__main__":
    unittest.main()
