"""playlist_health 測試：私人／已刪除／不公開的分類與盤點（純資料，無網路）。"""

import unittest

from youtube_toolkit.playlist_health import (
    DELETED,
    PRIVATE,
    UNLISTED,
    audit_playlist,
    classify_entry,
    issue_count,
)


def entry(video_id, title="正常影片", privacy_status="public"):
    return {
        "playlist_item_id": f"pi-{video_id}",
        "video_id": video_id,
        "title": title,
        "privacy_status": privacy_status,
    }


def detail(title="正常影片", channel="頻道", views=100, privacy_status="public"):
    return {"title": title, "channel": channel, "views": views, "privacy_status": privacy_status}


class TestClassifyEntry(unittest.TestCase):
    def test_public_video_is_ok(self):
        self.assertEqual(classify_entry(entry("v1"), {"v1": detail()}), "ok")

    def test_missing_detail_with_private_marker_is_private(self):
        # videos.list 沒回傳 + 清單項目標記 private → 私人
        e = entry("v1", title="Private video", privacy_status="private")
        self.assertEqual(classify_entry(e, {}), PRIVATE)

    def test_missing_detail_with_private_title_only_is_private(self):
        e = entry("v1", title="Private video", privacy_status=None)
        self.assertEqual(classify_entry(e, {}), PRIVATE)

    def test_missing_detail_without_private_marker_is_deleted(self):
        e = entry("v1", title="Deleted video", privacy_status="privacyStatusUnspecified")
        self.assertEqual(classify_entry(e, {}), DELETED)

    def test_unlisted_video_detected_from_video_status(self):
        e = entry("v1")
        self.assertEqual(classify_entry(e, {"v1": detail(privacy_status="unlisted")}), UNLISTED)


class TestAuditPlaylist(unittest.TestCase):
    def test_mixed_playlist_report(self):
        entries = [
            entry("ok1"),
            entry("pri", title="Private video", privacy_status="private"),
            entry("del", title="Deleted video", privacy_status="privacyStatusUnspecified"),
            entry("unl"),
            entry("ok2"),
        ]
        details = {
            "ok1": detail("好歌"),
            "unl": detail("不公開的歌", privacy_status="unlisted"),
            "ok2": detail("另一首好歌"),
        }

        report = audit_playlist(entries, details)

        self.assertEqual(report["total"], 5)
        self.assertEqual(issue_count(report), 3)
        self.assertEqual([i["video_id"] for i in report[PRIVATE]], ["pri"])
        self.assertEqual([i["video_id"] for i in report[DELETED]], ["del"])
        self.assertEqual([i["video_id"] for i in report[UNLISTED]], ["unl"])

    def test_issue_items_carry_position_and_url(self):
        entries = [entry("ok1"), entry("pri", title="Private video", privacy_status="private")]
        report = audit_playlist(entries, {"ok1": detail()})

        item = report[PRIVATE][0]
        self.assertEqual(item["position"], 2)  # 清單中第 2 首
        self.assertEqual(item["url"], "https://youtu.be/pri")
        self.assertEqual(item["title"], "Private video")  # 原始標題已被 YouTube 抹除，只剩替代文字

    def test_unlisted_items_keep_real_title_and_channel(self):
        entries = [entry("unl")]
        report = audit_playlist(entries, {"unl": detail("還聽得到的歌", channel="某頻道", privacy_status="unlisted")})

        item = report[UNLISTED][0]
        self.assertEqual(item["title"], "還聽得到的歌")
        self.assertEqual(item["channel"], "某頻道")

    def test_healthy_playlist_has_zero_issues(self):
        entries = [entry("v1"), entry("v2")]
        details = {"v1": detail(), "v2": detail()}
        self.assertEqual(issue_count(audit_playlist(entries, details)), 0)


if __name__ == "__main__":
    unittest.main()
