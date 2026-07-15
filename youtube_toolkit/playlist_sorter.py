"""播放清單自動排序器（主力工具）。

每天於排定時間將多份播放清單依「頻道 A→Z、觀看數 高→低」排序並寫回 YouTube，
全程受 QuotaManager 保護。執行方式：python -m youtube_toolkit.playlist_sorter
"""

from youtube_toolkit.quota_manager import QuotaManager, QuotaSoftLimitExceeded
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from typing import List, Dict, Any, Callable
from youtube_toolkit.log_utils import logger
from youtube_toolkit import config
import schedule
import pickle
import random
import time

OAUTH_SCOPES = ["https://www.googleapis.com/auth/youtube"]
LIST_QUOTA_COST = 1
UPDATE_QUOTA_COST = 50


class PlaylistSorter:
    def __init__(self, playlist_id: str, quota_manager: QuotaManager):
        self.playlist_id = playlist_id
        self.quota_manager = quota_manager
        self.youtube_service = self._authenticate()
        logger.info(f"成功認證並取得 YouTube 服務。")

    def _authenticate(self) -> Resource:
        credentials = None
        token_path = config.TOKEN_FILE

        # 1. 嘗試讀取舊憑證
        if token_path.exists():
            logger.debug(f"從 {token_path} 載入憑證...")
            try:
                with open(token_path, "rb") as token:
                    credentials = pickle.load(token)
            except Exception as e:
                logger.warning(f"讀取 pickle 檔案失敗，將重新登入: {e}")
                credentials = None

        # 2. 檢查憑證狀態與刷新
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                logger.info("憑證已過期，正在嘗試透過 Refresh Token 更新...")
                try:
                    # [關鍵修改] 加上 try-except 包裹刷新動作
                    credentials.refresh(Request())
                    logger.info("Access Token 更新成功！")
                except RefreshError as e:
                    # 如果會員卡(Refresh Token)也壞了，就印出錯誤並強制重來
                    logger.error(f"Refresh Token 已失效 (invalid_grant)，將重新啟動 OAuth 授權流程: {e}")
                    credentials = None  # 將憑證設為 None，讓程式進入下方的 else 重新登入
                except Exception as e:
                    logger.error(f"更新 Token 時發生未預期錯誤: {e}")
                    credentials = None

            # 3. 如果前面沒有憑證，或是刷新失敗 (credentials 被設回 None)，就重新登入
            if not credentials:  # 這裡不要用 else，因為上面的 refresh 失敗會流到這裡
                logger.warning(f"無有效憑證，正在啟動瀏覽器 OAuth 流程 (Scope: {OAUTH_SCOPES})...")

                if not config.CLIENT_SECRET_FILE.exists():
                    raise FileNotFoundError(
                        f"找不到 OAuth 用戶端密鑰檔：{config.CLIENT_SECRET_FILE}，"
                        "請參考 README「憑證設定」一節。"
                    )

                # 為了避免快取舊的錯誤 token，若檔案存在建議先移除 (非必要但較保險)
                if token_path.exists():
                    try:
                        token_path.unlink()
                    except OSError as e:
                        logger.warning(f"移除舊 token 檔失敗（不影響流程）: {e}")

                flow = InstalledAppFlow.from_client_secrets_file(str(config.CLIENT_SECRET_FILE), scopes=OAUTH_SCOPES)
                flow.run_local_server(
                    port=config.OAUTH_PORT,
                    prompt="consent",
                    authorization_prompt_message="請在瀏覽器中授權本應用程式修改您的 YouTube 播放清單",
                )
                credentials = flow.credentials

                # 4. 儲存最新的憑證
                token_path.parent.mkdir(parents=True, exist_ok=True)
                with open(token_path, "wb") as f:
                    logger.info(f"儲存新憑證至 {token_path}...")
                    pickle.dump(credentials, f)

        return build("youtube", "v3", credentials=credentials)

    def _fetch_all_playlist_items(self) -> List[Dict[str, Any]]:
        logger.debug(f"開始抓取播放清單【{self.playlist_id}】中的相關資訊")
        all_items = []
        next_page_token = None

        while True:
            try:
                self.quota_manager.consume(cost=LIST_QUOTA_COST, context=f"playlistItems.list")
                request = self.youtube_service.playlistItems().list(
                    part="snippet",
                    playlistId=self.playlist_id,
                    maxResults=50,
                    pageToken=next_page_token,
                )
                response = request.execute()

                for item in response.get("items", []):
                    if "videoId" in item["snippet"]["resourceId"]:
                        all_items.append(
                            {
                                "playlistItemId": item["id"],
                                "title": item["snippet"]["title"],
                                "videoId": item["snippet"]["resourceId"]["videoId"],
                            }
                        )
                next_page_token = response.get("nextPageToken")
                if not next_page_token:
                    break  # 所有頁面都已抓取完畢

            except HttpError as e:
                logger.error(f"抓取播放清單時發生錯誤: {e}")
                raise e
        logger.info(f"抓取完畢，共 {len(all_items)} 個項目。")
        return all_items

    def _fetch_video_details(self, video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        logger.debug(f"正在批次抓取 {len(video_ids)} 部影片的詳細資訊，像頻道、觀看次數等")
        details = {}
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i : i + 50]
            try:
                self.quota_manager.consume(cost=LIST_QUOTA_COST, context=f"videos.list (Batch {i // 50 + 1})")
                vid_request = self.youtube_service.videos().list(part="snippet,statistics", id=",".join(chunk))
                vid_response = vid_request.execute()
                for item in vid_response.get("items", []):
                    details[item["id"]] = {
                        "views": int(item.get("statistics", {}).get("viewCount", 0)),
                        "channel": item.get("snippet", {}).get("channelTitle", "N/A"),
                    }
            except HttpError as e:
                logger.error(f"抓取影片詳細資訊時發生錯誤: {e}")
        logger.info("影片詳細資訊抓取完畢。")
        return details

    def _get_sorted_items(
        self, items: List[Dict[str, Any]], sort_key_func: Callable[[Dict], Any]
    ) -> List[Dict[str, Any]]:
        logger.debug("正在本地端進行排序 (產生理想順序)...")
        return sorted(items, key=sort_key_func)

    def _apply_sort_to_youtube(self, ideal_list: List[Dict[str, Any]], current_list: List[Dict[str, Any]]) -> None:
        logger.debug(f"即將開始更新播放清單順序... (共 {len(ideal_list)} 個項目)")

        delay_between_updates = 0.5  # 單位：秒 (防止 API Rate Limit)
        max_retries = 5  # 單一項目最大重試次數
        base_retry_delay = 1.0  # 重試的基礎延遲 (秒)

        for new_position, ideal_item in enumerate(ideal_list):
            current_item_at_this_pos = current_list[new_position]

            if ideal_item["playlistItemId"] == current_item_at_this_pos["playlistItemId"]:
                logger.info(
                    f"✔️ SKIPPING (Pos: {new_position}): 【{ideal_item['channel']}】《{ideal_item['title']}》[觀看: {ideal_item.get('views', 0)}] (已在正確位置)"
                )
                continue  # 節省 50 Quota，繼續下一個

            logger.warning(
                f"⚠️ MOVING (Pos: {new_position}): 【{ideal_item['channel']}】《{ideal_item['title']}》[觀看: {ideal_item.get('views', 0)}]"
            )
            for attempt in range(max_retries):
                try:
                    self.quota_manager.consume(
                        cost=UPDATE_QUOTA_COST, context=f"playlistItems.update ({ideal_item['title']})"
                    )

                    body = {
                        "id": ideal_item["playlistItemId"],
                        "snippet": {
                            "playlistId": self.playlist_id,
                            "resourceId": {
                                "kind": "youtube#video",
                                "videoId": ideal_item["videoId"],
                            },
                            "position": new_position,
                        },
                    }
                    self.youtube_service.playlistItems().update(part="snippet", body=body).execute()

                    item_to_move_idx = -1
                    for idx, item in enumerate(current_list):
                        if item["playlistItemId"] == ideal_item["playlistItemId"]:
                            item_to_move_idx = idx
                            break

                    if item_to_move_idx != -1:
                        moved_item = current_list.pop(item_to_move_idx)
                        current_list.insert(new_position, moved_item)
                    else:
                        logger.warning("  > [警告] 本地狀態同步異常 (找不到 item)，但不影響 API。")

                    logger.info(f"  > ✅ SUCCESS. Moved to position {new_position}")
                    time.sleep(delay_between_updates)
                    break  # 成功，跳出重試迴圈

                except QuotaSoftLimitExceeded:
                    # 如果在「移動到一半」時 Quota 耗盡，我們必須停止
                    logger.warning(f"已達 Quota 軟上限，無法移動 {ideal_item['title']}，停止更新此播放清單。")
                    raise  # 再次拋出，讓 run() -> job_execute_sort() 捕捉

                except HttpError as e:
                    logger.warning(f"  > [重試 {attempt + 1}/{max_retries}] 移動 '{ideal_item['title']}' 時發生錯誤: {e}")
                    if e.resp.status in [409, 500, 503]:
                        if attempt < max_retries - 1:
                            delay = (base_retry_delay * (2**attempt)) + random.uniform(0, 1)
                            logger.debug(f"    ... 伺服器忙碌，{delay:.2f} 秒後重試...")
                            time.sleep(delay)
                        else:
                            logger.error(f"  > [永久失敗] 已達最大重試次數，放棄此項目。")
                    elif "quotaExceeded" in str(e):
                        logger.warning("API Quota 已用盡 (Hard Limit)！請明天再試。")
                        raise e
                    else:
                        logger.error(f"  > [非可重試錯誤] {e}")
                        break
                except Exception as e:
                    logger.error(f"  > [程式異常] {e}")
                    break
            else:
                logger.warning(f"  > [已跳過] 項目 '{ideal_item['title']}' 在 {max_retries} 次重試後仍失敗。")
        logger.info("播放清單排序更新完畢！")

    def run(self, auto_run: bool = False) -> None:
        current_items = self._fetch_all_playlist_items()
        if not current_items:
            logger.error("無法抓取到任何項目，程式終止。")
            return

        all_video_ids = [item["videoId"] for item in current_items]
        video_details = self._fetch_video_details(all_video_ids)

        for item in current_items:
            details = video_details.get(item["videoId"], {"views": 0, "channel": "N/A"})
            item["views"] = details["views"]
            item["channel"] = details["channel"]

        sort_function = lambda item: (item.get("channel", "").lower(), -item.get("views", 0))
        ideal_list = self._get_sorted_items(current_items.copy(), sort_function)

        confirm = "yes"  # 預設為 'yes'
        if not auto_run:
            confirm = input(
                f"您確定要將此排序套用到播放清單 '{self.playlist_id}' 嗎？\n"
                f"這將會消耗大量的 API Quota 並且「永久」修改您的播放清單順序。\n"
                f"預估剩餘 Quota {self.quota_manager.remaining_before_soft_limit()} (Soft Limit).\n"
                "輸入 'yes' 以繼續： "
            )

        if confirm.lower() == "yes":
            self._apply_sort_to_youtube(ideal_list, current_items)
        else:
            logger.debug("操作已取消。")


