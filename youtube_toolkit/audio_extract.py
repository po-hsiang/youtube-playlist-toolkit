"""低碼率音訊抽取：yt-dlp 下載 bestaudio，ffmpeg 轉 OGG/Opus 32kbps 單聲道。

用途：下游影片摘要遇到沒有 CC 字幕的影片時，抽出純音訊丟給語音轉錄模型。
**不走 YouTube Data API**——yt-dlp 抓的是網頁端資料，0 配額、不經 quota_manager。

為什麼用子程序而非 yt_dlp 的 Python API：逾時保護要「真的能砍掉」。
路由跑在 worker thread 上，卡死的執行緒無法回收；subprocess 逾時會直接
kill 子程序，worker 立刻釋放。測試時注入假的 run callable 即可離線驗證。

為什麼自己跑 ffmpeg 而非 yt-dlp 的 -x 後處理：來源音軌若已是 opus，
yt-dlp 會直接複製封裝、跳過重編碼，-b:a 32k 就沒生效——碼率控制必須
由我們自己的 ffmpeg 步驟保證。

逾時是**含探測的整體預算**（預設 180 秒）：主人定案的三層活門最內層
（bot 200 → n8n 190 → 本服務 180），本服務必須最先放棄，上游才收得到
明確的 502 而不是斷線。時長閘門同步收在 70 分鐘（4200 秒），正常速率
（實測約每分鐘影片 1 秒）跑得完；被 YouTube 限速時寧可 502 讓上游重試。

錯誤映射（args[0] 是契約錯誤碼、status_code 是對應 HTTP 狀態，伺服器不裸噴 500）：
- AudioUnavailable  → 404 VIDEO_NOT_FOUND（影片不存在／私人）
- AudioLiveStream   → 400 LIVE_STREAM（直播中或即將首播，沒有完整音訊可抽）
- AudioTooLong      → 413 AUDIO_TOO_LONG（超過 AUDIO_MAX_DURATION_SECONDS）
- AudioExtractError → 502 AUDIO_EXTRACT_FAILED（下載／轉檔失敗、逾時，可重試）
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from youtube_toolkit import config
from youtube_toolkit.log_utils import logger

# 契約錯誤碼（跨服務約定，不可改名）
VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"
LIVE_STREAM = "LIVE_STREAM"
AUDIO_TOO_LONG = "AUDIO_TOO_LONG"
AUDIO_EXTRACT_FAILED = "AUDIO_EXTRACT_FAILED"

PROBE_TIMEOUT_SECONDS = 30  # 探測在總預算內另設較緊上限，別讓卡住的探測吃光下載時間
AUDIO_BITRATE = "32k"  # mono Opus ≈ 14.4 MB／小時，Gemini 支援的 audio/ogg

# 探測失敗時 stderr 含這些樣式＝影片本身不存在／看不到（小寫比對）
_NOT_FOUND_PATTERNS = (
    "video unavailable",
    "private video",
    "this video is not available",
    "has been removed",
    "account associated with this video has been terminated",
)

_RunFn = Callable[..., "subprocess.CompletedProcess[str]"]


class AudioExtractionError(Exception):
    """基底例外：args[0] 固定為契約錯誤碼，detail 只進日誌、不回傳給呼叫端。"""

    code = AUDIO_EXTRACT_FAILED
    status_code = 502

    def __init__(self, detail: str = ""):
        super().__init__(self.code)
        self.detail = detail


class AudioUnavailable(AudioExtractionError):
    code = VIDEO_NOT_FOUND
    status_code = 404


class AudioLiveStream(AudioExtractionError):
    code = LIVE_STREAM
    status_code = 400


class AudioTooLong(AudioExtractionError):
    code = AUDIO_TOO_LONG
    status_code = 413


class AudioExtractError(AudioExtractionError):
    code = AUDIO_EXTRACT_FAILED
    status_code = 502


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _run(cmd: list, timeout: float, run: _RunFn) -> "subprocess.CompletedProcess[str]":
    # Windows 主機的子程序輸出預設是 cp950，必須指定 utf-8 才不會炸中文
    return run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _stderr_tail(proc: "subprocess.CompletedProcess[str]") -> str:
    return (proc.stderr or "").strip()[-300:]


def _remaining(deadline: float, step: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AudioExtractError(f"{step}前已超過總逾時")
    return remaining


def probe(video_id: str, run: _RunFn = subprocess.run, deadline: Optional[float] = None) -> Dict[str, Any]:
    """以 yt-dlp 取 metadata（不下載）。回傳解析後的 JSON dict。"""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-single-json", "--no-download", "--no-playlist", "--no-warnings",
        _watch_url(video_id),
    ]
    timeout: float = PROBE_TIMEOUT_SECONDS
    if deadline is not None:
        timeout = min(timeout, _remaining(deadline, "探測"))
    try:
        proc = _run(cmd, timeout, run)
    except subprocess.TimeoutExpired:
        raise AudioExtractError(f"探測逾時（>{int(timeout)} 秒）") from None
    if proc.returncode != 0:
        stderr = (proc.stderr or "").lower()
        if any(pattern in stderr for pattern in _NOT_FOUND_PATTERNS):
            raise AudioUnavailable(_stderr_tail(proc))
        raise AudioExtractError(f"探測失敗：{_stderr_tail(proc)}")
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        raise AudioExtractError("探測輸出不是合法 JSON") from None


def precheck(meta: Dict[str, Any], max_duration: int) -> None:
    """下載前的守門：直播／即將首播擋 400，超長擋 413。

    is_upcoming（即將首播）也歸入 LIVE_STREAM——同樣沒有完整音訊可抽，
    不擋會流到 502，語意較差。duration 缺值且非直播的罕見情況放行，
    交給抽取逾時兜底。
    """
    if meta.get("is_live") or meta.get("live_status") in ("is_live", "is_upcoming"):
        raise AudioLiveStream(f"live_status={meta.get('live_status')}")
    duration = meta.get("duration")
    if duration and duration > max_duration:
        raise AudioTooLong(f"時長 {int(duration)} 秒 > 上限 {max_duration} 秒")


def _download_bestaudio(video_id: str, workdir: Path, deadline: float, run: _RunFn) -> Path:
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist", "--no-progress", "--no-warnings",
        "-f", "bestaudio/best",
        "-o", str(workdir / f"{video_id}.src.%(ext)s"),
        _watch_url(video_id),
    ]
    try:
        proc = _run(cmd, _remaining(deadline, "下載"), run)
    except subprocess.TimeoutExpired:
        raise AudioExtractError("下載音訊逾時") from None
    if proc.returncode != 0:
        raise AudioExtractError(f"yt-dlp 下載失敗：{_stderr_tail(proc)}")
    # 副檔名由來源決定（webm／m4a…），用 glob 找；.part 是未完成的中間檔要排除
    src = next((p for p in sorted(workdir.glob(f"{video_id}.src.*")) if p.suffix != ".part"), None)
    if src is None:
        raise AudioExtractError("yt-dlp 成功結束但找不到下載的來源檔")
    return src


def _transcode_to_opus(src: Path, output: Path, deadline: float, run: _RunFn) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-vn", "-ac", "1", "-c:a", "libopus", "-b:a", AUDIO_BITRATE,
        str(output),
    ]
    try:
        proc = _run(cmd, _remaining(deadline, "轉檔"), run)
    except subprocess.TimeoutExpired:
        raise AudioExtractError("ffmpeg 轉檔逾時") from None
    if proc.returncode != 0:
        raise AudioExtractError(f"ffmpeg 轉檔失敗：{_stderr_tail(proc)}")
    if not output.exists() or output.stat().st_size == 0:
        raise AudioExtractError("ffmpeg 成功結束但輸出檔缺失或為空")


def cleanup_workdir(path: Any) -> None:
    """移除整個暫存目錄（成功回應送出後、或失敗時呼叫）；目錄已不存在也不報錯。"""
    shutil.rmtree(path, ignore_errors=True)


def extract_audio(
    video_id: str,
    max_duration: Optional[int] = None,
    run: _RunFn = subprocess.run,
    workdir: Optional[Path] = None,
    total_timeout: Optional[int] = None,
) -> Path:
    """探測 → 預檢 → 下載 → 轉檔，回傳 OGG/Opus 檔路徑（位於獨立暫存目錄內）。

    total_timeout 是**含探測**的整體預算（預設 config.AUDIO_TIMEOUT_SECONDS＝180）：
    本服務是三層活門的最內層，必須比上游先放棄。
    成功時呼叫端負責在回應送出後 cleanup_workdir(回傳路徑.parent)；
    **拋出任何例外前必先清空暫存目錄**，失敗路徑不留殘檔。
    workdir 僅供測試注入，正式流程一律在系統 temp 開新目錄。
    """
    if max_duration is None:
        max_duration = config.AUDIO_MAX_DURATION_SECONDS
    if total_timeout is None:
        total_timeout = config.AUDIO_TIMEOUT_SECONDS
    deadline = time.monotonic() + total_timeout
    work = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="yt-audio-"))
    try:
        meta = probe(video_id, run=run, deadline=deadline)
        precheck(meta, max_duration)
        duration = int(meta.get("duration") or 0)

        started = time.monotonic()
        src = _download_bestaudio(video_id, work, deadline, run)
        output = work / f"{video_id}.ogg"
        _transcode_to_opus(src, output, deadline, run)
        src.unlink(missing_ok=True)  # 來源檔可能上百 MB，轉完立刻釋放

        logger.info(
            f"[Audio] {video_id} 抽取完成：{duration} 秒 → "
            f"{output.stat().st_size / 1_048_576:.1f} MB，耗時 {time.monotonic() - started:.0f} 秒"
        )
        return output
    except BaseException as e:
        cleanup_workdir(work)
        if isinstance(e, AudioExtractionError) and e.detail:
            logger.warning(f"[Audio] {video_id} 抽取失敗（{e.code}）：{e.detail}")
        raise
