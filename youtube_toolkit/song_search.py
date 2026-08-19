"""歌曲搜尋／隨機抽選核心：MCP 伺服器與 CLI 共用同一套比對語意。

純函式、不碰網路——歌曲來源由呼叫端以 get_videos 注入
（伺服器傳入記憶體快取，CLI 退回模式傳入直接打 API 的取用器）。

**跨語言比對**：歌單約七成曲目來自 YouTube 自動生成的「- Topic」頻道，
歌名與頻道名只有羅馬字（周興哲的歌只寫得出 "Eric Chou - Topic"），用中文名
一律查不到。這些影片的標籤通常帶有藝人的母語名，因此比對範圍除歌名、頻道名
外再加上標籤——資料本來就在既有的 videos.list 回應裡，不增加任何配額。
標籤是隱藏欄位、偶有宣傳性雜訊（某藝人的 MV 會標上其他藝人名），故標籤命中
一律排在歌名／頻道命中之後，並以 matched_on 標示命中欄位供呼叫端判斷可信度；
標籤灌水的影片另以 MAX_TRUSTED_TAGS 整支排除。

**比對嚴格度分三種**（中日韓關鍵字一律用子字串，因為中日韓沒有詞邊界）：

| 欄位 | 純英數關鍵字的規則 | 理由 |
|------|-------------------|------|
| 歌名／頻道 | 詞首（前面不接英數字） | 擋掉字中間誤命中，但保留慣用的前綴搜尋 |
| 標籤 | 整詞（前後都不接英數字） | 隱藏欄位、誤命中難察覺，精準優先 |
"""

import random
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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

# 標籤數超過這個門檻的影片一律不採信其標籤。實測全庫 2,356 部：標籤數中位數 4、
# P95 為 24，但有影片掛到 82 個、塞滿一整排他人藝名（SEO 灌水）。取 25 略高於 P95，
# 且實測**不影響任何一部官方「- Topic」影片**——那正是母語名別名的主要來源。
MAX_TRUSTED_TAGS = 25

# 中日韓文字：決定關鍵字要用「子字串」還是「詞邊界」比對
# （依序為 日文假名／中日韓擴充A／中日韓統一表意文字／韓文音節）
_CJK_PATTERN = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯]")


def text_matcher(lowered_keyword: str) -> Callable[[str], bool]:
    """建立歌名／頻道比對器：**詞首**比對（關鍵字須已轉小寫）。

    中日韓文字沒有詞邊界，只能用子字串。純英數關鍵字則要求「前面不接英數字」：
    既擋掉字中間的誤命中（搜「hebe」命中頻道「onlythebestost」的 t-hebe-st、
    搜「live」命中「alive」、搜「ost」命中「hostage」），又保留大家慣用的
    前綴搜尋（「monster」仍命中「monsters」、「yorushi」仍命中「yorushika」）。
    """
    if _CJK_PATTERN.search(lowered_keyword):
        return lambda text: lowered_keyword in text
    pattern = re.compile(rf"(?<![0-9a-z]){re.escape(lowered_keyword)}")
    return lambda text: bool(pattern.search(text))


def tag_matcher(lowered_keyword: str) -> Callable[[str], bool]:
    """建立標籤比對器：**整詞**比對（關鍵字須已轉小寫）。

    比歌名／頻道更嚴格——標籤是使用者看不見的隱藏欄位，誤命中很難察覺，
    而且標籤本來就是一個個獨立的短詞，不需要前綴搜尋。因此要求前後都不接
    英數字：搜「ai」不會命中標籤「aimer」，但仍能命中「아이유(iu)」（括號即邊界）。
    """
    if _CJK_PATTERN.search(lowered_keyword):
        return lambda tag: lowered_keyword in tag
    pattern = re.compile(rf"(?<![0-9a-z]){re.escape(lowered_keyword)}(?![0-9a-z])")
    return lambda tag: bool(pattern.search(tag))


def build_matchers(lowered_keyword: str) -> Tuple[Callable[[str], bool], Callable[[str], bool]]:
    """一次建好 (歌名／頻道比對器, 標籤比對器)，讓每次查詢只編譯一次正規式。"""
    return text_matcher(lowered_keyword), tag_matcher(lowered_keyword)


def match_field(
    video: Dict[str, Any],
    lowered_keyword: str,
    matchers: Optional[Tuple[Callable[[str], bool], Callable[[str], bool]]] = None,
) -> str:
    """回傳命中的欄位（MATCH_TITLE／MATCH_CHANNEL／MATCH_TAG），沒命中回空字串。

    matchers 可預先以 build_matchers() 建好重複使用，避免每首歌重新編譯正規式。
    """
    match_text, match_tag = matchers or build_matchers(lowered_keyword)
    if match_text(video["title"].lower()):
        return MATCH_TITLE
    if match_text(video["channel"].lower()):
        return MATCH_CHANNEL
    tags = video.get("tags") or ()  # 舊快取或精簡來源可能沒有這個欄位
    # 灌水影片的標籤塞滿他人藝名，整支不採信（實測砍掉的全是雜訊，見 MAX_TRUSTED_TAGS）
    if tags and len(tags) <= MAX_TRUSTED_TAGS and any(match_tag(tag.lower()) for tag in tags):
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
    matchers = build_matchers(lowered)
    direct: List[Dict[str, Any]] = []  # 歌名／頻道命中
    by_tag: List[Dict[str, Any]] = []  # 標籤命中（可能是相關作品而非該藝人本人）
    total_matches = 0

    for name in playlist_names:
        for position, video in enumerate(get_videos(name), start=1):
            matched_on = match_field(video, lowered, matchers)
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
    matchers = build_matchers(lowered) if keyword else None
    candidates: List[Dict[str, Any]] = []
    for name in playlist_names:
        for position, video in enumerate(get_videos(name), start=1):
            matched_on = match_field(video, lowered, matchers) if keyword else ""
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
