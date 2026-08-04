"""quota_manager 持久化測試：同日續算、跨日歸零、壞檔容錯、熔斷不寫檔、多程序共用一致性。"""

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


class TestSharedStateAcrossProcesses(unittest.TestCase):
    """狀態檔被長駐伺服器、排程容器、CLI 工具同時讀寫時的一致性。

    每個 QuotaManager 實例代表一個獨立程序的視角。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_file = Path(self._tmp.name) / "quota_state.json"

    def _manager(self):
        return QuotaManager(daily_limit=10000, soft_limit=8000, state_file=self.state_file)

    def _write_state(self, quota_day, used):
        """模擬「其他程序」寫入狀態檔。"""
        self.state_file.write_text(
            json.dumps({"quota_day": quota_day, "used": used}), encoding="utf-8"
        )

    def test_long_running_manager_rolling_over_keeps_other_processes_usage(self):
        """本批修復的核心：長駐容器活過午夜，不得抹掉別人當日已記的帳。

        重現 HANDOFF 第 17 節：容器的 manager 建於 day-1，day-2 時其他程序
        已經記了 900，容器再 consume 時必須是 901 而不是 1。
        """
        with mock.patch(
            "youtube_toolkit.quota_manager._current_quota_day",
            side_effect=["2026-07-30", "2026-07-30", "2026-07-31"],
        ):
            container = self._manager()  # 第 1 次：建構於 day-1
            container.consume(478, "day-1 的用量")  # 第 2 次：仍是 day-1

            self._write_state("2026-07-31", 900)  # 換日後，其他程序已記了 900

            container.consume(1, "換日後第一次呼叫")  # 第 3 次：day-2

        self.assertEqual(container.used, 901)  # 不是 1——別人的 900 沒被洗掉
        self.assertEqual(container.quota_day, "2026-07-31")
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state, {"quota_day": "2026-07-31", "used": 901})

    def test_interleaved_consumes_accumulate_instead_of_overwriting(self):
        server, cli = self._manager(), self._manager()

        server.consume(10, "server-1")
        cli.consume(10, "cli-1")  # 應看見 server 的 10
        server.consume(10, "server-2")  # 應看見 cli 的 10
        cli.consume(10, "cli-2")

        self.assertEqual(cli.used, 40)
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["used"], 40)  # 舊版會是 20（兩邊各記各的、互相覆蓋）

    def test_fuse_sees_usage_spent_by_other_processes(self):
        """熔斷要用合併後的總量判斷，否則軟上限會晚跳——這正是這個 bug 的實際危害。"""
        manager = QuotaManager(daily_limit=100, soft_limit=80, state_file=self.state_file)
        self._write_state(manager.quota_day, 75)  # 其他程序已用掉 75

        with self.assertRaises(QuotaSoftLimitExceeded):
            manager.consume(10, "應被合併後的總量擋下")

    def test_does_not_regress_when_file_reports_less_than_memory(self):
        """檔案比記憶體小時不倒退（例如上一次寫檔失敗，或檔案被外力改小）。"""
        manager = self._manager()
        manager.consume(50, "已記帳")

        self._write_state(manager.quota_day, 5)

        manager.consume(1, "不該倒退成 6")
        self.assertEqual(manager.used, 51)

    def test_stale_file_from_previous_day_is_ignored_at_consume_time(self):
        """跨日判斷以讀檔當下為準：檔案停在昨天時，其計數不得被併進來。"""
        manager = self._manager()
        self._write_state("2000-01-01", 7777)

        manager.consume(10, "今天的第一筆")

        self.assertEqual(manager.used, 10)

    def test_corrupted_file_mid_run_falls_back_to_memory_without_crashing(self):
        manager = self._manager()
        manager.consume(50, "正常")

        self.state_file.write_text("壞掉的內容{{{", encoding="utf-8")

        manager.consume(10, "壞檔後仍可運作")  # 不得拋例外
        self.assertEqual(manager.used, 60)  # 沿用記憶體計數，不歸零
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["used"], 60)  # 並把壞檔覆寫回正常內容

    def test_unexpected_json_shape_is_treated_as_unreadable(self):
        manager = self._manager()
        manager.consume(50, "正常")

        self.state_file.write_text(json.dumps(["不是物件"]), encoding="utf-8")

        manager.consume(10, "格式不對也要撐住")
        self.assertEqual(manager.used, 60)

    def test_no_temp_files_left_behind(self):
        """暫存檔帶 PID 避免多程序踩踏，但用完必須換名成正式檔、不留殘骸。"""
        manager = self._manager()
        manager.consume(10, "寫一次")
        manager.consume(10, "再寫一次")

        leftovers = [p.name for p in Path(self._tmp.name).iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
