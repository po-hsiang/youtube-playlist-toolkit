"""集中管理專案設定與機敏資訊。

機敏資訊一律來自環境變數或專案根目錄的 .env 檔（已被 .gitignore 排除），
原始碼中不得出現任何金鑰明碼。優先序：既有環境變數 > .env 檔 > 預設值。
"""

import os
from pathlib import Path

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
TOKEN_FILE = BASE_DIR / os.environ.get("TOKEN_FILE", "secrets/token.json")

# ── 執行期狀態檔（自動產生，已被 .gitignore 排除）──
QUOTA_STATE_FILE = BASE_DIR / "quota_state.json"
SORTER_STATE_FILE = BASE_DIR / "sorter_state.json"  # 記錄最後一次排序日期，避免重啟重複跑

# ── 一般設定（可用環境變數覆寫）────────────────
SCHEDULE_TIME = os.environ.get("SCHEDULE_TIME", "16:05")
YOUTUBE_DAILY_LIMIT = int(os.environ.get("YOUTUBE_DAILY_LIMIT", "10000"))
YOUTUBE_SOFT_LIMIT = int(os.environ.get("YOUTUBE_SOFT_LIMIT", "8000"))
OAUTH_PORT = int(os.environ.get("OAUTH_PORT", "8080"))

# ── MCP / REST 伺服器（yt-mcp）───────────────────
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")  # 容器內以環境變數改為 0.0.0.0
MCP_PORT = int(os.environ.get("MCP_PORT", "8765"))
MCP_CACHE_TTL_MINUTES = int(os.environ.get("MCP_CACHE_TTL_MINUTES", "360"))  # 快取 6 小時

# CLI 工具查詢 yt-mcp 伺服器的位址（走快取，0 配額）
MCP_BASE_URL = os.environ.get("MCP_BASE_URL", f"http://127.0.0.1:{MCP_PORT}")

# 發燒影片榜的預設地區（ISO 3166-1 alpha-2 國碼）；TW＝台灣榜，非全球榜
TRENDING_REGION = os.environ.get("TRENDING_REGION", "TW")

# 音訊抽取（/audio）允許的影片時長上限（秒），超過回 413 AUDIO_TOO_LONG
# 主人 2026-08-05 定案收在 70 分鐘：摘要是福利功能，先求穩，有需求再開
AUDIO_MAX_DURATION_SECONDS = int(os.environ.get("AUDIO_MAX_DURATION_SECONDS", "4200"))

# 音訊抽取的整體逾時（秒，含探測）。三層活門的最內層（bot 200 → n8n 190 → 本服務 180）：
# 本服務必須最先放棄，上游才收得到明確的 502 而不是斷線
AUDIO_TIMEOUT_SECONDS = int(os.environ.get("AUDIO_TIMEOUT_SECONDS", "180"))

# ── 檔案日誌 ─────────────────────────────────────
# 不同程序（主機工具／排序容器）應寫不同檔案，避免同時輪替互相干擾
LOG_FILE_NAME = os.environ.get("LOG_FILE_NAME", "youtube_toolkit.log")


def require_api_key() -> str:
    """取得 API Key；未設定時給出明確指引，而非在呼叫 API 時才神祕失敗。"""
    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "找不到 YOUTUBE_API_KEY。請將 .env.example 複製為 .env 並填入金鑰，"
            "或設定同名環境變數。"
        )
    return YOUTUBE_API_KEY
