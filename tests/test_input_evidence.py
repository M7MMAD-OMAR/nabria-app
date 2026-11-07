"""Regression cases for pause dilution, quiet speech and recovery of uncertain audio."""
import math
import struct
import threading
import wave
from types import SimpleNamespace

import pytest

from nabria.recorder import LEVEL_WARMUP_FRAMES, RATE, Recorder, SILENT_DBFS


def observe(tmp_path, samples, chunks=8192):
    take = Recorder(tmp_path / "input.wav")
    data = struct.pack(f"<{len(samples)}h", *samples)
    for offset in range(0, len(data), chunks):
        take._observe(data[offset:offset + chunks])
    return take


def tone(seconds, amplitude=1000):
    return [round(amplitude * math.sin(2 * math.pi * 300 * i / RATE))
            for i in range(round(seconds * RATE))]


def test_a_pause_does_not_erase_sustained_sound(tmp_path):
    take = observe(tmp_path, [0] * LEVEL_WARMUP_FRAMES + tone(1) + [0] * (11 * RATE))
    assert take.rms_dbfs < -42
    assert take.activity_dbfs > -42
    assert not take.unheard(-42, 12)[0]


def test_one_loud_click_is_not_sustained_input(tmp_path):
    take = observe(tmp_path, [0] * LEVEL_WARMUP_FRAMES + [32767] + [0] * (12 * RATE))
    assert take.max_peak_dbfs > -1
    assert take.activity_dbfs == SILENT_DBFS
    assert take.unheard(-42, 12)[0]


def test_low_uniform_noise_is_still_below_the_gate(tmp_path):
    take = observe(tmp_path, [0] * LEVEL_WARMUP_FRAMES + [60, -60] * (6 * RATE))
    assert take.activity_dbfs < -42
    assert take.unheard(-42, 12)[0]


def test_device_open_pop_does_not_count_as_activity(tmp_path):
    take = observe(tmp_path, [32000] * LEVEL_WARMUP_FRAMES + [0] * (12 * RATE))
    assert take.activity_dbfs == SILENT_DBFS


def test_odd_capture_reads_preserve_sample_alignment_and_clock(tmp_path):
    samples = [0] * LEVEL_WARMUP_FRAMES + tone(0.4)
    whole = observe(tmp_path, samples)
    fragmented = observe(tmp_path, samples, chunks=101)
    assert fragmented.total_frames == len(samples)
    assert fragmented.samples == whole.samples
    assert fragmented.rms_dbfs == whole.rms_dbfs
    assert fragmented.activity_dbfs == whole.activity_dbfs


def test_activity_statistics_are_bounded(tmp_path):
    take = observe(tmp_path, tone(34))
    assert len(take._activity_windows) <= 10
    assert len(take._activity_pending) < 640
    assert take.live_bytes == 34 * RATE * 2


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    pytest.importorskip("gi")
    from nabria import app

    daemon = app.Daemon.__new__(app.Daemon)
    daemon.settings = {"model": "fixture", "language": "ar", "nemo_vocabulary": "",
                       "silence_threshold_dbfs": -42, "always_copy": True, "silent_notice_after": 0}
    calls = []
    daemon.whisper = SimpleNamespace(transcribe=lambda path: calls.append("decode") or "كلام واضح")
    daemon.log = lambda message: None
    daemon._note_gpu_fallback = lambda: None
    daemon.silent_run = 0
    daemon.recording = None
    monkeypatch.setattr(app.GLib, "idle_add", lambda *args: 0)
    monkeypatch.setattr(app.history, "append", lambda text, *args: calls.append(("history", text)))
    monkeypatch.setattr(app.inject, "to_clipboard", lambda text: calls.append(("clipboard", text)))
    monkeypatch.setattr(app.inject, "deliver", lambda text, *args, **kwargs: calls.append(("paste", text)) or "test")
    monkeypatch.setattr(app.notify, "send", lambda *args, **kwargs: calls.append(("notice", args)))
    failed = tmp_path / "failed"
    monkeypatch.setattr(app, "FAILED_DIR", failed)
    path = tmp_path / "take.wav"
    path.write_bytes(b"original uncertain audio")
    take = SimpleNamespace(destination=path, error="", measured=True, total_frames=13 * RATE,
                           seconds=13, rms_dbfs=-48, activity_dbfs=-60, max_peak_dbfs=-27,
                           warned_unheard=False, live_bytes=22, stop=lambda: None,
                           recent_stderr=lambda: "")
    return app, daemon, take, calls, failed


