"""歌單載入與關鍵字搜尋工具。

啟動時以 API Key 載入整份指定播放清單，之後可在本地以關鍵字搜尋歌名／頻道，
搜尋結果分段輸出（每段 ≤ 1,900 字元）。執行方式：python -m youtube_toolkit.playlist_search
"""

from typing import Any, Dict, List, Optional

from youtube_toolkit.youtube_client import YouTubeClient

PLAYLIST_ID = "PLLUffVVIYEV8J2P4Tp-rkEYZEtMHHkm7o"  # My YTMusic（公開播放清單）
# 其他常用目標：
# "PLLUffVVIYEV_eoZzUyq6z2pAumBCbYwit"  # BGM
# "PLLUffVVIYEV80h2q5Q2b5oUfowXxYAwxo"  # 大合刷（私人播放清單，API Key 讀不到）
# "PLLUffVVIYEV-EtG7w59dxNxHIE_GzMRS0"  # Japanese（公開播放清單）
MIN_KEYWORD_LENGTH = 2
MAX_MESSAGE_LENGTH = 1900  # 分段上限（為 Discord 2,000 字元上限預留空間）


class YouTubeAPIHandler:
    """載入播放清單後提供本地關鍵字搜尋。"""

    def __init__(self, client: Optional[YouTubeClient] = None, playlist_id: str = PLAYLIST_ID):
        self.client = client or YouTubeClient.for_public_data()
        self.song_list = self._load_song_list(playlist_id)

    def _load_song_list(self, playlist_id: str) -> List[Dict[str, Any]]:
        videos = self.client.fetch_playlist_videos(playlist_id)
        videos.sort(key=lambda vid: vid["views"], reverse=True)  # 依觀看次數排序
        videos.sort(key=lambda vid: (vid["channel"], vid["title"]))  # 先依頻道再依影片標題排序

        for index, video in enumerate(videos):
            print(
                f"{index + 1} url: {video['url']}, views: {video['views']}, "
                f"歌名: {video['title']}, 頻道: {video['channel']}"
            )
        print(f"total: {len(videos)}")
        return videos

    def search_keyword_in_song_list(self, keyword: str) -> List[str]:
        if len(keyword) < MIN_KEYWORD_LENGTH:
            return [f"搜尋請大於等於{MIN_KEYWORD_LENGTH}個字"]
        matched_songs = [song for song in self.song_list if self._is_keyword_matched(song, keyword)]
        answer = self._generate_song_list_response(matched_songs)
        return self._generate_search_result_message(keyword, len(matched_songs), answer)

    @staticmethod
    def _is_keyword_matched(song: Dict[str, Any], keyword: str) -> bool:
        keyword = keyword.lower()
        return keyword in song["title"].lower() or keyword in song["channel"].lower()

    @staticmethod
    def _generate_song_list_response(songs: List[Dict[str, Any]]) -> List[str]:
        """把符合的歌組成訊息，超過長度上限就切成下一段。"""
        result = ""
        answer: List[str] = []
        for index, song in enumerate(songs):
            current_song = f"{index + 1}.《{song['channel']}》{song['title']}\n"
            if len(result) + len(current_song) >= MAX_MESSAGE_LENGTH:
                answer.append(result)
                result = ""
            result += current_song
        if result:
            answer.append(result)
        return answer

    @staticmethod
    def _generate_search_result_message(keyword: str, count: int, answer: List[str]) -> List[str]:
        if answer:
            answer[0] = f"\n歌單內標題含有「{keyword}」的歌共有{count}首：\n" + answer[0]
        return answer


def main() -> None:
    yt = YouTubeAPIHandler()
    keyword = "40mP"
    results = yt.search_keyword_in_song_list(keyword)
    if results:
        for result in results:
            print(result)
    else:
        print(f"歌單內的歌標題都沒有「{keyword}」字元")


if __name__ == "__main__":
    main()
