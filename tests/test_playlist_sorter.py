"""playlist_sorter 測試：以假 client 驗證 LIS 搬移執行、重試、熔斷與手動模式。"""

import json
import unittest
from unittest import mock

import httplib2
from googleapiclient.errors import HttpError

from youtube_toolkit.playlist_sorter import PlaylistSorter
from youtube_toolkit.quota_manager import QuotaManager, QuotaSoftLimitExceeded


def http_error(status, reason="backendError"):
    resp = httplib2.Response({"status": status})
    content = json.dumps(
        {"error": {"code": status, "message": reason, "errors": [{"reason": reason}]}}
    ).encode()
    return HttpError(resp, content)


def entry(item_id, video_id, title="song"):
    return {"playlist_item_id": item_id, "video_id": video_id, "title": title}


class FakeSorterClient:
    """只實作 PlaylistSorter 用到的介面；move 可依序注入例外。"""

    def __init__(self, entries, details, move_side_effects=None):
        self.quota_manager = QuotaManager(daily_limit=10000, soft_limit=8000)
        self._entries = entries
        self._details = details
        self._side_effects = list(move_side_effects or [])
        self.successful_moves = []
        self.move_attempts = 0

    def fetch_playlist_entries(self, playlist_id):
        return [dict(e) for e in self._entries]

    def fetch_video_details(self, video_ids):
        return {vid: dict(detail) for vid, detail in self._details.items()}

    def move_playlist_item(self, playlist_item_id, playlist_id, video_id, position):
        self.move_attempts += 1
        if self._side_effects:
            effect = self._side_effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
        self.successful_moves.append((playlist_item_id, position))


def make_client(channel_order, **kwargs):
    """依指定「目前頻道順序」建立假資料；理想順序為頻道字母序。"""
    entries = [entry(f"pi-{ch}", f"v-{ch}") for ch in channel_order]
    details = {f"v-{ch}": {"title": f"song-{ch}", "channel": ch, "views": 10} for ch in channel_order}
    return FakeSorterClient(entries, details, **kwargs)


@mock.patch("youtube_toolkit.playlist_sorter.time.sleep")  # 測試不真的等待
class TestPlaylistSorterRun(unittest.TestCase):
    def test_already_sorted_makes_no_move(self, _sleep):
        client = make_client(["a", "b", "c"])
        PlaylistSorter("PL-test", client).run(auto_run=True)
        self.assertEqual(client.move_attempts, 0)

    def test_moves_follow_lis_plan_and_produce_sorted_order(self, _sleep):
        # 目前順序 d,a,b,c → 理想 a,b,c,d：LIS 保留 a,b,c，只搬 d（1 次）
        client = make_client(["d", "a", "b", "c"])
        PlaylistSorter("PL-test", client).run(auto_run=True)

        self.assertEqual(client.successful_moves, [("pi-d", 3)])

        # 依 YouTube「移除→插入」語意重播搬移，結果必須是理想順序
        working = ["pi-d", "pi-a", "pi-b", "pi-c"]
        for item_id, position in client.successful_moves:
            working.remove(item_id)
            working.insert(position, item_id)
        self.assertEqual(working, ["pi-a", "pi-b", "pi-c", "pi-d"])

    def test_transient_http_error_is_retried_until_success(self, _sleep):
        client = make_client(["d", "a", "b", "c"], move_side_effects=[http_error(500), http_error(503)])
        PlaylistSorter("PL-test", client).run(auto_run=True)
        self.assertEqual(client.move_attempts, 3)  # 失敗 2 次後第 3 次成功
        self.assertEqual(client.successful_moves, [("pi-d", 3)])

    def test_permanent_failure_skips_item_and_continues(self, _sleep):
        # 目前順序 c,b,a 需要 2 步搬移；第一步連續 5 次 500 → 放棄，第二步照常執行
        client = make_client(["c", "b", "a"], move_side_effects=[http_error(500)] * 5)
        PlaylistSorter("PL-test", client).run(auto_run=True)
        self.assertEqual(client.move_attempts, 6)  # 5 次失敗 + 第二步 1 次成功
        self.assertEqual(len(client.successful_moves), 1)

    def test_non_retryable_http_error_gives_up_immediately(self, _sleep):
        client = make_client(["d", "a", "b", "c"], move_side_effects=[http_error(404, "notFound")])
        PlaylistSorter("PL-test", client).run(auto_run=True)
        self.assertEqual(client.move_attempts, 1)  # 404 不重試
        self.assertEqual(client.successful_moves, [])

    def test_soft_limit_fuse_aborts_whole_run(self, _sleep):
        client = make_client(["d", "a", "b", "c"], move_side_effects=[QuotaSoftLimitExceeded("軟上限")])
        with self.assertRaises(QuotaSoftLimitExceeded):
            PlaylistSorter("PL-test", client).run(auto_run=True)

    def test_hard_limit_quota_exceeded_is_reraised(self, _sleep):
        client = make_client(["d", "a", "b", "c"], move_side_effects=[http_error(403, "quotaExceeded")])
        with self.assertRaises(HttpError):
            PlaylistSorter("PL-test", client).run(auto_run=True)

    def test_manual_mode_requires_yes_confirmation(self, _sleep):
        client = make_client(["d", "a", "b", "c"])
        with mock.patch("builtins.input", return_value="no"):
            PlaylistSorter("PL-test", client).run(auto_run=False)
        self.assertEqual(client.move_attempts, 0)

        with mock.patch("builtins.input", return_value="YES"):
            PlaylistSorter("PL-test", client).run(auto_run=False)
        self.assertEqual(client.successful_moves, [("pi-d", 3)])

    def test_empty_playlist_is_skipped(self, _sleep):
        client = FakeSorterClient([], {})
        PlaylistSorter("PL-test", client).run(auto_run=True)
        self.assertEqual(client.move_attempts, 0)


if __name__ == "__main__":
    unittest.main()
