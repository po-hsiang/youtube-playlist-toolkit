"""transcript 測試：字幕軌優先序、文字整形、截斷與例外映射（假 api，無網路）。

例外映射測試 raise 的是 youtube-transcript-api 的**真實例外類別**，
套件升級若改了例外階層，這裡會先紅燈。
"""

import unittest

from youtube_transcript_api import (
    AgeRestricted,
    IpBlocked,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeRequestFailed,
)

from youtube_toolkit import transcript
from youtube_toolkit.transcript import (
    TranscriptUnavailable,
    TranscriptUpstreamError,
    build_text,
    fetch_transcript,
    select_transcript,
)

VID = "dQw4w9WgXcQ"


class FakeSnippet:
    def __init__(self, text):
        self.text = text


class FakeTrack:
    def __init__(self, language_code, is_generated=False, language=None, snippets=None):
        self.language_code = language_code
        self.language = language or language_code
        self.is_generated = is_generated
        self._snippets = snippets or [FakeSnippet("預設字幕")]

    def fetch(self):
        return self._snippets


class FakeApi:
    """回傳固定軌道清單；或在 list() 時 raise 指定例外（模擬套件的失敗情境）。"""

    def __init__(self, tracks=None, raises=None):
        self._tracks = tracks or []
        self._raises = raises

    def list(self, video_id):
        if self._raises:
            raise self._raises
        return list(self._tracks)


class TestSelectTranscript(unittest.TestCase):
    def test_manual_zh_hant_tw_beats_manual_english(self):
        tracks = [FakeTrack("en"), FakeTrack("zh-Hant-TW")]
        self.assertEqual(select_transcript(tracks).language_code, "zh-Hant-TW")

    def test_language_priority_order_within_manual(self):
        # zh-Hant 應勝過 zh-TW（優先序 zh-Hant-TW → zh-Hant → zh-TW → en）
        tracks = [FakeTrack("en"), FakeTrack("zh-TW"), FakeTrack("zh-Hant")]
        self.assertEqual(select_transcript(tracks).language_code, "zh-Hant")

    def test_any_manual_beats_generated_chinese(self):
        # 規格第 2 點：任一人工上傳（即使是日文）勝過自動生成的繁中
        tracks = [FakeTrack("zh-Hant-TW", is_generated=True), FakeTrack("ja")]
        chosen = select_transcript(tracks)
        self.assertEqual(chosen.language_code, "ja")
        self.assertFalse(chosen.is_generated)

    def test_generated_follows_same_language_priority(self):
        tracks = [FakeTrack("ko", is_generated=True), FakeTrack("en", is_generated=True)]
        self.assertEqual(select_transcript(tracks).language_code, "en")

    def test_falls_back_to_any_generated(self):
        tracks = [FakeTrack("ko", is_generated=True)]
        self.assertEqual(select_transcript(tracks).language_code, "ko")

    def test_no_tracks_returns_none(self):
        self.assertIsNone(select_transcript([]))


class TestBuildText(unittest.TestCase):
    def test_joins_segments_with_single_space(self):
        snippets = [FakeSnippet("第一段"), FakeSnippet("第二段"), FakeSnippet("第三段")]
        self.assertEqual(build_text(snippets), "第一段 第二段 第三段")

    def test_flattens_internal_newlines_and_extra_whitespace(self):
        snippets = [FakeSnippet("兩行\n字幕"), FakeSnippet("  空白很多  ")]
        self.assertEqual(build_text(snippets), "兩行 字幕 空白很多")

    def test_empty_segments_are_skipped(self):
        snippets = [FakeSnippet("有字"), FakeSnippet("  "), FakeSnippet("")]
        self.assertEqual(build_text(snippets), "有字")


