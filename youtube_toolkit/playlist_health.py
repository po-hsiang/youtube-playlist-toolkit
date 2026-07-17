"""清單健康檢查：找出各播放清單中「私人／已刪除／不公開」的影片。

偵測原理：playlistItems.list 會回傳清單裡**所有**項目（含已壞掉的），
而 videos.list 只回傳還存在、可存取的影片——兩邊對照，缺席的就是私人或
已刪除（再以清單項目殘留的線索分辨）；不公開（unlisted）則由影片本身的
status.privacyStatus 判讀。

注意：私人／已刪除影片的原始標題已被 YouTube 抹除（清單上只剩
"Private video" / "Deleted video"），能列出的資訊為網址、位置與 videoId。
不公開影片目前仍可播放，但屬於高下架風險族群。

執行方式：
    uv run yt-health              # 掃描 playlists.toml 的全部清單
    uv run yt-health YTMusic      # 只掃描指定清單（可多個）
"""

import argparse
from typing import Any, Dict, List

from googleapiclient.errors import HttpError

from youtube_toolkit import playlists
from youtube_toolkit.youtube_client import YouTubeClient

PRIVATE = "private"
DELETED = "deleted"
UNLISTED = "unlisted"
OK = "ok"

_CATEGORY_LABELS = [
    (PRIVATE, "🔒 私人"),
    (DELETED, "🗑️ 已刪除"),
    (UNLISTED, "🔗 不公開（仍可播放，有下架風險）"),
]


def classify_entry(entry: Dict[str, Any], details: Dict[str, Dict[str, Any]]) -> str:
    """判定單一清單項目的健康狀態。"""
    detail = details.get(entry["video_id"])
    if detail is None:
        # videos.list 沒回傳 → 私人或已刪除；用清單項目殘留的線索分辨
        if entry.get("privacy_status") == "private" or entry["title"] == "Private video":
            return PRIVATE
        return DELETED
    if detail.get("privacy_status") == "unlisted":
        return UNLISTED
    return OK


def audit_playlist(
    entries: List[Dict[str, Any]], details: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """對一份清單做健康盤點，回傳 {"total", "private": [...], "deleted": [...], "unlisted": [...]}。

    每個問題項目包含：position（清單中第幾首）、video_id、url、title、channel
    （私人／已刪除影片的 title 只剩 YouTube 的替代文字，channel 為空）。
    """
    report: Dict[str, Any] = {"total": len(entries), PRIVATE: [], DELETED: [], UNLISTED: []}
    for position, entry in enumerate(entries, start=1):
        category = classify_entry(entry, details)
        if category == OK:
            continue
        detail = details.get(entry["video_id"]) or {}
        report[category].append(
            {
                "position": position,
                "video_id": entry["video_id"],
                "url": f"https://youtu.be/{entry['video_id']}",
                "title": detail.get("title") or entry["title"],
                "channel": detail.get("channel", ""),
            }
        )
    return report


def issue_count(report: Dict[str, Any]) -> int:
    return len(report[PRIVATE]) + len(report[DELETED]) + len(report[UNLISTED])


def print_report(name: str, report: Dict[str, Any]) -> None:
    if issue_count(report) == 0:
        print(f"✅ {name}：{report['total']} 部影片全部公開可用\n")
        return

    print(f"⚠️ {name}：{report['total']} 部影片中發現 {issue_count(report)} 個問題項目")
    for category, label in _CATEGORY_LABELS:
        items = report[category]
        if not items:
            continue
        print(f"  {label}（{len(items)}）：")
        for item in items:
            channel = f"【{item['channel']}】" if item["channel"] else ""
            print(f"    - 第 {item['position']:>4} 首  {item['url']}  {channel}{item['title']}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="播放清單健康檢查：列出私人／已刪除／不公開的影片")
    parser.add_argument("names", nargs="*", help="要檢查的清單名稱（省略則掃描 playlists.toml 全部清單）")
    args = parser.parse_args()

    all_playlists = playlists.load_all()
    if args.names:
        targets = [(name, playlists.get_playlist_id(name)) for name in args.names]
    else:
        targets = list(all_playlists.items())

    client = YouTubeClient.for_public_data()
    start_used = client.quota_manager.used
    totals = {PRIVATE: 0, DELETED: 0, UNLISTED: 0, "videos": 0, "skipped": []}

    for name, playlist_id in targets:
        try:
            entries = client.fetch_playlist_entries(playlist_id)
            details = client.fetch_video_details([entry["video_id"] for entry in entries])
        except HttpError as e:
            print(f"⏭️ {name}：無法讀取（HTTP {e.resp.status}，私人清單或 ID 失效），跳過\n")
            totals["skipped"].append(name)
            continue

        report = audit_playlist(entries, details)
        print_report(name, report)

        totals["videos"] += report["total"]
        for category in (PRIVATE, DELETED, UNLISTED):
            totals[category] += len(report[category])

    print("=" * 60)
    print(
        f"總結：掃描 {len(targets) - len(totals['skipped'])} 份清單、{totals['videos']} 部影片 → "
        f"私人 {totals[PRIVATE]}、已刪除 {totals[DELETED]}、不公開 {totals[UNLISTED]}"
    )
    if totals["skipped"]:
        print(f"跳過（無法讀取）：{'、'.join(totals['skipped'])}")
    used = client.quota_manager.used
    print(f"本次掃描消耗 {used - start_used} units（本配額日累計 {used}）")


if __name__ == "__main__":
    main()
