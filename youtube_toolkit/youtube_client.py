"""YouTube Data API 共用資料存取層。

三個工具原本各自複製了「分頁抓清單 → 批次抓詳情」的邏輯，本模組將其收斂為一份，
並保證**所有** API 呼叫都經過 QuotaManager 記帳：超過軟上限即拋出
QuotaSoftLimitExceeded 熔斷，避免燒光每日 10,000 units 的免費配額。

googleapiclient 底層 (httplib2) 內建 60 秒逾時與標準 HttpError 錯誤，
取代先前手刻、無逾時保護的 requests 呼叫。
"""

import random
import time
from typing import Any, Dict, List, Optional

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from youtube_toolkit import auth, config
from youtube_toolkit.log_utils import logger
from youtube_toolkit.quota_manager import QuotaManager


class QuotaCost:
    """各 API 操作的配額成本（units），來源：YouTube Data API 官方配額表。"""

    LIST = 1  # playlistItems.list / videos.list（每頁／每批）
    UPDATE = 50  # playlistItems.update（搬移一個項目）
    DELETE = 50  # playlistItems.delete（從清單移除一個項目）
    SEARCH = 100  # search.list（每次呼叫）


MAX_RESULTS_PER_PAGE = 50  # YouTube API 單頁／單批上限

# 讀取路徑的暫時性錯誤重試（寫入的重試政策由 playlist_sorter 控制，含 409）
READ_RETRY_STATUS = (500, 502, 503)
MAX_READ_ATTEMPTS = 3
READ_RETRY_BASE_DELAY = 1.0  # 秒，指數退避