class TestFetchTranscript(unittest.TestCase):
    def _api(self, text="這 是 字幕", **track_kwargs):
        snippets = [FakeSnippet(word) for word in text.split()]
        return FakeApi(tracks=[FakeTrack("zh-Hant-TW", snippets=snippets, **track_kwargs)])

    def test_contract_fields(self):
        """欄位名是跨服務契約——此測試斷言整個 dict，改名會直接紅燈。"""
        api = FakeApi(tracks=[FakeTrack(
            "zh-TW", language="Chinese (Taiwan)", snippets=[FakeSnippet("哈囉"), FakeSnippet("世界")]
        )])

        result = fetch_transcript(VID, api=api)

        self.assertEqual(result, {
            "video_id": VID,
            "language": "Chinese (Taiwan)",
            "language_code": "zh-TW",
            "is_auto_generated": False,
            "text": "哈囉 世界",
            "char_count": 5,
            "truncated": False,
        })

    def test_auto_generated_flag_is_reported(self):
        result = fetch_transcript(VID, api=self._api(is_generated=True))
        self.assertTrue(result["is_auto_generated"])

    def test_truncation_keeps_full_char_count(self):
        result = fetch_transcript(VID, max_chars=5, api=self._api(text="一二三 四五六 七八九"))

        self.assertEqual(result["text"], "一二三 四")  # 截到 5 字元
        self.assertTrue(result["truncated"])
        self.assertEqual(result["char_count"], 11)  # 完整長度，下游才知道全文有多少

    def test_max_chars_none_or_zero_means_no_truncation(self):
        for max_chars in (None, 0):
            result = fetch_transcript(VID, max_chars=max_chars, api=self._api())
            self.assertFalse(result["truncated"], f"max_chars={max_chars}")

    def test_exact_length_is_not_truncated(self):
        result = fetch_transcript(VID, max_chars=7, api=self._api(text="剛好七個字元"))
        self.assertFalse(result["truncated"])

    # ── 例外映射（raise 套件的真實例外）──────────────────

    def test_video_unavailable_maps_to_video_not_found(self):
        with self.assertRaises(TranscriptUnavailable) as ctx:
            fetch_transcript(VID, api=FakeApi(raises=VideoUnavailable(VID)))
        self.assertEqual(ctx.exception.args[0], "VIDEO_NOT_FOUND")

    def test_transcripts_disabled_maps_to_no_transcript(self):
        with self.assertRaises(TranscriptUnavailable) as ctx:
            fetch_transcript(VID, api=FakeApi(raises=TranscriptsDisabled(VID)))
        self.assertEqual(ctx.exception.args[0], "NO_TRANSCRIPT")

    def test_no_transcript_found_maps_to_no_transcript(self):
        error = NoTranscriptFound(VID, ["zh-Hant-TW"], transcript_data=None)
        with self.assertRaises(TranscriptUnavailable) as ctx:
            fetch_transcript(VID, api=FakeApi(raises=error))
        self.assertEqual(ctx.exception.args[0], "NO_TRANSCRIPT")

    def test_age_restricted_maps_to_no_transcript(self):
        with self.assertRaises(TranscriptUnavailable) as ctx:
            fetch_transcript(VID, api=FakeApi(raises=AgeRestricted(VID)))
        self.assertEqual(ctx.exception.args[0], "NO_TRANSCRIPT")

    def test_empty_track_list_maps_to_no_transcript(self):
        with self.assertRaises(TranscriptUnavailable) as ctx:
            fetch_transcript(VID, api=FakeApi(tracks=[]))
        self.assertEqual(ctx.exception.args[0], "NO_TRANSCRIPT")

    def test_ip_blocked_maps_to_upstream_error(self):
        with self.assertRaises(TranscriptUpstreamError) as ctx:
            fetch_transcript(VID, api=FakeApi(raises=IpBlocked(VID)))
        self.assertEqual(ctx.exception.args[0], "TRANSCRIPT_UPSTREAM_ERROR")

    def test_request_failed_maps_to_upstream_error(self):
        error = YouTubeRequestFailed(VID, http_error=Exception("HTTP 429"))
        with self.assertRaises(TranscriptUpstreamError) as ctx:
            fetch_transcript(VID, api=FakeApi(raises=error))
        self.assertEqual(ctx.exception.args[0], "TRANSCRIPT_UPSTREAM_ERROR")


class TestDefaultMcpTruncation(unittest.TestCase):
    def test_mcp_default_is_a_sane_truncation_limit(self):
        # MCP 端預設截斷、REST 端預設不截斷是規格要求；上限值本身要存在且合理
        self.assertGreaterEqual(transcript.DEFAULT_MCP_MAX_CHARS, 1000)


if __name__ == "__main__":
    unittest.main()
