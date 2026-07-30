"""共用 logger：console 彩色輸出 ＋ 檔案輪替日誌（logs/youtube_toolkit.log）。

檔案日誌不帶 ANSI 色碼，供無人值守執行出問題時追查；2MB × 5 份輪替。
"""

import copy
import logging
from logging.handlers import RotatingFileHandler

from youtube_toolkit import config

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
PURPLE = "\033[95m"
CYAN = "\033[96m"
RESET = "\033[0m"

LOG_COLORS_MAPPING = {
    logging.CRITICAL: PURPLE,
    logging.ERROR: RED,
    logging.WARNING: YELLOW,
    logging.INFO: GREEN,
    logging.DEBUG: CYAN,
}

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

LOG_DIR = config.BASE_DIR / "logs"
LOG_FILE = LOG_DIR / config.LOG_FILE_NAME
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5


class ColoredFormatter(logging.Formatter):
    """只為 console 上色。

    不就地修改 record（修改會污染同一筆 record 的其他 handler，
    例如讓 ANSI 色碼寫進檔案日誌），改在 record 的複本上著色。
    """

    def format(self, record):
        color = LOG_COLORS_MAPPING.get(record.levelno)
        if color is None:
            return super().format(record)
        colored_record = copy.copy(record)
        colored_record.msg = f"{color}{record.getMessage()}{RESET}"
        colored_record.args = None  # getMessage() 已套用過 args，避免二次格式化
        return super().format(colored_record)


logger = logging.getLogger("youtube_toolkit")
logger.setLevel(logging.DEBUG)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(ColoredFormatter(LOG_FORMAT, datefmt=DATE_FORMAT))
logger.addHandler(_console_handler)

try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    _file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(_file_handler)
except OSError as e:  # 例如唯讀目錄：檔案日誌失效不應阻止程式運作
    logger.warning(f"無法建立檔案日誌（僅保留 console 輸出）：{e}")

def set_console_level(level: int) -> None:
    """調整 console 輸出等級（檔案日誌不受影響，仍保留完整 DEBUG）。

    CLI 工具用來避免除錯訊息洗版；常駐服務維持預設的 DEBUG。
    """
    _console_handler.setLevel(level)


if __name__ == "__main__":
    logger.critical("這是一個致命錯誤訊息")
    logger.error("這是一個錯誤訊息")
    logger.warning("這是一個警告訊息")
    logger.info("這是一個資訊訊息")
    logger.debug("這是一個除錯訊息")
