"""ANSI 彩色 console logger，全專案共用同一個 logger 實例。"""

import logging

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


class ColoredFormatter(logging.Formatter):
    def format(self, record):
        levelno = record.levelno
        if levelno in LOG_COLORS_MAPPING:
            record.msg = f"{LOG_COLORS_MAPPING[levelno]}{record.msg}{RESET}"
        return super().format(record)


formatter = ColoredFormatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logger.addHandler(handler)

if __name__ == "__main__":
    logger.critical("這是一個致命錯誤訊息")
    logger.error("這是一個錯誤訊息")
    logger.warning("這是一個警告訊息")
    logger.info("這是一個資訊訊息")
    logger.debug("這是一個除錯訊息")
