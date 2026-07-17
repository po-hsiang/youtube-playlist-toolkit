"""auth 測試：無人值守模式（interactive=False）絕不可觸發瀏覽器授權流程。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from youtube_toolkit import auth


class TestNonInteractiveAuth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.token_path = Path(self._tmp.name) / "token.json"
        patcher = mock.patch("youtube_toolkit.config.TOKEN_FILE", self.token_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_token_raises_instead_of_opening_browser(self):
        with self.assertRaises(auth.ReauthorizationRequired) as ctx:
            auth.get_oauth_credentials(auth.OAUTH_SCOPES, interactive=False)
        self.assertIn("yt-sort --once", str(ctx.exception))  # 錯誤訊息要告訴主人怎麼修

    def test_corrupted_token_raises_in_unattended_mode(self):
        self.token_path.write_text("這不是 JSON", encoding="utf-8")
        with self.assertRaises(auth.ReauthorizationRequired):
            auth.get_oauth_credentials(auth.OAUTH_SCOPES, interactive=False)

    def test_valid_cached_token_works_without_interaction(self):
        self.token_path.write_text(
            json.dumps(
                {
                    "token": "access-token",
                    "refresh_token": "refresh-token",
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    # 沒有 expiry 會被新版 google-auth 視為已過期而觸發真實網路刷新
                    "expiry": "2099-01-01T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        credentials = auth.get_oauth_credentials(auth.OAUTH_SCOPES, interactive=False)
        self.assertTrue(credentials.valid)  # 有效快取：無人值守也能直接用


if __name__ == "__main__":
    unittest.main()
