"""quota_manager 持久化測試：同日續算、跨日歸零、壞檔容錯、熔斷不寫檔。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from youtube_toolkit.quota_manager import QuotaManager, QuotaSoftLimitExceeded


class TestQuotaPersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_file = Path(self._tmp.name) / "quota_state.json"

    def test_count_survives_restart_within_same_day(self):
        first = QuotaManager(daily_limit=100, soft_limit=80, state_file=self.state_file)
        first.consume(30, "run-1")

        second = QuotaManager(daily_limit=100, soft_limit=80, state_file=self.state_file)
        self.assertEqual(second.used, 30)  # 重啟不歸零——這批修正的核心目標

        second.consume(20, "run-2")
        third = QuotaManager(daily_limit=100, soft_limit=80, state_file=self.state_file)
        self.assertEqual(third.used, 50)

    def test_state_from_previous_quota_day_is_discarded(self):
        self.state_file.write_text(
            json.dumps({"quota_day": "2000-01-01", "used": 7777}), encoding="utf-8"
        )
        manager = QuotaManager(daily_limit=100, soft_limit=80, state_file=self.state_file)
        self.assertEqual(manager.used, 0)

    def test_rollover_resets_count_mid_run(self):
        with mock.patch(
            "youtube_toolkit.quota_manager._current_quota_day",
            side_effect=["2026-07-15", "2026-07-15", "2026-07-16"],
        ):
            manager = QuotaManager(daily_limit=100, soft_limit=80, state_file=self.state_file)  # 第 1 次呼叫
            manager.consume(30, "day-1")  # 第 2 次：同日，累計 30
            manager.consume(30, "day-2")  # 第 3 次：換日，先歸零再累計
        self.assertEqual(manager.used, 30)
        self.assertEqual(manager.quota_day, "2026-07-16")
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state, {"quota_day": "2026-07-16", "used": 30})

    def test_corrupted_state_file_starts_from_zero(self):
        self.state_file.write_text("not-json{{{", encoding="utf-8")
        manager = QuotaManager(daily_limit=100, soft_limit=80, state_file=self.state_file)
        self.assertEqual(manager.used, 0)
        manager.consume(10, "recovered")  # 之後仍可正常覆寫
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["used"], 10)

    def test_fuse_does_not_persist_rejected_cost(self):
        manager = QuotaManager(daily_limit=100, soft_limit=80, state_file=self.state_file)
        manager.consume(50, "ok")
        with self.assertRaises(QuotaSoftLimitExceeded):
            manager.consume(50, "blocked")
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["used"], 50)  # 被熔斷的成本不得寫入狀態檔

    def test_no_state_file_means_no_persistence(self):
        manager = QuotaManager(daily_limit=100, soft_limit=80)
        manager.consume(10, "in-memory")
        self.assertFalse(self.state_file.exists())


if __name__ == "__main__":
    unittest.main()
