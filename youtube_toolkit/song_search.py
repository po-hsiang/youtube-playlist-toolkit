"""歌曲搜尋／隨機抽選核心：MCP 伺服器與 CLI 共用同一套比對語意。

純函式、不碰網路——歌曲來源由呼叫端以 get_videos 注入
（伺服器傳入記憶體快取，CLI 退回模式傳入直接打 API 的取用器）。

**跨語言比對**：歌單約七成曲目來自 YouTube 自動生成的「- Topic」頻道，
歌名與頻道名只有羅馬字（周興哲的歌只寫得出 "Eric Chou - Topic"），用中文名
一律查不到。這些影片的標籤通常帶有藝人的母語名，因此比對範圍除歌名、頻道名
外再加上標籤——資料本來就在既有的 videos.list 回應裡，不增加任何配額。
標籤是隱藏欄位、偶有宣傳性雜訊（某藝人的 MV 會標上其他藝人名），故標籤命中
一律排在歌名／頻道命中之後，並以 matched_on 標示命中欄位供呼叫端判斷可信度。
"""

import random
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

MIN_KEYWORD_LENGTH = 2
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
DEFAULT_RANDOM_COUNT = 1
MAX_RANDOM_COUNT = 10

# matched_on 的取值（跨服務契約：下游依此判斷命中品質，改名會讓客戶端拿不到值）
MATCH_TITLE = "title"
MATCH_CHANNEL = "channel"
MATCH_TAG = "tag"

# 查無結果時附上的提示：AI agent 常在 0 筆時就回覆「沒有收錄」，
# 但它其實知道藝人的羅馬字名——給一句提示它就會自己換關鍵字重試。
NO_MATCH_HINT = (
    "查無結果。若關鍵字是藝人／團體名，請改用其英文或羅馬拼音名再查一次"
    "（例：周興哲→Eric Chou、ヨルシカ→Yorushika、田馥甄→Hebe），"
    "確認兩種寫法都沒有命中，再回覆使用者找不到。"
)

# 中日韓文字：決定關鍵字要用「子字串」還是「詞邊界」比對標籤
# （依序為 日文假名／中日韓擴充A／中日韓統一表意文字／韓文音節）
_CJK_PATTERN = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯]")


def tag_matcher(lowered_keyword: str) -> Callable[[str], bool]:
    """建立標籤比對器（關鍵字須已轉小寫）。

    中日韓文字沒有詞邊界，只能用子字串比對（「周興哲」要命中標籤「周興哲」）；
    但純英數關鍵字用子字串會誤命中——搜「iu」會中「studio」、搜「ai」會中
    「aimer」——故改為前後不接英數字的詞邊界比對：既擋掉誤命中，又仍能命中
    標籤「아이유(iu)」（括號即邊界）。
    """
    if _CJK_PATTERN.search(lowered_keyword):
        return lambda tag: lowered_keyword in tag
    pattern = re.compile(rf"(?<![0-9a-z]){re.escape(lowered_keyword)}(?![0-9a-z])")
    return lambda tag: bool(pattern.search(tag))


def match_field(
    video: Dict[str, Any],
    lowered_keyword: str,
    match_tag: Optional[Callable[[str], bool]] = None,
) -> str:
    """回傳命中的欄位（MATCH_TITLE／MATCH_CHANNEL／MATCH_TAG），沒命中回空字串。

    match_tag 可預先以 tag_matcher() 建好重複使用，避免每首歌重新編譯正規式。
    """
    if lowered_keyword in video["title"].lower():
        return MATCH_TITLE
    if lowered_keyword in video["channel"].lower():
        return MATCH_CHANNEL
    tags = video.get("tags") or ()  # 舊快取或精簡來源可能沒有這個欄位
    if tags:
        matcher = match_tag or tag_matcher(lowered_keyword)
        if any(matcher(tag.lower()) for tag in tags):
            return MATCH_TAG
    return ""


