"""播放清單自動排序器（主力工具）。

每天於排定時間將多份播放清單依「頻道 A→Z、觀看數 高→低」排序並寫回 YouTube，
全程受 QuotaManager 保護。

執行方式：
    uv run yt-sort               # 立即跑一輪後進入每日排程待命（預設）
    uv run yt-sort --once        # 只跑一輪就結束（也用於手動重新 OAuth 授權）
    uv run yt-sort --dry-run     # 只顯示 LIS 搬移計畫與預估配額，不寫入 YouTube
    uv run yt-sort --unattended  # 無人值守（容器／背景服務）：任何情況都不開瀏覽器

排序策略：以 LIS（最長遞增子序列）計算最少搬移計畫——相對順序已正確的項目
原地不動，只搬其餘項目，搬移次數為數學上的最小值（每次搬移 50 units）。
排程（無人值守）執行時若憑證失效，不會開瀏覽器卡死，改記錯誤並於下輪再試。
"""

import argparse
import json
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import schedule
from googleapiclient.errors import HttpError

from youtube_toolkit import config, playlists
from youtube_toolkit.auth import ReauthorizationRequired
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

    def run(self, auto_run: bool = False, dry_run: bool = False) -> int:
        """執行單一清單排序，回傳 LIS 計畫的搬移數（dry_run 或取消時不寫入）。"""
        entries = self.client.fetch_playlist_entries(self.playlist_id)
        if not entries:
            logger.error("無法抓取到任何項目，跳過此清單。")
            return 0

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
            return 0

        estimated_cost = len(moves) * QuotaCost.UPDATE
        logger.info(
            f"共 {len(entries)} 首，LIS 計畫僅需搬移 {len(moves)} 首，"
            f"預估寫入成本 {estimated_cost} units"
            f"（剩餘可用 {self.client.quota_manager.remaining_before_soft_limit()} units）。"
        )

        entries_by_id = {entry["playlist_item_id"]: entry for entry in entries}

        if dry_run:
            for step, move in enumerate(moves, start=1):
                entry = entries_by_id[move.item_id]
                logger.info(
                    f"[DRY-RUN] {step}/{len(moves)}: 【{entry['channel']}】《{entry['title']}》→ 位置 {move.position}"
                )
            return len(moves)

        if not auto_run:
            confirm = input(
                f"您確定要將此排序套用到播放清單 '{self.playlist_id}' 嗎？\n"
                f"這將消耗約 {estimated_cost} units 並「永久」修改您的播放清單順序。\n"
                "輸入 'yes' 以繼續： "
            )
            if confirm.lower() != "yes":
                logger.debug("操作已取消。")
                return 0

        self._execute_moves(moves, entries_by_id)
        return len(moves)

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


def _last_run_date() -> Optional[str]:
    """讀取最後一次完成排序的本地日期（YYYY-MM-DD）；讀不到回傳 None。"""
    try:
        return json.loads(config.SORTER_STATE_FILE.read_text(encoding="utf-8")).get("last_run_date")
    except (OSError, ValueError):
        return None


def _record_run_date() -> None:
    try:
        payload = json.dumps({"last_run_date": datetime.now().strftime("%Y-%m-%d")})
        config.SORTER_STATE_FILE.write_text(payload, encoding="utf-8")
    except OSError as e:
        logger.warning(f"無法寫入排序狀態檔（不影響本次作業）：{e}")


def job_execute_sort(interactive: bool = True, dry_run: bool = False) -> None:
    """跑一輪全部清單的排序。

    interactive=False（每日排程觸發）時，憑證失效不會開瀏覽器卡死 daemon，
    改記 ERROR 並結束本輪；請手動執行 `uv run yt-sort --once` 完成重新授權。
    """
    logger.info("排程作業啟動：開始執行播放清單排序..." + ("（DRY-RUN 模式，不寫入）" if dry_run else ""))

    try:
        playlists_to_sort = playlists.sorter_playlists()  # 清單與順序設定於 playlists.toml
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"[設定錯誤] {e}")
        return

    quota_manager = QuotaManager(
        daily_limit=config.YOUTUBE_DAILY_LIMIT,
        soft_limit=config.YOUTUBE_SOFT_LIMIT,
        state_file=config.QUOTA_STATE_FILE,  # 同配額日內重啟不歸零
    )
    try:
        client = YouTubeClient.for_authorized_user(quota_manager, interactive=interactive)
    except ReauthorizationRequired as e:
        logger.error(f"[需要重新授權] {e}")
        return  # daemon 保持存活，明天再試；授權完成後自動恢復
    except Exception as e:
        logger.error(f"[認證失敗] 無法建立 YouTube 服務：{e}")
        return

    total_planned_moves = 0
    for name, playlist_id in playlists_to_sort:
        logger.info(f"開始處理清單：{name} (目前 Quota 已用: {quota_manager.used})")
        try:
            sorter = PlaylistSorter(playlist_id=playlist_id, client=client)
            total_planned_moves += sorter.run(auto_run=True, dry_run=dry_run)
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

    if dry_run:
        logger.info(
            f"[DRY-RUN] 全部清單總計：需搬移 {total_planned_moves} 首、"
            f"預估寫入成本 {total_planned_moves * QuotaCost.UPDATE} units（未寫入任何變更）。"
        )
    else:
        _record_run_date()  # 供「今天已跑過就跳過啟動輪」判斷
    logger.info("排程作業執行完畢。")


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube 播放清單自動排序器（LIS 最少搬移）")
    parser.add_argument("--once", action="store_true", help="立即執行一輪後結束，不進入每日排程待命")
    parser.add_argument("--dry-run", action="store_true", help="只顯示搬移計畫與預估配額，不寫入 YouTube")
    parser.add_argument(
        "--unattended",
        action="store_true",
        help="無人值守模式（容器／背景服務用）：憑證失效時記錯誤而非啟動瀏覽器授權",
    )
    args = parser.parse_args()

    # 容器內沒有瀏覽器，啟動時的立即執行也必須是非互動的
    startup_interactive = not args.unattended

    if args.dry_run:
        job_execute_sort(interactive=startup_interactive, dry_run=True)
        return
    if args.once:
        job_execute_sort(interactive=startup_interactive)
        return

    logger.info(f"已設定排程：每天 {config.SCHEDULE_TIME} 執行排序作業。")
    schedule.every().day.at(config.SCHEDULE_TIME).do(job_execute_sort, interactive=False)

    # 啟動時補跑一輪（機器關機／容器重啟後的追進度機制），
    # 但同一天內重複啟動不再重跑，避免重開機或容器重建時白燒配額。
    today = datetime.now().strftime("%Y-%m-%d")
    if _last_run_date() == today:
        logger.info(f"今天（{today}）已執行過排序，略過啟動時的立即執行，直接進入待命。")
    else:
        logger.debug("啟動時立即執行一次作業...")
        job_execute_sort(interactive=startup_interactive)

    logger.info(f"腳本進入待命狀態，等待下一個排程時間 ({config.SCHEDULE_TIME})... (Ctrl+C 關閉)")
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
