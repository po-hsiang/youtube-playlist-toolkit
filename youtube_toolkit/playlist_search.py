"""歌單載入與關鍵字搜尋工具。

啟動時以 API Key 載入整份指定播放清單，之後可在本地以關鍵字搜尋歌名／頻道。
執行方式：python -m youtube_toolkit.playlist_search
"""

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from youtube_toolkit import config
import pickle


class YouTubeAPIHandler:
    def __init__(self):
        self.song_list = self.__sort_yt_playlist()
        # self.oauth_access_user()

    def __sort_yt_playlist(self):
        api_key = config.require_api_key()

        youtube = build("youtube", "v3", developerKey=api_key)

        # playlist_id = "PL8uoeex94UhHFRew8gzfFJHIpRFWyY4YW"  # EuroPython 2019
        playlist_id = "PLLUffVVIYEV8J2P4Tp-rkEYZEtMHHkm7o"  # My TYMusic(公開播放清單)
        # playlist_id = "PLLUffVVIYEV_eoZzUyq6z2pAumBCbYwit"  # BGM
        # playlist_id = "PLLUffVVIYEV80h2q5Q2b5oUfowXxYAwxo"  # 大合刷(私人播放清單)
        # playlist_id = "PLLUffVVIYEV-EtG7w59dxNxHIE_GzMRS0"  # Japanese (公開播放清單)

        videos = []

        next_page_token = None
        while True:
            pl_request = youtube.playlistItems().list(
                part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=next_page_token
            )
            pl_response = pl_request.execute()
            vid_ids = []
            for item in pl_response["items"]:
                vid_ids.append(item["contentDetails"]["videoId"])

            vid_request = youtube.videos().list(part="snippet,statistics", id=",".join(vid_ids))
            vid_response = vid_request.execute()
            for item in vid_response["items"]:
                vid_views = item["statistics"]["viewCount"]
                vid_id = item["id"]
                yt_link = f"https://youtu.be/{vid_id}"
                videos.append(
                    {
                        "views": int(vid_views),
                        "url": yt_link,
                        "title": item["snippet"]["title"],
                        "channel": item["snippet"]["channelTitle"],
                    }
                )
            next_page_token = pl_response.get("nextPageToken")
            if not next_page_token:
                break

        videos.sort(key=lambda vid: vid["views"], reverse=True)  # 依觀看次數排序
        videos.sort(key=lambda vid: (vid["channel"], vid["title"]))  # 先依頻道再依影片標題排序

        for index, video in enumerate(videos):
            print(
                f"{index + 1} url: {video['url']}, views: {video['views']}, 歌名: {video['title']}, 頻道: {video['channel']}"
            )
            # print(f"【{video['channel']}】《{video['title']}》")
        print(f"total: {len(videos)}")
        return videos

    def oauth_access_user(self):
        credentials = None

        if config.TOKEN_FILE.exists():
            print(f"Loading Credentials From File ...")
            with open(config.TOKEN_FILE, "rb") as token:
                credentials = pickle.load(token)

        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                print(f"Refreshing Access Token...")
                credentials.refresh(Request())
            else:
                print(f"Fetching New Tokens...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(config.CLIENT_SECRET_FILE),
                    scopes=[
                        "https://www.googleapis.com/auth/youtube.readonly",
                        "https://www.googleapis.com/auth/youtube.force-ssl",
                    ],
                )
                flow.run_local_server(port=config.OAUTH_PORT, prompt="consent", authorization_prompt_message="")
                credentials = flow.credentials

                config.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(config.TOKEN_FILE, "wb") as f:
                    print(f"Saving Credentials for Future use...")
                    pickle.dump(credentials, f)

        playlist_id = "PLLUffVVIYEV80h2q5Q2b5oUfowXxYAwxo"  # 大合刷
        youtube = build("youtube", "v3", credentials=credentials)
        request = youtube.playlistItems().list(part="status,contentDetails", playlistId=playlist_id, maxResults=50)
        response = request.execute()
        print(response)
        for item in response["items"]:
            vid_id = item["contentDetails"]["videoId"]
            yt_link = f"https://youtu.be/{vid_id}"
            print(yt_link)

    def search_keyword_in_song_list(self, keyword):
        if len(keyword) < 2:
            return [f"搜尋請大於等於2個字"]
        matched_songs = [song for song in self.song_list if self.__is_keyword_matched(song, keyword)]
        count = len(matched_songs)
        answer = self.__generate_song_list_response(matched_songs)
        return self.__generate_search_result_message(keyword, count, answer)

    def __is_keyword_matched(self, song, keyword):
        title = song["title"].lower()
        channel = song["channel"].lower()
        keyword = keyword.lower()
        return keyword in title or keyword in channel

    def __generate_song_list_response(self, songs):
        result = ""
        answer = []
        for index, song in enumerate(songs):
            current_song = f"{index + 1}.《{song['channel']}》{song['title']}\n"
            temp_result = result + current_song
            if len(temp_result) >= 1900:
                answer.append(result)
                result = ""
            result += current_song
        if result:
            answer.append(result)
        return answer

    def __generate_search_result_message(self, keyword, count, answer):
        if answer:
            answer[0] = f"\n歌單內標題含有「{keyword}」的歌共有{count}首：\n" + answer[0]
        return answer


def main():
    yt = YouTubeAPIHandler()
    # ヨルシカ あたらよ
    keyword = "Monsters"
    results = yt.search_keyword_in_song_list(keyword)
    if results:
        for result in results:
            print(result)
    else:
        print(f"歌單內的歌標題都沒有「{keyword}」字元")


if __name__ == "__main__":
    main()
