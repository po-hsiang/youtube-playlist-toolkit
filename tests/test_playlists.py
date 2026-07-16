"""playlists 設定載入器測試：正常載入、錯誤訊息要能引導使用者修設定。"""

import tempfile
import unittest
from pathlib import Path

from youtube_toolkit import playlists

VALID_TOML = """
[playlists]
"Live" = "PL-live"
"YTMusic" = "PL-ytmusic"

[sorter]
order = ["Live", "YTMusic"]

[playlist_search]
target = "YTMusic"
"""


class TestPlaylistsLoader(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.toml_path = Path(self._tmp.name) / "playlists.toml"

    def _write(self, content: str) -> Path:
        self.toml_path.write_text(content, encoding="utf-8")
        return self.toml_path

    def test_load_all(self):
        path = self._write(VALID_TOML)
        self.assertEqual(playlists.load_all(path), {"Live": "PL-live", "YTMusic": "PL-ytmusic"})

    def test_get_playlist_id_by_name(self):
        path = self._write(VALID_TOML)
        self.assertEqual(playlists.get_playlist_id("Live", path), "PL-live")

    def test_unknown_name_lists_available_names(self):
        path = self._write(VALID_TOML)
        with self.assertRaises(KeyError) as ctx:
            playlists.get_playlist_id("不存在", path)
        self.assertIn("YTMusic", str(ctx.exception))  # 錯誤訊息要列出可用名稱

    def test_sorter_playlists_keeps_configured_order(self):
        path = self._write(VALID_TOML)
        self.assertEqual(
            playlists.sorter_playlists(path),
            [("Live", "PL-live"), ("YTMusic", "PL-ytmusic")],
        )

    def test_sorter_order_with_undefined_name_raises(self):
        path = self._write(VALID_TOML.replace('order = ["Live", "YTMusic"]', 'order = ["Live", "打錯字"]'))
        with self.assertRaises(ValueError) as ctx:
            playlists.sorter_playlists(path)
        self.assertIn("打錯字", str(ctx.exception))

    def test_tool_target(self):
        path = self._write(VALID_TOML)
        self.assertEqual(playlists.tool_target("playlist_search", path), ("YTMusic", "PL-ytmusic"))

    def test_missing_target_section_raises(self):
        path = self._write(VALID_TOML)
        with self.assertRaises(ValueError):
            playlists.tool_target("duplicate_finder", path)  # VALID_TOML 沒有這個區段

    def test_missing_file_raises_with_path(self):
        missing = Path(self._tmp.name) / "nope.toml"
        with self.assertRaises(FileNotFoundError) as ctx:
            playlists.load_all(missing)
        self.assertIn("nope.toml", str(ctx.exception))

    def test_invalid_toml_raises_value_error(self):
        path = self._write("[playlists\n糟糕")
        with self.assertRaises(ValueError):
            playlists.load_all(path)

    def test_real_project_file_is_consistent(self):
        """實際的 playlists.toml 必須通過驗證（sorter order、各工具 target 都指向存在的清單）。"""
        self.assertEqual(len(playlists.sorter_playlists()), 13)
        playlists.tool_target("playlist_search")
        playlists.tool_target("duplicate_finder")


if __name__ == "__main__":
    unittest.main()
