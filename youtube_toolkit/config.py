"""集中管理專案設定與機敏資訊。

機敏資訊一律來自環境變數或專案根目錄的 .env 檔（已被 .gitignore 排除），
原始碼中不得出現任何金鑰明碼。優先序：既有環境變數 > .env 檔 > 預設值。
"""

import os
import sys
from pathlib import Path

# PyInstaller 打包後 __file__ 位於暫存目錄，改以執行檔所在位置為根目錄
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parents[1]

ENV_FILE = BASE_DIR / ".env"
SECRETS_DIR = BASE_DIR / "secrets"


def _load_env_file(path: Path) -> None:
    """極簡 .env 載入器：支援 KEY=VALUE、# 註解與空行，不覆蓋既有環境變數。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(ENV_FILE)

# ── 機敏資訊 ──────────────────────────────────
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

# ── 憑證檔路徑（可用環境變數覆寫；相對路徑以專案根目錄為基準）──
CLIENT_SECRET_FILE = BASE_DIR / os.environ.get("CLIENT_SECRET_FILE", "secrets/client_secret.json")
TOKEN_FILE = BASE_DIR / os.environ.get("TOKEN_FILE", "secrets/token.pickle")

# ── 一般設定（可用環境變數覆寫）────────────────
SCHEDULE_TIME = os.environ.get("SCHEDULE_TIME", "16:05")
YOUTUBE_DAILY_LIMIT = int(os.environ.get("YOUTUBE_DAILY_LIMIT", "10000"))
YOUTUBE_SOFT_LIMIT = int(os.environ.get("YOUTUBE_SOFT_LIMIT", "8000"))
OAUTH_PORT = int(os.environ.get("OAUTH_PORT", "8080"))


def require_api_key() -> str:
    """取得 API Key；未設定時給出明確指引，而非在呼叫 API 時才神祕失敗。"""
    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "找不到 YOUTUBE_API_KEY。請將 .env.example 複製為 .env 並填入金鑰，"
            "或設定同名環境變數。"
        )
    return YOUTUBE_API_KEY
