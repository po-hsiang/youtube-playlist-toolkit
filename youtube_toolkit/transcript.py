"""YouTube 字幕抓取：軌道挑選、純文字整形與例外映射（給下游機器人做 LLM 摘要）。

用的是 youtube-transcript-api **v1.x 介面**（實例方法 `YouTubeTranscriptApi().list()`，
不是 0.6 舊版的類別方法 list_transcripts()）。此套件抓的是 YouTube 網頁端的字幕資料，
**不走 YouTube Data API、不消耗配額**，因此不經 quota_manager。

輸出欄位名是跨服務契約（下游機器人依名取值），**不可改名**。
"""

from typing import Any, Dict, Iterable, Optional

from youtube_transcript_api import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    InvalidVideoId,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeTranscriptApi,
)

# 字幕軌語言優先序（規格核心）：先在人工上傳字幕裡找、再退任一人工，
# 然後同樣順序找自動生成字幕、再退任一自動生成
LANGUAGE_PRIORITY = ("zh-Hant-TW", "zh-Hant", "zh-TW", "en")

# MCP 端預設截斷長度（REST 端預設不截斷）：字幕全文可以長達數十萬字元，
# 塞爆 agent context 比少讀一段更傷
DEFAULT_MCP_MAX_CHARS = 8000

NO_TRANSCRIPT = "NO_TRANSCRIPT"
VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"
TRANSCRIPT_UPSTREAM_ERROR = "TRANSCRIPT_UPSTREAM_ERROR"


class TranscriptUnavailable(Exception):
    """這支影片拿不到字幕（永久性）→ 404。args[0] 為契約錯誤碼。"""


class TranscriptUpstreamError(Exception):
    """上游封鎖／限流／暫時性失敗（可重試）→ 502。args[0] 為契約錯誤碼。"""


def select_transcript(transcripts: Iterable[Any]) -> Optional[Any]:
    """依規格優先序挑字幕軌；完全沒有可用軌道時回 None。

    1. 人工上傳，語言依 LANGUAGE_PRIORITY → 2. 任一人工上傳
    3. 自動生成，語言依 LANGUAGE_PRIORITY → 4. 任一自動生成
    """
    tracks = list(transcripts)
    manual = [t for t in tracks if not t.is_generated]
    generated = [t for t in tracks if t.is_generated]
    for pool in (manual, generated):
        for code in LANGUAGE_PRIORITY:
            for track in pool:
                if track.language_code == code:
                    return track
        if pool:
            return pool[0]
    return None


def build_text(snippets: Iterable[Any]) -> str:
    """各段字幕合併成單一空格分隔的純文字（段內換行也一併整平，方便餵 LLM）。"""
    words = []
    for snippet in snippets:
        words.extend(snippet.text.split())
    return " ".join(words)


def build_result(
    video_id: str, track: Any, text: str, max_chars: Optional[int] = None
) -> Dict[str, Any]:
    """組出契約格式。char_count 一律是**完整**字幕長度——截斷時下游才知道全文有多少。"""
    truncated = bool(max_chars and max_chars > 0 and len(text) > max_chars)
    return {
        "video_id": video_id,
        "language": track.language,
        "language_code": track.language_code,
        "is_auto_generated": track.is_generated,
        "text": text[:max_chars] if truncated else text,
        "char_count": len(text),
        "truncated": truncated,
    }


def fetch_transcript(
    video_id: str, max_chars: Optional[int] = None, api: Optional[Any] = None
) -> Dict[str, Any]:
    """抓字幕並整形成契約格式。max_chars 為 None 或 ≤0 時不截斷。

    api 可注入（測試用）；預設每次建新的 YouTubeTranscriptApi（各自的 requests
    session，避免跨執行緒共用）。套件例外映射：
    - VideoUnavailable / InvalidVideoId → VIDEO_NOT_FOUND
      （InvalidVideoId 理論上被呼叫端的格式預檢擋掉，這裡是防禦性映射）
    - 「這支影片永久拿不到字幕」（TranscriptsDisabled、NoTranscriptFound、
      AgeRestricted、VideoUnplayable）→ NO_TRANSCRIPT
    - 其餘一律 → TranscriptUpstreamError：RequestBlocked／IpBlocked、
      YouTubeRequestFailed（含 429）、PoTokenRequired…以及**未知的新例外**——
      對未知情況回「可重試」比回「永久沒有」安全，也保證不裸噴 500
    """
    api = api or YouTubeTranscriptApi()
    try:
        track = select_transcript(api.list(video_id))
        if track is None:
            raise TranscriptUnavailable(NO_TRANSCRIPT)
        text = build_text(track.fetch())
    except (VideoUnavailable, InvalidVideoId):
        raise TranscriptUnavailable(VIDEO_NOT_FOUND) from None
    except (TranscriptsDisabled, NoTranscriptFound, AgeRestricted, VideoUnplayable) as e:
        raise TranscriptUnavailable(NO_TRANSCRIPT) from e
    except CouldNotRetrieveTranscript as e:
        raise TranscriptUpstreamError(TRANSCRIPT_UPSTREAM_ERROR) from e
    return build_result(video_id, track, text, max_chars)
