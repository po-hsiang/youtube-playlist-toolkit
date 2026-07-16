"""YouTube Data API 配額管理：呼叫前預扣檢查，超過軟上限即熔斷。

計數可持久化到 JSON 狀態檔（傳入 state_file）：同一個「配額日」內重啟程式
不會歸零，所有工具共用同一份狀態檔即可合併計算整個帳號的用量。
配額日以太平洋時間為準（YouTube 每日配額於太平洋時間午夜重置）。

注意：狀態檔採「啟動時載入、每次消耗後覆寫」，多個程序同時執行時
以最後寫入者為準，可能輕微低估——個人工具可接受，軟上限緩衝已預留空間。
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from youtube_toolkit.log_utils import logger


def _pacific_timezone():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/Los_Angeles")
    except Exception:
        # Windows 沒有系統 IANA 時區資料庫（安裝 tzdata 套件可修正）時，
        # 退回固定 PST（UTC-8）：夏令期間換日比真實重置晚一小時，方向偏保守。
        logger.debug("[Quota] 找不到 IANA 時區資料庫，改用固定 UTC-8 判斷配額日。")
        return timezone(timedelta(hours=-8))


_PACIFIC = _pacific_timezone()


def _current_quota_day() -> str:
    """回傳目前配額日（太平洋時間的日期，ISO 格式字串）。"""
    return datetime.now(_PACIFIC).date().isoformat()


class QuotaSoftLimitExceeded(Exception):
    pass


class QuotaManager:
    def __init__(
            self,
            daily_limit: int = 10000,
            soft_limit: int = 8000,
            initial_used: int = 0,
            state_file: Optional[Path] = None,
    ):
        """state_file 為 None 時不持久化（單元測試用）；

        有給 state_file 且檔案屬於同一配額日時，載入的計數會覆蓋 initial_used。
        """
        self.daily_limit = daily_limit
        self.soft_limit = soft_limit
        self.state_file = Path(state_file) if state_file else None
        self.quota_day = _current_quota_day()
        self.used = initial_used
        self._load_state()
        logger.info(
            f"[Quota] Quota 管理器已啟動。軟上限 {self.soft_limit} / 硬上限 {self.daily_limit}，"
            f"配額日 {self.quota_day}（太平洋時間）已用 {self.used}"
        )

    def remaining_before_soft_limit(self) -> int:
        return max(self.soft_limit - self.used, 0)

    def remaining_before_hard_limit(self) -> int:
        return max(self.daily_limit - self.used, 0)

    def consume(self, cost: int, context: str):
        self._roll_over_if_new_day()
        if self.used + cost > self.soft_limit:
            message = (
                f"[Quota] 嘗試執行 {context} (成本: {cost})，"
                f"但累計 {self.used} + {cost} > Soft Limit {self.soft_limit}。"
                "為了保留配額，將停止本次作業。"
            )
            logger.warning(message)
            raise QuotaSoftLimitExceeded(message)
        self.used += cost
        self._save_state()
        logger.debug(f"[Quota] {context}: 消耗 {cost} 單位，目前累計 {self.used}/{self.daily_limit}。")

    # ── 持久化 ────────────────────────────────────────────

    def _roll_over_if_new_day(self) -> None:
        today = _current_quota_day()
        if today != self.quota_day:
            logger.info(f"[Quota] 進入新配額日 {today}（太平洋時間），計數歸零。")
            self.quota_day = today
            self.used = 0
            self._save_state()

    def _load_state(self) -> None:
        if not self.state_file or not self.state_file.exists():
            return
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"[Quota] 狀態檔讀取失敗，本日計數從 {self.used} 開始：{e}")
            return
        if state.get("quota_day") == self.quota_day:
            self.used = int(state.get("used", 0))
            logger.debug(f"[Quota] 從 {self.state_file} 載入本配額日既有計數：{self.used}")
        else:
            logger.debug(f"[Quota] 狀態檔屬於前一個配額日（{state.get('quota_day')}），重新計數。")

    def _save_state(self) -> None:
        if not self.state_file:
            return
        try:
            payload = json.dumps({"quota_day": self.quota_day, "used": self.used}, ensure_ascii=False)
            temp_file = self.state_file.with_name(self.state_file.name + ".tmp")
            temp_file.write_text(payload, encoding="utf-8")
            temp_file.replace(self.state_file)  # 同一磁碟區內為原子操作，避免寫到一半留下壞檔
        except OSError as e:
            logger.warning(f"[Quota] 狀態檔寫入失敗（不影響本次記憶體計數）：{e}")
