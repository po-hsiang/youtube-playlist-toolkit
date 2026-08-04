"""audio_extract 測試：注入假的 subprocess run，涵蓋成功、各錯誤碼、逾時與暫存清理。

測試不可依賴真實網路——yt-dlp／ffmpeg 一律以 ScriptedRun 的 handler 模擬，
handler 想製造輸出檔就直接寫進測試自備的 workdir。
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from youtube_toolkit.audio_extract import (
    AUDIO_EXTRACT_FAILED,
    AUDIO_TOO_LONG,
    EXTRACT_TIMEOUT_BASE,
    EXTRACT_TIMEOUT_MAX,
    LIVE_STREAM,
    PROBE_TIMEOUT_SECONDS,
    VIDEO_NOT_FOUND,
    AudioExtractError,
    AudioExtractionError,
    AudioLiveStream,
    AudioTooLong,
    AudioUnavailable,
    extract_audio,
    extract_timeout,
)

VID = "dQw4w9WgXcQ"


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class ScriptedRun:
    """依呼叫順序執行預排的 handler(cmd, kwargs)，並記錄每次收到的指令與參數。"""

    def __init__(self, *handlers):
        self._handlers = list(handlers)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs)))
        return self._handlers[len(self.calls) - 1](cmd, kwargs)


class ExtractAudioTestBase(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="test-yt-audio-"))

    def tearDown(self):
        shutil.rmtree(self.workdir, ignore_errors=True)

    # ── 各步驟的標準 handler ──────────────────────────

    def probe_ok(self, duration=300, **extra):
        meta = {"id": VID, "duration": duration, "is_live": False, "live_status": "not_live"}
        meta.update(extra)
        return lambda cmd, kwargs: completed(stdout=json.dumps(meta))

    def download_ok(self, ext="webm"):
        def handler(cmd, kwargs):
            (self.workdir / f"{VID}.src.{ext}").write_bytes(b"fake-source-audio")
            return completed()

        return handler

    def ffmpeg_ok(self):
        def handler(cmd, kwargs):
            (self.workdir / f"{VID}.ogg").write_bytes(b"OggS fake opus data")
            return completed()

        return handler

    def extract(self, run, max_duration=None):
        return extract_audio(VID, max_duration=max_duration, run=run, workdir=self.workdir)

    def assert_fails_and_cleans_workdir(self, run, exc_type, code, status_code, max_duration=None):
        with self.assertRaises(exc_type) as ctx:
            self.extract(run, max_duration=max_duration)
        self.assertEqual(ctx.exception.args[0], code)  # args[0] 是契約錯誤碼
        self.assertEqual(ctx.exception.status_code, status_code)
        self.assertFalse(self.workdir.exists())  # 失敗路徑不留殘檔
        return ctx.exception


class TestSuccessPath(ExtractAudioTestBase):
    def test_returns_playable_ogg_and_keeps_workdir_for_response(self):
        run = ScriptedRun(self.probe_ok(), self.download_ok(), self.ffmpeg_ok())

        output = self.extract(run)

        self.assertEqual(output, self.workdir / f"{VID}.ogg")
        self.assertTrue(output.exists())
        self.assertTrue(self.workdir.exists())  # 成功時目錄留給呼叫端在回應送出後清
        self.assertEqual(len(run.calls), 3)  # 探測 → 下載 → 轉檔

    def test_source_file_is_deleted_after_transcode(self):
        run = ScriptedRun(self.probe_ok(), self.download_ok(), self.ffmpeg_ok())

        self.extract(run)

        # 來源檔可能上百 MB，轉完就該釋放，暫存目錄裡只剩輸出檔
        self.assertEqual([p.name for p in self.workdir.iterdir()], [f"{VID}.ogg"])

    def test_probe_does_not_download(self):
        run = ScriptedRun(self.probe_ok(), self.download_ok(), self.ffmpeg_ok())

        self.extract(run)

        probe_cmd, probe_kwargs = run.calls[0]
        self.assertIn("--dump-single-json", probe_cmd)
        self.assertIn("--no-download", probe_cmd)
        self.assertEqual(probe_kwargs["timeout"], PROBE_TIMEOUT_SECONDS)

    def test_ffmpeg_enforces_low_bitrate_mono_opus(self):
        """碼率控制是本端點存在的理由：ffmpeg 參數是契約，改動要有意識。"""
        run = ScriptedRun(self.probe_ok(), self.download_ok(), self.ffmpeg_ok())

        self.extract(run)

        ffmpeg_cmd, _ = run.calls[2]
        self.assertEqual(ffmpeg_cmd[0], "ffmpeg")
        for flag in ("-vn", "libopus", "32k"):
            self.assertIn(flag, ffmpeg_cmd)
        self.assertEqual(ffmpeg_cmd[ffmpeg_cmd.index("-ac") + 1], "1")  # 單聲道

    def test_download_requests_bestaudio(self):
        run = ScriptedRun(self.probe_ok(), self.download_ok(), self.ffmpeg_ok())

        self.extract(run)

        download_cmd, _ = run.calls[1]
        self.assertEqual(download_cmd[download_cmd.index("-f") + 1], "bestaudio/best")

    def test_leftover_part_file_is_not_picked_as_source(self):
        def download_with_leftover(cmd, kwargs):
            (self.workdir / f"{VID}.src.webm.part").write_bytes(b"partial")
            (self.workdir / f"{VID}.src.webm").write_bytes(b"complete")
            return completed()

        run = ScriptedRun(self.probe_ok(), download_with_leftover, self.ffmpeg_ok())

        self.extract(run)

        ffmpeg_cmd, _ = run.calls[2]
        src_arg = ffmpeg_cmd[ffmpeg_cmd.index("-i") + 1]
        self.assertTrue(src_arg.endswith(f"{VID}.src.webm"))  # 不能拿 .part 去轉


class TestPrecheckFailures(ExtractAudioTestBase):
    def test_live_stream_is_rejected_before_download(self):
        run = ScriptedRun(self.probe_ok(is_live=True, live_status="is_live"))

        self.assert_fails_and_cleans_workdir(run, AudioLiveStream, LIVE_STREAM, 400)
        self.assertEqual(len(run.calls), 1)  # 只探測，沒下載

    def test_upcoming_premiere_counts_as_live_stream(self):
        # 即將首播同樣沒有完整音訊可抽，歸 400 而非讓下載失敗流到 502
        run = ScriptedRun(self.probe_ok(is_live=False, live_status="is_upcoming"))

        self.assert_fails_and_cleans_workdir(run, AudioLiveStream, LIVE_STREAM, 400)

    def test_over_duration_limit_returns_413(self):
        run = ScriptedRun(self.probe_ok(duration=601))

        self.assert_fails_and_cleans_workdir(
            run, AudioTooLong, AUDIO_TOO_LONG, 413, max_duration=600
        )
        self.assertEqual(len(run.calls), 1)

    def test_duration_exactly_at_limit_is_allowed(self):
        run = ScriptedRun(self.probe_ok(duration=600), self.download_ok(), self.ffmpeg_ok())

        output = self.extract(run, max_duration=600)

        self.assertTrue(output.exists())

    def test_missing_duration_on_normal_video_is_allowed(self):
        # 罕見的 metadata 缺 duration：放行，交給抽取逾時兜底
        run = ScriptedRun(self.probe_ok(duration=None), self.download_ok(), self.ffmpeg_ok())

        output = self.extract(run, max_duration=600)

        self.assertTrue(output.exists())


class TestProbeFailures(ExtractAudioTestBase):
    def _probe_error(self, stderr):
        return ScriptedRun(lambda cmd, kwargs: completed(returncode=1, stderr=stderr))

    def test_nonexistent_video_maps_to_404(self):
        run = self._probe_error(f"ERROR: [youtube] {VID}: Video unavailable")
        self.assert_fails_and_cleans_workdir(run, AudioUnavailable, VIDEO_NOT_FOUND, 404)

    def test_private_video_maps_to_404(self):
        run = self._probe_error(f"ERROR: [youtube] {VID}: Private video. Sign in if...")
        self.assert_fails_and_cleans_workdir(run, AudioUnavailable, VIDEO_NOT_FOUND, 404)

    def test_unknown_probe_failure_maps_to_502(self):
        run = self._probe_error("ERROR: unable to download webpage (network is unreachable)")
        self.assert_fails_and_cleans_workdir(run, AudioExtractError, AUDIO_EXTRACT_FAILED, 502)

    def test_probe_timeout_maps_to_502(self):
        def timeout_handler(cmd, kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        run = ScriptedRun(timeout_handler)
        self.assert_fails_and_cleans_workdir(run, AudioExtractError, AUDIO_EXTRACT_FAILED, 502)

    def test_garbled_probe_output_maps_to_502(self):
        run = ScriptedRun(lambda cmd, kwargs: completed(stdout="這不是 JSON"))
        self.assert_fails_and_cleans_workdir(run, AudioExtractError, AUDIO_EXTRACT_FAILED, 502)


class TestExtractionFailures(ExtractAudioTestBase):
    def test_download_error_maps_to_502(self):
        run = ScriptedRun(
            self.probe_ok(),
            lambda cmd, kwargs: completed(returncode=1, stderr="ERROR: fragment not found"),
        )
        self.assert_fails_and_cleans_workdir(run, AudioExtractError, AUDIO_EXTRACT_FAILED, 502)

    def test_download_timeout_maps_to_502(self):
        def timeout_handler(cmd, kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        run = ScriptedRun(self.probe_ok(), timeout_handler)
        self.assert_fails_and_cleans_workdir(run, AudioExtractError, AUDIO_EXTRACT_FAILED, 502)

    def test_download_succeeds_but_source_file_missing_maps_to_502(self):
        run = ScriptedRun(self.probe_ok(), lambda cmd, kwargs: completed())
        self.assert_fails_and_cleans_workdir(run, AudioExtractError, AUDIO_EXTRACT_FAILED, 502)

    def test_only_part_file_left_counts_as_missing_source(self):
        def download_only_part(cmd, kwargs):
            (self.workdir / f"{VID}.src.webm.part").write_bytes(b"partial")
            return completed()

        run = ScriptedRun(self.probe_ok(), download_only_part)
        self.assert_fails_and_cleans_workdir(run, AudioExtractError, AUDIO_EXTRACT_FAILED, 502)

    def test_ffmpeg_error_maps_to_502(self):
        run = ScriptedRun(
            self.probe_ok(),
            self.download_ok(),
            lambda cmd, kwargs: completed(returncode=1, stderr="Invalid data found"),
        )
        self.assert_fails_and_cleans_workdir(run, AudioExtractError, AUDIO_EXTRACT_FAILED, 502)

    def test_ffmpeg_succeeds_but_output_missing_maps_to_502(self):
        run = ScriptedRun(self.probe_ok(), self.download_ok(), lambda cmd, kwargs: completed())
        self.assert_fails_and_cleans_workdir(run, AudioExtractError, AUDIO_EXTRACT_FAILED, 502)

    def test_empty_output_file_maps_to_502(self):
        def ffmpeg_empty(cmd, kwargs):
            (self.workdir / f"{VID}.ogg").write_bytes(b"")
            return completed()

        run = ScriptedRun(self.probe_ok(), self.download_ok(), ffmpeg_empty)
        self.assert_fails_and_cleans_workdir(run, AudioExtractError, AUDIO_EXTRACT_FAILED, 502)

    def test_unexpected_exception_still_cleans_workdir(self):
        def broken_handler(cmd, kwargs):
            raise RuntimeError("模擬程式 bug")

        run = ScriptedRun(self.probe_ok(), broken_handler)

        with self.assertRaises(RuntimeError):
            self.extract(run)
        self.assertFalse(self.workdir.exists())  # 連未預期例外都不留殘檔


class TestExtractTimeout(unittest.TestCase):
    def test_short_video_keeps_tight_base_timeout(self):
        self.assertEqual(extract_timeout(0), EXTRACT_TIMEOUT_BASE)
        self.assertEqual(extract_timeout(60), 124)

    def test_timeout_scales_with_duration(self):
        self.assertEqual(extract_timeout(1200), 200)  # 20 分鐘影片

    def test_two_hour_limit_fits_within_max(self):
        # 固定 120 秒會讓上限內的長片必然逾時——這是動態逾時存在的理由
        self.assertEqual(extract_timeout(7200), EXTRACT_TIMEOUT_MAX)

    def test_timeout_is_capped(self):
        self.assertEqual(extract_timeout(100_000), EXTRACT_TIMEOUT_MAX)


class TestErrorContract(unittest.TestCase):
    def test_all_errors_share_base_class_for_route_handling(self):
        # 路由只攔 AudioExtractionError 一種，靠 status_code 分流
        for exc_type, status in (
            (AudioUnavailable, 404),
            (AudioLiveStream, 400),
            (AudioTooLong, 413),
            (AudioExtractError, 502),
        ):
            self.assertTrue(issubclass(exc_type, AudioExtractionError))
            self.assertEqual(exc_type().status_code, status)

    def test_detail_is_not_leaked_in_args(self):
        e = AudioUnavailable("stderr 裡的內部訊息")
        self.assertEqual(e.args[0], VIDEO_NOT_FOUND)  # 回應端只看得到契約碼
        self.assertEqual(e.detail, "stderr 裡的內部訊息")


if __name__ == "__main__":
    unittest.main()