def live_result(app, daemon, take, text="كلام هادئ حقيقي"):
    result = app.LiveResult(str(take.destination), daemon.whisper, daemon._live_key())
    result.expected_bytes = result.sent_bytes = take.live_bytes
    result.finalize = True
    result.text = text
    result.done.set()
    take.live_result = result
    return result


def test_low_mean_but_sustained_speech_reaches_recognition(delivery):
    _, daemon, take, calls, _ = delivery
    take.activity_dbfs = -33
    daemon._transcribe(take)
    assert "decode" in calls
    assert ("paste", "كلام واضح") in calls
    assert calls.index(("history", "كلام واضح")) < calls.index(("paste", "كلام واضح"))


def test_exact_final_live_text_survives_the_quiet_gate(delivery):
    app, daemon, take, calls, _ = delivery
    result = live_result(app, daemon, take)
    daemon._transcribe(take)
    assert "decode" not in calls
    assert ("history", result.text) in calls
    assert ("clipboard", result.text) in calls
    assert ("paste", result.text) in calls


@pytest.mark.parametrize("fault", ["take", "engine", "key", "finalize", "bytes", "unfinished"])
def test_untrusted_quiet_live_text_is_saved_without_pasting(delivery, fault):
    app, daemon, take, calls, failed = delivery
    result = live_result(app, daemon, take)
    if fault == "take": result.take = "another.wav"
    elif fault == "engine": result.engine = object()
    elif fault == "key": result.key = ("other", "ar", "")
    elif fault == "finalize": result.finalize = False
    elif fault == "bytes": result.sent_bytes -= 2
    else: result.done = SimpleNamespace(wait=lambda timeout: False)
    daemon._transcribe(take)
    assert [p.read_bytes() for p in failed.glob("quiet-*.wav")] == [b"original uncertain audio"]
    assert "decode" not in calls
    assert not any(isinstance(c, tuple) and c[0] in ["clipboard", "paste", "history"] for c in calls)


def test_digital_silence_does_not_paste_a_model_hallucination(delivery):
    app, daemon, take, calls, failed = delivery
    take.max_peak_dbfs = SILENT_DBFS
    live_result(app, daemon, take, "invented words")
    daemon._transcribe(take)
    assert not failed.exists()
    assert not any(isinstance(c, tuple) and c[0] in ["clipboard", "paste", "history"] for c in calls)


