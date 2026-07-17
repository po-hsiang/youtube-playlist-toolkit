"""playlist_cleaner 測試：候選名單安全性（不公開永不列入）、二次驗證、留底。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from youtube_toolkit import playlist_cleaner
from youtube_toolkit.playlist_health import audit_playlist
from youtube_toolkit.playlist_cleaner import removal_candidates, split_still_dead, write_backup


def entry(video_id, title="正常影片", privacy_status="public"):
    return {
        "playlist_item_id": f"pi-{video_id}",
        "video_id": video_id,
        "title": title,
        "privacy_status": privacy_status,
    }


def detail(title="正常影片", channel="頻道", views=100, privacy_status="public"):
    return {"title": title, "channel": channel, "views": views, "privacy_status": privacy_status}


def build_report():
    """固定情境：正常 1、私人 1、已刪除 1、不公開 1。"""
    entries = [
        entry("ok"),
        entry("pri", title="Private video", privacy_status="private"),
        entry("del", title="Deleted video", privacy_status="privacyStatusUnspecified"),
        entry("unl"),
    ]
    details = {
        "ok": detail("好歌"),
        "unl": detail("不公開但能聽", privacy_status="unlisted"),
    }
    return audit_playlist(entries, details)


class TestRemovalCandidates(unittest.TestCase):
    def test_only_private_and_deleted_are_candidates(self):
        candidates = removal_candidates(build_report())
        self.assertEqual(sorted(c["video_id"] for c in candidates), ["del", "pri"])

    def test_unlisted_is_never_a_candidate(self):
        # 主人明確要求：不公開影片仍可播放，絕對不可異動
        candidates = removal_candidates(build_report())
        self.assertNotIn("unl", [c["video_id"] for c in candidates])

    def test_candidates_carry_playlist_item_id_for_deletion(self):
        for candidate in removal_candidates(build_report()):
            self.assertTrue(candidate["playlist_item_id"].startswith("pi-"))


class TestSplitStillDead(unittest.TestCase):
    def test_resurrected_video_is_excluded_from_deletion(self):
        report = build_report()
        plan = [("清單", "PL-x", item) for item in removal_candidates(report)]

        # 二次驗證時 "pri" 又讀得到了（暫時性誤判）→ 必須剔除，只刪 "del"
        fresh_details = {"pri": detail("其實還活著")}
        dead, resurrected = split_still_dead(plan, fresh_details)

        self.assertEqual([c[2]["video_id"] for c in dead], ["del"])
        self.assertEqual([c[2]["video_id"] for c in resurrected], ["pri"])

    def test_all_still_dead_when_fresh_details_empty(self):
        plan = [("清單", "PL-x", item) for item in removal_candidates(build_report())]
        dead, resurrected = split_still_dead(plan, {})
        self.assertEqual(len(dead), 2)
        self.assertEqual(resurrected, [])


class TestWriteBackup(unittest.TestCase):
    def test_backup_file_contains_every_candidate(self):
        plan = [("我的清單", "PL-x", item) for item in removal_candidates(build_report())]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(playlist_cleaner, "LOG_DIR", Path(tmp)):
                path = write_backup(plan)
                content = path.read_text(encoding="utf-8")

        self.assertIn("https://youtu.be/pri", content)
        self.assertIn("https://youtu.be/del", content)
        self.assertIn("playlistItemId=pi-pri", content)  # 留底需含刪除所用的 ID
        self.assertIn("我的清單", content)


if __name__ == "__main__":
    unittest.main()
