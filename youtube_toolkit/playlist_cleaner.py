"""清除播放清單中已失效（私人／已刪除）影片的工具。

**「不公開」（unlisted）影片仍可播放，永遠不會被本工具移除**——候選名單
只來自 playlist_health 的私人／已刪除分類，且刪除前會重新向 API 驗證一次，
仍讀得到詳情的影片（含不公開）一律剔除。

安全設計（由外而內四道防線）：
1. 預設 dry-run：只列出「將移除」名單與成本，加 `--apply` 才會刪
2. 刪除前重新驗證候選影片仍然失效（防暫時性 API 抖動造成誤判）
3. 完整名單先寫入 logs/cleanup-*.txt 留底（標題已被 YouTube 抹除，這是最後線索）
4. 要求輸入 yes 確認（`--yes` 跳過，供非互動情境）

使用 OAuth 認證（刪除需要），因此連 API Key 讀不到的私人清單也能一併檢查；
你自己上傳的私人影片對你而言仍可播放，會被歸類為正常、不會列入移除。
playlistItems.delete 每筆 50 units。

執行方式：
    uv run yt-clean                  # dry-run：列出將移除的名單（不動任何東西）
    uv run yt-clean --apply          # 實際移除（會先重新驗證＋留底＋要求輸入 yes）
    uv run yt-clean --apply --yes    # 同上但跳過互動確認
    uv run yt-clean YTMusic --apply  # 只清指定清單
"""

import argparse
from datetime import datetime
from typing import Any, Dict, List, Tuple

from googleapiclient.errors import HttpError

from youtube_toolkit import playlists
from youtube_toolkit.log_utils import LOG_DIR, logger
from youtube_toolkit.playlist_health import DELETED, PRIVATE, audit_playlist
from youtube_toolkit.quota_manager import QuotaSoftLimitExceeded
from youtube_toolkit.youtube_client import QuotaCost, YouTubeClient

# (清單名稱, 清單 ID, 候選項目) —— 候選項目來自 audit_playlist 的問題項目
Candidate = Tuple[str, str, Dict[str, Any]]


def removal_candidates(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """從單一清單的健康報告取出「可移除」項目：只有私人與已刪除，不公開永不列入。"""
    return list(report[PRIVATE]) + list(report[DELETED])


def split_still_dead(
    candidates: List[Candidate], fresh_details: Dict[str, Dict[str, Any]]
) -> Tuple[List[Candidate], List[Candidate]]:
    """以重新抓取的影片詳情二次驗證：仍讀不到詳情＝確定失效；讀得到＝剔除不刪。"""
    dead = [c for c in candidates if c[2]["video_id"] not in fresh_details]
    resurrected = [c for c in candidates if c[2]["video_id"] in fresh_details]
    return dead, resurrected


def write_backup(candidates: List[Candidate]) -> Any:
    """刪除前把完整名單留底到 logs/（影片標題已被 YouTube 抹除，這份是最後線索）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"cleanup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    lines = [f"# 播放清單失效影片移除名單（{datetime.now().isoformat(timespec='seconds')}）"]
    for name, playlist_id, item in candidates:
        lines.append(
            f"{name}\t第 {item['position']} 首\t{item['url']}\t{item['title']}\t"
            f"playlistItemId={item['playlist_item_id']}\tplaylistId={playlist_id}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="移除播放清單中已失效（私人／已刪除）的影片；不公開影片不受影響")
    parser.add_argument("names", nargs="*", help="要清理的清單名稱（省略則掃描 playlists.toml 全部清單）")
    parser.add_argument("--apply", action="store_true", help="實際執行移除（預設只列名單）")
    parser.add_argument("--yes", action="store_true", help="跳過互動確認（供非互動情境）")
    args = parser.parse_args()

    all_playlists = playlists.load_all()
    if args.names:
        targets = [(name, playlists.get_playlist_id(name)) for name in args.names]
    else:
        targets = list(all_playlists.items())

    client = YouTubeClient.for_authorized_user()  # 刪除需要 OAuth；私人清單也能一併檢查
    plan: List[Candidate] = []

    for name, playlist_id in targets:
        try:
            entries = client.fetch_playlist_entries(playlist_id)
            details = client.fetch_video_details([entry["video_id"] for entry in entries])
        except HttpError as e:
            print(f"⏭️ {name}：無法讀取（HTTP {e.resp.status}），跳過")
            continue

        candidates = removal_candidates(audit_playlist(entries, details))
        if not candidates:
            print(f"✅ {name}：沒有失效影片")
            continue
        print(f"⚠️ {name}：{len(candidates)} 筆將移除")
        for item in candidates:
            print(f"    - 第 {item['position']:>4} 首  {item['url']}  {item['title']}")
        plan.extend((name, playlist_id, item) for item in candidates)

    if not plan:
        print("\n🎉 所有清單都沒有失效影片，不需要清理。")
        return

    print(f"\n共 {len(plan)} 筆，移除成本 {len(plan) * QuotaCost.DELETE} units。")
    if not args.apply:
        print("（目前為 dry-run，未做任何變更；加上 --apply 執行移除。）")
        return

    # 二次驗證：重新抓一次詳情，仍讀得到的（含不公開）一律不刪
    unique_ids = sorted({item["video_id"] for _, _, item in plan})
    fresh_details = client.fetch_video_details(unique_ids)
    plan, resurrected = split_still_dead(plan, fresh_details)
    for name, _, item in resurrected:
        print(f"🛟 二次驗證仍可讀取，剔除不刪：{name} 第 {item['position']} 首 {item['url']}")
    if not plan:
        print("二次驗證後沒有需要移除的項目。")
        return

    backup_path = write_backup(plan)
    print(f"📄 名單已留底：{backup_path}")

    if not args.yes:
        confirm = input(f"確定要從清單中移除這 {len(plan)} 筆失效影片嗎？（輸入 yes 繼續）： ")
        if confirm.lower() != "yes":
            print("已取消，未做任何變更。")
            return

    removed, failed = 0, 0
    for name, _, item in plan:
        try:
            client.delete_playlist_item(item["playlist_item_id"])
            removed += 1
            print(f"✂️ 已移除：{name} 第 {item['position']} 首 {item['url']}")
        except QuotaSoftLimitExceeded:
            logger.warning("已達 Quota 軟上限，停止移除；剩餘項目請明天再跑一次。")
            break
        except HttpError as e:
            failed += 1
            logger.error(f"移除失敗（{name} {item['url']}）：{e}")

    print(f"\n完成：移除 {removed} 筆、失敗 {failed} 筆、未處理 {len(plan) - removed - failed} 筆。")
    print(f"本配額日累計已用 {client.quota_manager.used} units。")


if __name__ == "__main__":
    main()
