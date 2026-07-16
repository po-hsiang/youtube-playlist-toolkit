"""重複歌曲偵測工具：標題正規化後以子字串互含比對分組，產生人工判斷用報告。

僅產生報告、不會自動刪除任何影片。執行方式：python -m youtube_toolkit.duplicate_finder
目標清單設定於 playlists.toml 的 [duplicate_finder].target，改字串即可換清單。
"""

import re
from typing import Any, Dict, List

from youtube_toolkit import playlists
from youtube_toolkit.youtube_client import YouTubeClient

# 括號「符號」換成空白但保留內容——【七里香】的歌名在括號裡，整段去掉會漏比對
_BRACKET_TRANSLATION = str.maketrans({ch: " " for ch in "【】[]（）()「」『』〈〉《》｛｝{}"})

# 比對前移除的宣傳雜訊字樣（不移除 live / cover 等有語意的詞，現場版≠原版）
_NOISE_PATTERN = re.compile(
    r"official\s*music\s*video|official\s*(video|audio|mv)|\bofficial\b"
    r"|\bm/?v\b|lyric(s)?(\s*video)?|\bhd\b|\b4k\b|\b(feat|ft)\b\.?"
    r"|官方(完整版|歌詞版|高畫質)?|完整版|歌詞版|高清|音樂錄影帶|動態歌詞",
    re.IGNORECASE,
)
_WHITESPACE_PATTERN = re.compile(r"\s+")

MIN_NORMALIZED_LENGTH = 2  # 正規化後太短的標題不參與比對，避免空字串互含造成亂分組


def normalize_title(title: str) -> str:
    """正規化標題供比對：小寫、括號符號換空白（保留內容）、去雜訊字樣、壓縮空白。"""
    text = title.lower().translate(_BRACKET_TRANSLATION)
    text = _NOISE_PATTERN.sub(" ", text)
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


def find_potential_duplicates(videos: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """找出標題相似的影片並分組：正規化後的標題互為子字串即視為同組。"""
    normalized_titles = [normalize_title(video["title"]) for video in videos]

    processed_indices = set()  # 已被歸組的影片索引，避免重複撿起
    duplicate_groups: List[List[Dict[str, Any]]] = []

    for i in range(len(videos)):
        if i in processed_indices or len(normalized_titles[i]) < MIN_NORMALIZED_LENGTH:
            continue

        current_group = [videos[i]]
        title_a = normalized_titles[i]

        for j in range(i + 1, len(videos)):
            if j in processed_indices or len(normalized_titles[j]) < MIN_NORMALIZED_LENGTH:
                continue
            title_b = normalized_titles[j]
            # 例如：「七里香」 vs 「周杰倫 Jay Chou【七里香 Common Jasmine Orange】Official MV」
            if title_a in title_b or title_b in title_a:
                current_group.append(videos[j])
                processed_indices.add(j)

        if len(current_group) > 1:
            # 依觀看次數由大到小排序，方便決定要留哪一個
            current_group.sort(key=lambda x: x["views"], reverse=True)
            duplicate_groups.append(current_group)

    return duplicate_groups


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
    name, playlist_id = playlists.tool_target("duplicate_finder")
    print(f"正在讀取播放清單：{name}（{playlist_id}）...")

    client = YouTubeClient.for_public_data()
    videos = client.fetch_playlist_videos(playlist_id)
    print(f"共取得 {len(videos)} 部影片。\n")

    print("正在進行標題正規化與相似度比對...\n")
    print_report(find_potential_duplicates(videos))


if __name__ == "__main__":
    main()
