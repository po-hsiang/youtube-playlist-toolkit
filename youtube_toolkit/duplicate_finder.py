"""重複歌曲偵測工具：以標題子字串互含比對分組，產生人工判斷用報告。

僅產生報告、不會自動刪除任何影片。執行方式：python -m youtube_toolkit.duplicate_finder
"""

from typing import Any, Dict, List, Optional

from youtube_toolkit.youtube_client import YouTubeClient

PLAYLIST_ID = "PLLUffVVIYEV8J2P4Tp-rkEYZEtMHHkm7o"  # My YTMusic


class YouTubeDuplicateFinder:
    def __init__(self, client: Optional[YouTubeClient] = None):
        self.client = client or YouTubeClient.for_public_data()

    def get_all_videos_in_playlist(self, playlist_id: str) -> List[Dict[str, Any]]:
        """取得播放清單內所有影片的詳細資訊（標題、頻道、觀看次數）。"""
        print(f"正在讀取播放清單：{playlist_id} ...")
        videos = self.client.fetch_playlist_videos(playlist_id)
        print(f"共取得 {len(videos)} 部影片。\n")
        return videos

    def find_potential_duplicates(self, videos: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """核心邏輯：標題（小寫、去頭尾空白後）互為子字串即視為同組。"""
        print("正在進行相似度比對與分組...\n")

        processed_indices = set()  # 已被歸組的影片索引，避免重複撿起
        duplicate_groups: List[List[Dict[str, Any]]] = []

        for i in range(len(videos)):
            if i in processed_indices:
                continue

            current_group = [videos[i]]
            title_a = videos[i]["title"].lower().strip()

            for j in range(i + 1, len(videos)):
                if j in processed_indices:
                    continue
                title_b = videos[j]["title"].lower().strip()
                # 例如：「七里香」 vs 「周杰倫 Jay Chou【七里香 Common Jasmine Orange】Official MV」
                if title_a in title_b or title_b in title_a:
                    current_group.append(videos[j])
                    processed_indices.add(j)

            if len(current_group) > 1:
                # 依觀看次數由大到小排序，方便決定要留哪一個
                current_group.sort(key=lambda x: x["views"], reverse=True)
                duplicate_groups.append(current_group)

        return duplicate_groups

    @staticmethod
    def print_report(groups: List[List[Dict[str, Any]]]) -> None:
        """印出報告讓使用者手動判斷、手動刪除。"""
        if not groups:
            print("恭喜！沒有發現明顯的重複歌曲。")
            return

        print(f"=== 發現 {len(groups)} 組疑似重複的歌曲 ===")
        print("註：每組的第一首通常是觀看次數最高的 (建議保留)，其他的可以考慮刪除。\n")

        for idx, group in enumerate(groups):
            print(f"【第 {idx + 1} 組】")
            for v_idx, video in enumerate(group):
                prefix = "👑 保留?" if v_idx == 0 else "❌ 刪除?"
                print(f"  {prefix} [{video['views']:,} 觀看] {video['title']}")
                print(f"       頻道: {video['channel']}")
                print(f"       連結: {video['url']}")
            print("-" * 50)


def main() -> None:
    finder = YouTubeDuplicateFinder()
    all_videos = finder.get_all_videos_in_playlist(PLAYLIST_ID)
    duplicates = finder.find_potential_duplicates(all_videos)
    finder.print_report(duplicates)


if __name__ == "__main__":
    main()
