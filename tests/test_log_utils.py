"""log_utils 測試：console 上色不得污染同一筆 record 的其他 handler（檔案日誌）。"""

import logging
import unittest
from logging.handlers import RotatingFileHandler

from youtube_toolkit import log_utils


def make_record(level=logging.INFO, msg="測試訊息 hello"):
    return logging.LogRecord("test", level, __file__, 1, msg, None, None)


class TestColoredFormatter(unittest.TestCase):
    def test_console_output_is_colored(self):
        formatted = log_utils.ColoredFormatter("%(message)s").format(make_record(logging.INFO))
        self.assertIn(log_utils.GREEN, formatted)
        self.assertIn(log_utils.RESET, formatted)

    def test_record_is_not_mutated_so_file_output_stays_clean(self):
        # 重構前的 bug：formatter 就地改 record.msg，色碼會外洩到其他 handler
        record = make_record(logging.WARNING)
        log_utils.ColoredFormatter("%(message)s").format(record)  # 先給 console formatter 處理

        plain = logging.Formatter("%(message)s").format(record)  # 再給檔案 formatter 處理
        self.assertNotIn("\033", plain)
        self.assertEqual(record.msg, "測試訊息 hello")

    def test_unknown_level_falls_back_to_plain(self):
        record = make_record(level=5)  # 自訂 level，不在顏色表內
        formatted = log_utils.ColoredFormatter("%(message)s").format(record)
        self.assertNotIn("\033", formatted)


class TestLoggerSetup(unittest.TestCase):
    def test_logger_has_console_and_rotating_file_handlers(self):
        kinds = [type(handler) for handler in log_utils.logger.handlers]
        self.assertIn(logging.StreamHandler, kinds)
        self.assertIn(RotatingFileHandler, kinds)

    def test_file_handler_has_no_color_formatter(self):
        for handler in log_utils.logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                self.assertNotIsInstance(handler.formatter, log_utils.ColoredFormatter)


if __name__ == "__main__":
    unittest.main()