class YouTubeClient:
    """包裝 googleapiclient 服務物件，提供帶配額記帳的高階操作。

    以建構子注入 service 與 quota_manager，方便測試時以假物件替換。
    """

    def __init__(self, service: Resource, quota_manager: Optional[QuotaManager] = None):
        self._service = service
        self.quota_manager = quota_manager or QuotaManager(
            daily_limit=config.YOUTUBE_DAILY_LIMIT,
            soft_limit=config.YOUTUBE_SOFT_LIMIT,
            state_file=config.QUOTA_STATE_FILE,  # 跨程序共用計數：全部工具合併記帳
        )

    @classmethod
    def for_public_data(cls, quota_manager: Optional[QuotaManager] = None) -> "YouTubeClient":
        """API Key 認證：讀取公開資料（搜尋、公開播放清單）。"""
        return cls(auth.build_public_service(), quota_manager)

    @classmethod
    def for_authorized_user(
        cls, quota_manager: Optional[QuotaManager] = None, interactive: bool = True
    ) -> "YouTubeClient":
        """OAuth 2.0 認證：修改使用者自己的播放清單。

        interactive=False 時若需重新授權會拋 auth.ReauthorizationRequired，不開瀏覽器。
        """
        return cls(auth.build_oauth_service(interactive=interactive), quota_manager)

    def _execute_with_retry(self, request: Any, cost: int, context: str) -> Dict[str, Any]:
        """執行讀取請求，對暫時性 HTTP 錯誤（500/502/503）指數退避重試。

        每次嘗試都個別記帳——失敗的請求同樣消耗 Google 端配額，計數保守才安全。
        """
        for attempt in range(MAX_READ_ATTEMPTS):
            self.quota_manager.consume(cost=cost, context=context)
            try:
                return request.execute()
            except HttpError as e:
                if e.resp.status in READ_RETRY_STATUS and attempt < MAX_READ_ATTEMPTS - 1:
                    delay = READ_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"[重試 {attempt + 1}/{MAX_READ_ATTEMPTS}] {context} 失敗（HTTP {e.resp.status}），"
                        f"{delay:.1f} 秒後重試..."
                    )
                    time.sleep(delay)
                    continue
                raise

    # ── 讀取 ──────────────────────────────────────────────

    def fetch_playlist_entries(self, playlist_id: str) -> List[Dict[str, Any]]:
        """抓取播放清單全部項目（自動分頁），包含已私人／已刪除的項目。

        回傳依清單順序排列的 [{"playlist_item_id", "video_id", "title", "privacy_status"}]。
        注意：私人／已刪除影片的 title 會被 YouTube 改為 "Private video" / "Deleted video"。
        """
        logger.debug(f"開始抓取播放清單【{playlist_id}】的項目...")
        entries: List[Dict[str, Any]] = []
        page_token = None
        while True:
            request = self._service.playlistItems().list(
                part="snippet,status",
                playlistId=playlist_id,
                maxResults=MAX_RESULTS_PER_PAGE,
                pageToken=page_token,
            )
            response = self._execute_with_retry(request, QuotaCost.LIST, "playlistItems.list")
            for item in response.get("items", []):
                resource = item["snippet"]["resourceId"]
                if resource.get("kind") != "youtube#video":
                    continue
                entries.append(
                    {
                        "playlist_item_id": item["id"],
                        "video_id": resource["videoId"],
                        "title": item["snippet"]["title"],
                        "privacy_status": item.get("status", {}).get("privacyStatus"),
                    }
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        logger.info(f"抓取完畢，共 {len(entries)} 個項目。")
        return entries

    def fetch_video_details(self, video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """批次抓取影片詳情（每批 50 部），

        回傳 {video_id: {"title", "channel", "views", "privacy_status"}}。
        已刪除或私人影片不會出現在回傳結果中（呼叫端請以 .get() 取值）。
        """
        logger.debug(f"正在批次抓取 {len(video_ids)} 部影片的詳細資訊...")
        details: Dict[str, Dict[str, Any]] = {}
        for start in range(0, len(video_ids), MAX_RESULTS_PER_PAGE):
            batch = video_ids[start : start + MAX_RESULTS_PER_PAGE]
            request = self._service.videos().list(part="snippet,statistics,status", id=",".join(batch))
            response = self._execute_with_retry(
                request, QuotaCost.LIST, f"videos.list (batch {start // MAX_RESULTS_PER_PAGE + 1})"
            )
            for item in response.get("items", []):
                statistics = item.get("statistics")
                if not statistics:  # 私人／已下架影片沒有統計資料
                    continue
                snippet = item.get("snippet", {})
                details[item["id"]] = {
                    "title": snippet.get("title", "N/A"),
                    "channel": snippet.get("channelTitle", "N/A"),
                    "views": int(statistics.get("viewCount", 0)),
                    "privacy_status": item.get("status", {}).get("privacyStatus"),
                }
        logger.info(f"影片詳細資訊抓取完畢（{len(details)}/{len(video_ids)} 部有資料）。")
        return details

    def fetch_video(self, video_id: str) -> Optional[Dict[str, Any]]:
        """抓取單一影片的完整中繼資料（videos.list，成本 1 unit）。

        回傳原始 API item（整形交給 video_info 模組）；影片不存在或為私人時
        videos.list 會回空 items，此時回傳 None。
        """
        request = self._service.videos().list(
            part="snippet,statistics,contentDetails", id=video_id
        )
        response = self._execute_with_retry(request, QuotaCost.LIST, f"videos.list ({video_id})")
        items = response.get("items", [])
        return items[0] if items else None

    def fetch_playlist_videos(self, playlist_id: str) -> List[Dict[str, Any]]:
        """一次取得清單內全部影片與詳情（依清單順序）。

        回傳 [{"video_id", "title", "channel", "views", "url"}]；
        無詳情的影片（已刪除／私人）會記 log 後跳過。
        """
        entries = self.fetch_playlist_entries(playlist_id)
        details = self.fetch_video_details([entry["video_id"] for entry in entries])

        videos: List[Dict[str, Any]] = []
        for entry in entries:
            detail = details.get(entry["video_id"])
            if detail is None:
                logger.debug(f"跳過無詳情的影片（可能已刪除或設為私人）：{entry['title']} ({entry['video_id']})")
                continue
            videos.append(
                {
                    "video_id": entry["video_id"],
                    "url": f"https://youtu.be/{entry['video_id']}",
                    **detail,
                }
            )
        return videos

    def fetch_most_popular(
        self, region_code: str, max_results: int, category_id: str = ""
    ) -> List[Dict[str, Any]]:
        """抓取指定地區的發燒影片榜（videos.list chart=mostPopular，成本 1 unit）。

        回傳原始 API items（整形交給 trending 模組），最多 50 筆——這是單頁上限，
        再多要分頁而榜單前 50 名以外意義不大。category_id 留空＝不分類別。

        注意：類別 ID 合法不代表該地區有榜（實測 TW 的 29 非營利回 404、
        不存在的 ID 回 400），呼叫端需自行處理 HttpError。
        """
        params: Dict[str, Any] = {
            "part": "snippet,statistics,contentDetails",
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": min(max_results, MAX_RESULTS_PER_PAGE),
        }
        if category_id:
            params["videoCategoryId"] = category_id
        request = self._service.videos().list(**params)
        response = self._execute_with_retry(
            request, QuotaCost.LIST, f"videos.list (mostPopular {region_code}/{category_id or 'all'})"
        )
        return response.get("items", [])

    # ── 寫入 ──────────────────────────────────────────────

    def move_playlist_item(
        self, playlist_item_id: str, playlist_id: str, video_id: str, position: int
    ) -> None:
        """把清單項目搬到指定位置（0-based，成本 50 units）。"""
        self.quota_manager.consume(cost=QuotaCost.UPDATE, context=f"playlistItems.update (pos {position})")
        body = {
            "id": playlist_item_id,
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
                "position": position,
            },
        }
        self._service.playlistItems().update(part="snippet", body=body).execute()

    def delete_playlist_item(self, playlist_item_id: str) -> None:
        """把項目從播放清單移除（成本 50 units）。只影響清單，不影響影片本身。"""
        self.quota_manager.consume(
            cost=QuotaCost.DELETE, context=f"playlistItems.delete ({playlist_item_id})"
        )
        self._service.playlistItems().delete(id=playlist_item_id).execute()

    # ── 搜尋 ──────────────────────────────────────────────

    def search_videos(self, **params: Any) -> Dict[str, Any]:
        """search.list 包裝（成本 100 units／次）。params 即 API 原生參數。"""
        request = self._service.search().list(**params)
        return self._execute_with_retry(request, QuotaCost.SEARCH, f"search.list (q={params.get('q', '')})")
