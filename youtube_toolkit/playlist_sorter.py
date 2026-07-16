"""播放清單自動排序器（主力工具）。

每天於排定時間將多份播放清單依「頻道 A→Z、觀看數 高→低」排序並寫回 YouTube，
全程受 QuotaManager 保護。執行方式：python -m youtube_toolkit.playlist_sorter

排序策略：以 LIS（最長遞增子序列）計算最少搬移計畫——相對順序已正確的項目
原地不動，只搬其餘項目，搬移次數為數學上的最小值（每次搬移 50 units）。
"""

import random
import time
from typing import Any, Dict, List

import schedule
from googleapiclient.errors import HttpError

from youtube_toolkit import config
from youtube_toolkit.log_utils import logger
from youtube_toolkit.quota_manager import QuotaManager, QuotaSoftLimitExceeded
from youtube_toolkit.sorting import Move, plan_minimal_moves
from youtube_toolkit.youtube_client import QuotaCost, YouTubeClient

DELAY_BETWEEN_UPDATES = 0.5  # 秒；防止 API Rate Limit
MAX_RETRIES = 5  # 單一搬移的最大重試次數
BASE_RETRY_DELAY = 1.0  # 重試的基礎延遲（秒），指數退避
RETRYABLE_HTTP_STATUS = (409, 500, 503)


class PlaylistSorter:
    """對單一播放清單執行「頻道 A→Z、觀看數 高→低」排序。"""

    def __init__(self, playlist_id: str, client: YouTubeClient):
        self.playlist_id = playlist_id
        self.client = client

    def run(self, auto_run: bool = False) -> None:
        entries = self.client.fetch_playlist_entries(self.playlist_id)
        if not entries:
            logger.error("無法抓取到任何項目，跳過此清單。")
            return

        details = self.client.fetch_video_details([entry["video_id"] for entry in entries])
        for entry in entries:
            detail = details.get(entry["video_id"], {})
            entry["channel"] = detail.get("channel", "N/A")
            entry["views"] = detail.get("views", 0)

        ideal = sorted(entries, key=lambda e: (e["channel"].lower(), -e["views"]))
        moves = plan_minimal_moves(
            current_order=[e["playlist_item_id"] for e in entries],
            ideal_order=[e["playlist_item_id"] for e in ideal],
        )

        if not moves:
            logger.info("✔️ 清單已完全有序，無需搬移（本清單寫入成本 0 units）。")
            return

        estimated_cost = len(moves) * QuotaCost.UPDATE
        logger.info(
            f"共 {len(entries)} 首，LIS 計畫僅需搬移 {len(moves)} 首，"
            f"預估寫入成本 {estimated_cost} units"
            f"（剩餘可用 {self.client.quota_manager.remaining_before_soft_limit()} units）。"
        )

        if not auto_run:
            confirm = input(
                f"您確定要將此排序套用到播放清單 '{self.playlist_id}' 嗎？\n"
                f"這將消耗約 {estimated_cost} units 並「永久」修改您的播放清單順序。\n"
                "輸入 'yes' 以繼續： "
            )
            if confirm.lower() != "yes":
                logger.debug("操作已取消。")
                return

        entries_by_id = {entry["playlist_item_id"]: entry for entry in entries}
        self._execute_moves(moves, entries_by_id)

    def _execute_moves(self, moves: List[Move], entries_by_id: Dict[str, Dict[str, Any]]) -> None:
        logger.debug(f"即將開始更新播放清單順序...（共 {len(moves)} 步搬移）")

        for step, move in enumerate(moves, start=1):
            entry = entries_by_id[move.item_id]
            logger.warning(
                f"⚠️ MOVING ({step}/{len(moves)} → Pos {move.position}): "
                f"【{entry['channel']}】《{entry['title']}》[觀看: {entry['views']}]"
            )
            if not self._move_with_retry(entry, move.position):
                # 一步永久失敗會使後續計畫位置產生偏移；不中斷，讓明日排程重算修正。
                logger.warning("  > 此步失敗，後續搬移位置可能略有偏移，下次執行會自動重算修正。")

        logger.info("播放清單排序更新完畢！")

    def _move_with_retry(self, entry: Dict[str, Any], position: int) -> bool:
        """搬移單一項目，對暫時性錯誤做指數退避重試。回傳是否成功。

        配額相關例外（軟上限熔斷、硬上限用盡）不屬於可重試錯誤，直接往外拋。
        """
        for attempt in range(MAX_RETRIES):
            try:
                self.client.move_playlist_item(
                    playlist_item_id=entry["playlist_item_id"],
                    playlist_id=self.playlist_id,
                    video_id=entry["video_id"],
                    position=position,
                )
                logger.info(f"  > ✅ SUCCESS. Moved to position {position}")
                time.sleep(DELAY_BETWEEN_UPDATES)
                return True

            except QuotaSoftLimitExceeded:
                logger.warning(f"已達 Quota 軟上限，無法搬移《{entry['title']}》，停止更新此播放清單。")
                raise  # 交由 job_execute_sort 中止整輪作業

            except HttpError as e:
                if "quotaExceeded" in str(e):
                    logger.warning("API Quota 已用盡 (Hard Limit)！請明天再試。")
                    raise
                if e.resp.status in RETRYABLE_HTTP_STATUS and attempt < MAX_RETRIES - 1:
                    delay = BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"  > [重試 {attempt + 1}/{MAX_RETRIES}] 搬移《{entry['title']}》失敗（{e.resp.status}），"
                        f"{delay:.2f} 秒後重試..."
                    )
                    time.sleep(delay)
                    continue
                logger.error(f"  > [放棄此項目] 搬移《{entry['title']}》失敗：{e}")
                return False

            except Exception as e:
                logger.error(f"  > [程式異常] {e}")
                return False

        return False


def job_execute_sort() -> None:
    logger.info("排程作業啟動：開始執行播放清單排序...")

    quota_manager = QuotaManager(daily_limit=config.YOUTUBE_DAILY_LIMIT, soft_limit=config.YOUTUBE_SOFT_LIMIT)
    try:
        client = YouTubeClient.for_authorized_user(quota_manager)  # 13 份清單共用同一次認證
    except Exception as e:
        logger.error(f"[認證失敗] 無法建立 YouTube 服務：{e}")
        return

    # 由小到大排列：確保配額耗盡前，小清單能全部完成
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
            sorter = PlaylistSorter(playlist_id=playlist_id, client=client)
            sorter.run(auto_run=True)
            logger.info(f"清單 {name} 處理完畢\n\n\n")

        except QuotaSoftLimitExceeded:
            logger.warning(f"!!! 已達到 Quota 軟上限 ({config.YOUTUBE_SOFT_LIMIT}) !!!")
            logger.warning("中止本次「所有」排程作業，以保留剩餘 Quota。")
            break

        except HttpError as e:
            logger.error(f"[嚴重 API 錯誤] 處理清單 {name} ({playlist_id}) 時失敗: {e}")
            if "quotaExceeded" in str(e):
                logger.warning("!!! API Quota 已用盡 (Hard Limit)！中止本次排程作業。")
                break

        except Exception as e:
            logger.error(f"[未預期錯誤] 處理清單 {name} ({playlist_id}) 時失敗: {e}")

    logger.info("排程作業執行完畢。")


def main() -> None:
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
