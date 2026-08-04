"""YouTube Data API 配額管理：呼叫前預扣檢查，超過軟上限即熔斷。

計數可持久化到 JSON 狀態檔（傳入 state_file）：同一個「配額日」內重啟程式
不會歸零，所有工具共用同一份狀態檔即可合併計算整個帳號的用量。
配額日以太平洋時間為準（YouTube 每日配額於太平洋時間午夜重置）。

**多程序共用的一致性**：狀態檔會被長駐的 yt-music-mcp 伺服器、每日排程的
yt-sorter、以及各 CLI 工具同時讀寫，因此每次 consume() 都會重新讀檔對齊
（read-modify-write），而不是只信任建構時載入的記憶體快照。跨配額日的判斷
也一律以「讀檔當下」的日期為準。

  歷史教訓：舊版只在建構時載入，長駐容器的 QuotaManager 活過午夜後，
  會用自己的舊視角把狀態檔歸零，抹掉其他程序當日已記的帳（實測少記約 900 units，
  導致軟上限熔斷晚跳）。詳見 HANDOFF.md 第 17 節。

殘留的競態：沒有跨程序檔案鎖，兩個程序在同一瞬間 consume 仍可能掉一次遞增
（最壞低估一筆的成本）。對個人工具而言，這比引入鎖的複雜度划算——寫入本身
是原子的（暫存檔 + os.replace），暫存檔以 PID 命名避免互相踩踏。
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

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

        有給 state_file 且檔案屬於同一配額日時，會取檔案與 initial_used 的較大值。
        """
        self.daily_limit = daily_limit
        self.soft_limit = soft_limit
        self.state_file = Path(state_file) if state_file else None
        self.quota_day = ""  # 由 _sync_with_state_file() 依「現在」的配額日設定
        self.used = initial_used
        self.session_used = 0  # 只算本實例消耗的量；used 是全帳號合併值，兩者用途不同
        self._read_failed = False  # 壞檔時只警告一次，避免每次 consume 洗版
        self._sync_with_state_file()
        logger.info(
            f"[Quota] Quota 管理器已啟動。軟上限 {self.soft_limit} / 硬上限 {self.daily_limit}，"
            f"配額日 {self.quota_day}（太平洋時間）已用 {self.used}"
        )

    def remaining_before_soft_limit(self) -> int:
        return max(self.soft_limit - self.used, 0)

    def remaining_before_hard_limit(self) -> int:
        return max(self.daily_limit - self.used, 0)

    def consume(self, cost: int, context: str):
        self._sync_with_state_file()  # 先對齊其他程序的用量與今天的配額日，再判斷熔斷
        if self.used + cost > self.soft_limit:
            message = (
                f"[Quota] 嘗試執行 {context} (成本: {cost})，"
                f"但累計 {self.used} + {cost} > Soft Limit {self.soft_limit}。"
                "為了保留配額，將停止本次作業。"
            )
            logger.warning(message)
            raise QuotaSoftLimitExceeded(message)
        self.used += cost
        self.session_used += cost  # 「本次作業消耗」的報表要用這個，used 會含其他程序的量
        self._save_state()
        logger.debug(f"[Quota] {context}: 消耗 {cost} 單位，目前累計 {self.used}/{self.daily_limit}。")

    # ── 持久化 ────────────────────────────────────────────

    def _sync_with_state_file(self) -> None:
        """以「現在」的配額日為準，重新對齊記憶體計數與狀態檔。

        兩份資料的任一方都可能過期，因此分兩步：
        1. 換日 → 記憶體先歸零（自己的視角過期了，長駐程序活過午夜就是這種情況）
        2. 檔案屬於今天 → 取兩者**較大值**

        取較大值而非直接覆蓋，是為了兩種都會真實發生的情況：
        - 其他程序已經消耗過 → 檔案較大，採用檔案（這就是本次修的 bug）
        - 自己剛消耗、或上次 _save_state() 寫檔失敗 → 記憶體較大，不能倒退
        """
        today = _current_quota_day()
        if self.quota_day and today != self.quota_day:
            logger.info(f"[Quota] 進入新配額日 {today}（太平洋時間），計數歸零。")
            self.used = 0
        self.quota_day = today

        persisted_day, persisted_used = self._read_state()
        if persisted_day is None:
            return
        if persisted_day != today:
            logger.debug(f"[Quota] 狀態檔屬於前一個配額日（{persisted_day}），忽略其計數。")
            return
        if persisted_used > self.used:
            logger.debug(f"[Quota] 併入其他程序已記的用量：{self.used} → {persisted_used}")
            self.used = persisted_used

    def _read_state(self) -> Tuple[Optional[str], int]:
        """讀取狀態檔，回傳 (配額日, 已用量)；沒有檔案或內容壞掉時回 (None, 0)。

        讀檔失敗一律降級為「沿用記憶體計數」，絕不讓長駐伺服器因為一個壞檔而中斷。
        """
        if not self.state_file or not self.state_file.exists():
            return None, 0
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            result = (state.get("quota_day"), int(state.get("used", 0)))
        except (OSError, ValueError, TypeError, AttributeError) as e:
            if not self._read_failed:  # 壞檔會每次 consume 都踩到，只警告第一次
                logger.warning(f"[Quota] 狀態檔讀取失敗，沿用記憶體計數 {self.used}：{e}")
                self._read_failed = True
            return None, 0
        self._read_failed = False
        return result

    def _save_state(self) -> None:
        if not self.state_file:
            return
        try:
            payload = json.dumps({"quota_day": self.quota_day, "used": self.used}, ensure_ascii=False)
            # 暫存檔帶 PID：多個程序同時寫入時不會互相踩到對方的暫存檔
            temp_file = self.state_file.with_name(f"{self.state_file.name}.{os.getpid()}.tmp")
            temp_file.write_text(payload, encoding="utf-8")
            temp_file.replace(self.state_file)  # 同一磁碟區內為原子操作，避免寫到一半留下壞檔
        except OSError as e:
            logger.warning(f"[Quota] 狀態檔寫入失敗（不影響本次記憶體計數）：{e}")