def job_execute_sort():
    logger.info("排程作業啟動：開始執行播放清單排序...")

    quota_manager = QuotaManager(daily_limit=config.YOUTUBE_DAILY_LIMIT, soft_limit=config.YOUTUBE_SOFT_LIMIT)

    playlists_to_sort = [
        ("Live", "PLLUffVVIYEV_8vV5tNnViOQaNhDrrbcr9"),  # 7
        ("Covers (Chinese)", "PLLUffVVIYEV8cdnk8mseZz8As7Pljs7nm"),  # 9
        ("Other Languages Songs", "PLLUffVVIYEV8eyriNIjptSXg__6RB1ltx"),  # 13
        ("Song of Combination", "PLEA4152F16A0C1ACC"),  # 27
        ("Covers (English)", "PLLUffVVIYEV-QmbB7ZvRvM4zA4OJepmXd"),  # 56
        ("Musical Instruments", "PL21C891F13DFB9C25"),  # 100
        ("KTV", "PLLUffVVIYEV947ZHP92M-PVEKpdJtgU06"),  # 142
        ("English", "PLLUffVVIYEV_RlQHzEqBUa1jFlAW9FzDN"),  # 163
        ("Japanese", "PLLUffVVIYEV-EtG7w59dxNxHIE_GzMRS0"),  # 190
        ("Chinese", "PLLUffVVIYEV9wvxjcqMEbuojSiMqvwGNA"),  # 217
        ("Covers (Japanese)", "PLLUffVVIYEV-NE2WtmUva-rw4R3t3UBA1"),  # 226
        ("BGM / OST", "PLLUffVVIYEV_eoZzUyq6z2pAumBCbYwit"),  # 272
        ("YTMusic", "PLLUffVVIYEV8J2P4Tp-rkEYZEtMHHkm7o"),  # 1085
    ]

    for name, playlist_id in playlists_to_sort:
        logger.info(f"開始處理清單：{name} (目前 Quota 已用: {quota_manager.used})")
        try:
            sorter = PlaylistSorter(playlist_id=playlist_id, quota_manager=quota_manager)
            sorter.run(auto_run=True)
            logger.info(f"清單 {name} 處理完畢\n\n\n")

        except QuotaSoftLimitExceeded:
            logger.warning(f"!!! 已達到 Quota 軟上限 ({config.YOUTUBE_SOFT_LIMIT}) !!!")
            logger.warning("中止本次「所有」排程作業，以保留剩餘 Quota。")
            break  # 跳出 for 迴圈，不再處理下一個播放清單

        except HttpError as e:
            logger.error(f"[嚴重 API 錯誤] 處理清單 {name} ({playlist_id}) 時失敗: {e}")
            if "quotaExceeded" in str(e):
                logger.warning("!!! API Quota 已用盡 (Hard Limit)！中止本次排程作業。")
                break  # Hard Limit，必須停止

        except Exception as e:
            logger.error(f"[未預期錯誤] 處理清單 {name} ({playlist_id}) 時失敗: {e}")

    logger.info("排程作業執行完畢。")


def main():
    logger.info(f"已設定排程：每天 {config.SCHEDULE_TIME} 執行排序作業。")
    schedule.every().day.at(config.SCHEDULE_TIME).do(job_execute_sort)

    logger.debug("為了測試，啟動時將立即執行一次作業...")
    job_execute_sort()

    logger.info(f"腳本進入待命狀態，等待下一個排程時間 ({config.SCHEDULE_TIME})... (Ctrl+C 關閉)")
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
