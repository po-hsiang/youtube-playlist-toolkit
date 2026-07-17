"""認證中心：集中管理 API Key 服務與 OAuth 2.0 憑證。

OAuth 憑證以 JSON 格式快取（google-auth 官方的 to_json / from_authorized_user_file），
不再使用 pickle——JSON 跨 Python／套件版本穩定，也沒有反序列化執行任意程式碼的風險。
"""

from typing import List, Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from youtube_toolkit import config
from youtube_toolkit.log_utils import logger

OAUTH_SCOPES = ["https://www.googleapis.com/auth/youtube"]

API_SERVICE_NAME = "youtube"
API_VERSION = "v3"


class ReauthorizationRequired(Exception):
    """憑證失效且處於無人值守模式：不可啟動瀏覽器授權（會讓排程 daemon 卡死）。"""


def build_public_service() -> Resource:
    """以 API Key 建立 YouTube 服務，適用於讀取公開資料（搜尋、公開清單）。"""
    return build(API_SERVICE_NAME, API_VERSION, developerKey=config.require_api_key())


def build_oauth_service(scopes: Optional[List[str]] = None, interactive: bool = True) -> Resource:
    """以 OAuth 2.0 使用者憑證建立 YouTube 服務，適用於修改播放清單。

    interactive=False（排程等無人值守情境）時，若需要重新授權不會開瀏覽器，
    改拋出 ReauthorizationRequired 讓呼叫端記錯誤、下輪再試。
    """
    credentials = get_oauth_credentials(scopes or OAUTH_SCOPES, interactive=interactive)
    return build(API_SERVICE_NAME, API_VERSION, credentials=credentials)


def get_oauth_credentials(scopes: List[str], interactive: bool = True) -> Credentials:
    """取得有效的 OAuth 憑證：快取 → 過期就刷新 → 刷新失敗才重走瀏覽器授權。"""
    _warn_if_legacy_pickle_exists()

    credentials = _load_cached_credentials(scopes)

    if credentials and credentials.expired and credentials.refresh_token:
        logger.info("憑證已過期，正在透過 Refresh Token 更新...")
        try:
            credentials.refresh(Request())
            _save_credentials(credentials)
            logger.info("Access Token 更新成功。")
        except RefreshError as e:
            logger.error(f"Refresh Token 已失效，需要重新授權：{e}")
            credentials = None

    if not credentials or not credentials.valid:
        if not interactive:
            raise ReauthorizationRequired(
                f"OAuth 憑證無效或已失效（{config.TOKEN_FILE}），目前為無人值守模式，"
                "不啟動瀏覽器授權。請手動執行一次 `uv run yt-sort --once` 完成重新授權。"
            )
        credentials = _run_authorization_flow(scopes)
        _save_credentials(credentials)

    return credentials


def _load_cached_credentials(scopes: List[str]) -> Optional[Credentials]:
    token_path = config.TOKEN_FILE
    if not token_path.exists():
        return None
    logger.debug(f"從 {token_path} 載入憑證...")
    try:
        return Credentials.from_authorized_user_file(str(token_path), scopes=scopes)
    except ValueError as e:  # 非 JSON 內容或欄位缺漏
        logger.warning(f"憑證快取無法解析，將重新授權：{e}")
        return None


def _save_credentials(credentials: Credentials) -> None:
    token_path = config.TOKEN_FILE
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    logger.info(f"憑證已儲存至 {token_path}。")


def _run_authorization_flow(scopes: List[str]) -> Credentials:
    if not config.CLIENT_SECRET_FILE.exists():
        raise FileNotFoundError(
            f"找不到 OAuth 用戶端密鑰檔：{config.CLIENT_SECRET_FILE}，"
            "請參考 README「憑證設定」一節。"
        )
    logger.warning(f"無有效憑證，正在啟動瀏覽器 OAuth 流程 (Scope: {scopes})...")
    flow = InstalledAppFlow.from_client_secrets_file(str(config.CLIENT_SECRET_FILE), scopes=scopes)
    return flow.run_local_server(
        port=config.OAUTH_PORT,
        prompt="consent",
        authorization_prompt_message="請在瀏覽器中授權本應用程式存取您的 YouTube 播放清單",
    )


def _warn_if_legacy_pickle_exists() -> None:
    legacy_path = config.SECRETS_DIR / "token.pickle"
    if legacy_path.exists() and legacy_path != config.TOKEN_FILE:
        logger.info(f"偵測到舊版憑證快取 {legacy_path}（pickle 格式已停用），確認新憑證可用後即可手動刪除。")