def as_song(
    playlist: str, position: int, video: Dict[str, Any], matched_on: str = ""
) -> Dict[str, Any]:
    """統一的單曲輸出格式（搜尋結果與隨機抽歌共用，客戶端只需認得一種形狀）。

    matched_on 空字串＝未經關鍵字比對（沒給關鍵字的隨機抽歌）。
    刻意不回傳 tags：那是隱藏欄位、對使用者沒有意義，只會佔滿 agent 的上下文。
    """
    return {
        "playlist": playlist,
        "position": position,
        "title": video["title"],
        "channel": video["channel"],
        "views": video["views"],
        "url": video["url"],
        "matched_on": matched_on,
    }


def search_playlists(
    keyword: str,
    playlist_names: Sequence[str],
    get_videos: Callable[[str], List[Dict[str, Any]]],
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """在多份清單中搜尋關鍵字，回傳統一格式的結果。

    keyword 太短時回傳 {"error": ...}。limit 只限制回傳筆數，
    total_matches 仍是完整命中數（讓使用者知道還有多少沒顯示）。
    歌名／頻道命中一律排在標籤命中之前；完全沒有命中時附上 hint。
    """
    keyword = keyword.strip()
    if len(keyword) < MIN_KEYWORD_LENGTH:
        return {"error": f"關鍵字需至少 {MIN_KEYWORD_LENGTH} 個字元"}
    limit = max(1, min(int(limit), MAX_LIMIT))

    lowered = keyword.lower()
    match_tag = tag_matcher(lowered)
    direct: List[Dict[str, Any]] = []  # 歌名／頻道命中
    by_tag: List[Dict[str, Any]] = []  # 標籤命中（可能是相關作品而非該藝人本人）
    total_matches = 0

    for name in playlist_names:
        for position, video in enumerate(get_videos(name), start=1):
            matched_on = match_field(video, lowered, match_tag)
            if not matched_on:
                continue
            total_matches += 1
            bucket = by_tag if matched_on == MATCH_TAG else direct
            if len(bucket) < limit:  # 兩桶各留最多 limit 筆，合併後再截斷就夠了
                bucket.append(as_song(name, position, video, matched_on))

    results = (direct + by_tag)[:limit]
    payload: Dict[str, Any] = {
        "keyword": keyword,
        "searched_playlists": list(playlist_names),
        "total_matches": total_matches,
        "returned": len(results),
        "results": results,
    }
    if total_matches == 0:
        payload["hint"] = NO_MATCH_HINT
    return payload


def pick_random_songs(
    playlist_names: Sequence[str],
    get_videos: Callable[[str], List[Dict[str, Any]]],
    count: int = DEFAULT_RANDOM_COUNT,
    keyword: str = "",
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """從清單中隨機抽出 count 首**不重複**的歌。

    keyword 留空＝整份清單都是候選；有給則只從命中的歌曲中抽（比對語意與 search 完全一致）。
    候選數不足 count 時回傳現有的全部（不補齊、不重複）；完全沒有候選時 songs 為空陣列，
    不視為錯誤——呼叫端只要檢查 songs 是否為空即可（有給關鍵字時另附 hint）。

    rng 可注入以取得可重現的結果（測試用）。
    """
    keyword = keyword.strip()
    if keyword and len(keyword) < MIN_KEYWORD_LENGTH:
        return {"error": f"關鍵字需至少 {MIN_KEYWORD_LENGTH} 個字元"}
    count = max(1, min(int(count), MAX_RANDOM_COUNT))

    lowered = keyword.lower()
    match_tag = tag_matcher(lowered) if keyword else None
    candidates: List[Dict[str, Any]] = []
    for name in playlist_names:
        for position, video in enumerate(get_videos(name), start=1):
            matched_on = match_field(video, lowered, match_tag) if keyword else ""
            if keyword and not matched_on:
                continue
            candidates.append(as_song(name, position, video, matched_on))

    picker = rng or random.Random()
    songs = picker.sample(candidates, min(count, len(candidates)))

    payload: Dict[str, Any] = {
        "playlists": list(playlist_names),
        "keyword": keyword,
        "candidates": len(candidates),
        "returned": len(songs),
        "songs": songs,
    }
    if keyword and not candidates:
        payload["hint"] = NO_MATCH_HINT
    return payload
