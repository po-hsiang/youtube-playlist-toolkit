"""重複歌曲偵測工具：以標題子字串互含比對分組，產生人工判斷用報告。

執行方式：python -m youtube_toolkit.duplicate_finder
"""

from googleapiclient.discovery import build
from youtube_toolkit import config


class YouTubeDuplicateFinder:
    def __init__(self, api_key):
        self.api_key = api_key
        self.youtube = build("youtube", "v3", developerKey=self.api_key)

    def get_all_videos_in_playlist(self, playlist_id):
        """
        取得播放清單內所有影片的詳細資訊 (包含標題、頻道、觀看次數)
        """
        print(f"正在讀取播放清單：{playlist_id} ...")
        videos = []
        next_page_token = None

        while True:
            # 1. 先抓取播放清單內的項目 ID
            pl_request = self.youtube.playlistItems().list(
                part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=next_page_token
            )
            pl_response = pl_request.execute()

            vid_ids = []
            for item in pl_response["items"]:
                vid_ids.append(item["contentDetails"]["videoId"])

            # 2. 再根據 ID 去抓取影片詳細資訊 (為了拿標題和觀看次數)
            if vid_ids:
                vid_request = self.youtube.videos().list(part="snippet,statistics", id=",".join(vid_ids))
                vid_response = vid_request.execute()

                for item in vid_response["items"]:
                    # 有些影片可能被刪除或私人，會沒有 statistics，稍微防呆一下
                    if "statistics" not in item:
                        continue

                    vid_views = item["statistics"].get("viewCount", 0)
                    vid_title = item["snippet"]["title"]
                    vid_channel = item["snippet"]["channelTitle"]
                    vid_id = item["id"]

                    videos.append(
                        {
                            "id": vid_id,
                            "title": vid_title,
                            "channel": vid_channel,
                            "views": int(vid_views),
                            "url": f"https://youtu.be/{vid_id}",
                        }
                    )

            next_page_token = pl_response.get("nextPageToken")
            if not next_page_token:
                break

        print(f"共取得 {len(videos)} 部影片。\n")
        return videos

    def find_potential_duplicates(self, videos):
        """
        核心邏輯：找出標題相似的影片並分組
        """
        print("正在進行相似度比對與分組...\n")

        # 用來記錄哪些影片已經被歸類過了，避免重複被撿起來
        processed_indices = set()
        duplicate_groups = []

        # 雙層迴圈：拿每一首歌去跟後面的歌比對
        for i in range(len(videos)):
            if i in processed_indices:
                continue

            current_video = videos[i]
            # 建立一個新的群組，預設裡面只有自己
            current_group = [current_video]

            # 把標題轉成小寫，比較準確 (忽略大小寫差異)
            title_a = current_video["title"].lower().strip()

            for j in range(i + 1, len(videos)):
                if j in processed_indices:
                    continue

                compare_video = videos[j]
                title_b = compare_video["title"].lower().strip()

                # --- 核心比對邏輯 ---
                # 判斷邏輯：如果 A 字串在 B 裡面，或是 B 字串在 A 裡面
                # 例如：「七里香」 vs 「周杰倫 Jay Chou【七里香 Common Jasmine Orange】Official MV」
                if title_a in title_b or title_b in title_a:
                    current_group.append(compare_video)
                    processed_indices.add(j)  # 標記這個 j 已經被抓到了

            # 如果這個群組裡面的數量大於 1，代表有找到重複的
            if len(current_group) > 1:
                # 依觀看次數由大到小排序，方便您決定要留哪一個
                current_group.sort(key=lambda x: x["views"], reverse=True)
                duplicate_groups.append(current_group)

        return duplicate_groups

    def print_report(self, groups):
        """
        印出漂亮的報告讓使用者手動刪除
        """
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


def main():
    api_key = config.require_api_key()

    # 您的播放清單 ID
    playlist_id = "PLLUffVVIYEV8J2P4Tp-rkEYZEtMHHkm7o"

    finder = YouTubeDuplicateFinder(api_key)

    # 1. 抓取所有影片
    all_videos = finder.get_all_videos_in_playlist(playlist_id)

    # 2. 找出重複並分組
    duplicates = finder.find_potential_duplicates(all_videos)

    # 3. 印出結果
    finder.print_report(duplicates)


if __name__ == "__main__":
    main()