def test_isolated_click_cannot_rescue_a_matched_final_hallucination(delivery):
    app, daemon, original, calls, failed = delivery
    pcm = struct.pack(f"<{LEVEL_WARMUP_FRAMES + 1 + RATE}h",
                      *([0] * LEVEL_WARMUP_FRAMES + [32767] + [0] * RATE))
    take = Recorder(original.destination)
    take._observe(pcm)
    with wave.open(str(take.destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm)
    saved = take.destination.read_bytes()
    live_result(app, daemon, take, "invented words")
    daemon._transcribe(take)
    assert take.activity_dbfs == SILENT_DBFS
    assert [path.read_bytes() for path in failed.glob("quiet-*.wav")] == [saved]
    assert "decode" not in calls
    assert not any(isinstance(c, tuple) and c[0] in ["clipboard", "paste", "history"] for c in calls)


def test_only_current_take_preview_suppresses_the_silence_warning(delivery):
    app, daemon, take, calls, _ = delivery
    take.unheard = lambda *args: (True, 13, -48)
    result = live_result(app, daemon, take)
    result.preview = "كلام ظاهر"
    daemon.recording = take
    assert daemon._poll_silence() == app.GLib.SOURCE_CONTINUE
    assert not take.warned_unheard
    result.take = "older.wav"
    assert not daemon._has_live_preview(take)


def test_muted_input_probe_is_early_and_stale_results_do_not_notify(delivery, monkeypatch):
    app, daemon, take, calls, _ = delivery
    monkeypatch.setattr(app, "_default_input", lambda: ("Muted test mic", True))
    queued = []
    monkeypatch.setattr(app.GLib, "idle_add", lambda callback, *args: queued.append((callback, args)) or 1)
    daemon.recording = take
    daemon._probe_input(take)
    assert len(queued) == 1
    callback, args = queued.pop()
    daemon.recording = None
    callback(*args)
    assert not take.warned_unheard and not calls
    daemon.recording = take
    daemon._input_checked(take, "Muted test mic")
    assert take.warned_unheard
    # The notification thread is bounded by the local, patched sender.
    for _ in range(100):
        if calls: break
        threading.Event().wait(0.001)
    assert len([c for c in calls if c[0] == "notice"]) == 1
    assert daemon._poll_silence() == app.GLib.SOURCE_REMOVE


def test_muting_after_speech_is_reported_despite_existing_live_text(delivery, monkeypatch):
    app, daemon, take, calls, _ = delivery
    take.activity_dbfs = -30
    take.lock, take.peak_dbfs = threading.Lock(), -120
    daemon.recording = take
    daemon.orb = SimpleNamespace(set_level=lambda value: None)
    live_result(app, daemon, take).preview = "earlier speech"
    muted, queries = [False], []
    monkeypatch.setattr(app, "_default_input", lambda: queries.append(True) or ("Test input", muted[0]))
    monkeypatch.setattr(app.GLib, "idle_add", lambda callback, *args: callback(*args))
    class ImmediateThread:
        def __init__(self, target, args=(), **kwargs):
            self.run = lambda: target(*args)
        def start(self):
            self.run()
    monkeypatch.setattr(app.threading, "Thread", ImmediateThread)
    daemon._probe_input(take)
    muted[0] = True
    daemon._poll_level()
    assert len(queries) == 2
    assert take.warned_unheard
    assert len([call for call in calls if call[0] == "notice"]) == 1


def test_input_queries_are_throttled_and_never_overlap(delivery, monkeypatch):
    app, daemon, take, _, _ = delivery
    daemon.recording = take
    now, pending = [0.0], []
    monkeypatch.setattr(app.time, "monotonic", lambda: now[0])
    class DeferredThread:
        def __init__(self, target, args=(), **kwargs):
            self.run = lambda: target(*args)
        def start(self):
            pending.append(self.run)
    monkeypatch.setattr(app.threading, "Thread", DeferredThread)
    monkeypatch.setattr(app, "_default_input", lambda: ("Test input", False))
    daemon._check_input(take)
    now[0] = 10
    daemon._check_input(take)
    assert len(pending) == 1
    pending.pop()()
    daemon._check_input(take)
    assert len(pending) == 1
    pending.pop()()
    now[0] = 10.1
    daemon._check_input(take)
    assert not pending


@pytest.mark.parametrize("change", ["stopped", "speech_arrived"])
def test_delayed_device_query_cannot_post_an_obsolete_silence_warning(delivery, monkeypatch, change):
    app, daemon, take, calls, _ = delivery
    daemon.recording = take
    def query():
        if change == "stopped": daemon.recording = None
        else: take.activity_dbfs = -30
        return "Delayed input", False
    monkeypatch.setattr(app, "_default_input", query)
    daemon._warn_unheard(13, -42, take)
    assert not calls


def test_quiet_retention_is_bounded_and_leaves_other_failed_audio_alone(tmp_path):
    pytest.importorskip("gi")
    from nabria.app import _file_quiet_take
    failed = tmp_path / "failed"
    failed.mkdir()
    sentinel = failed / "existing-failure.wav"
    sentinel.write_bytes(b"keep")
    for index in range(5):
        source = tmp_path / "new.wav"
        source.write_bytes(bytes([index]) * 8)
        newest = _file_quiet_take(source, failed, lambda message: None, max_files=2, max_bytes=16)
    assert sentinel.read_bytes() == b"keep"
    assert len(list(failed.glob("quiet-*.wav"))) == 2
    assert newest.read_bytes() == bytes([4]) * 8


@pytest.mark.parametrize("change", ["recording", "started_and_stopped", "already_superseded"])
def test_finished_silence_notice_does_not_follow_a_new_take(delivery, monkeypatch, change):
    app, daemon, take, calls, _ = delivery
    daemon.settings["silent_notice_after"] = 1
    daemon.takes = 1
    take.take_number = 1
    if change == "already_superseded":
        daemon.takes = 2
    def delayed_query():
        daemon.takes = 2
        if change == "recording":
            daemon.recording = object()
        return "Old muted input", True
    monkeypatch.setattr(app, "_default_input", delayed_query)
    daemon._note_silent_take(take, -120, -42)
    assert not calls
    assert daemon.silent_run == 0
