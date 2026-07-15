"""YouTube Data API 配額管理：呼叫前預扣檢查，超過軟上限即熔斷。"""

from youtube_toolkit.log_utils import logger


class QuotaSoftLimitExceeded(Exception):
    pass


class QuotaManager:
    def __init__(
            self,
            daily_limit: int = 10000,
            soft_limit: int = 8000,
            initial_used: int = 0,
    ):
        self.daily_limit = daily_limit
        self.soft_limit = soft_limit
        self.used = initial_used
        logger.info(f"[Quota] Quota 管理器已啟動。軟上限 {self.soft_limit} / 硬上限 {self.daily_limit}")

    def remaining_before_soft_limit(self) -> int:
        return max(self.soft_limit - self.used, 0)

    def remaining_before_hard_limit(self) -> int:
        return max(self.daily_limit - self.used, 0)

    def consume(self, cost: int, context: str):
        if self.used + cost > self.soft_limit:
            message = (
                f"[Quota] 嘗試執行 {context} (成本: {cost})，"
                f"但累計 {self.used} + {cost} > Soft Limit {self.soft_limit}。"
                "為了保留配額，將停止本次作業。"
            )
            logger.warning(message)
            raise QuotaSoftLimitExceeded(message)
        self.used += cost
        logger.debug(f"[Quota] {context}: 消耗 {cost} 單位，目前累計 {self.used}/{self.daily_limit}。")
