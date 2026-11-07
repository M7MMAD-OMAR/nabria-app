"""Exercise the WASAPI adapter's PCM and shutdown paths without a microphone."""

import threading
import time
import wave
import math
import struct
import ast
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from nabria.windows import audio
from nabria.recorder import LEVEL_WARMUP_FRAMES, MissingRecorder, RATE


class Stream:
    def __init__(self, **kwargs):
        assert kwargs["samplerate"] == RATE
        self.closed = False
        self.frames = 0

    def start(self):
        pass

    def read(self, count):
        time.sleep(0.001)
        self.frames += count
        return b"\x00\x10" * count, False

    def close(self):
        self.closed = True

    def abort(self):
        pass


def test_capture_finishes_a_readable_wav_and_shared_measurement(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "_sd", lambda: SimpleNamespace(
        RawInputStream=Stream, WasapiSettings=lambda **kwargs: kwargs,
    ))
    monkeypatch.setattr(audio, "sources", lambda: [{"id": 1, "default": True}])
    recorder = audio.Recorder(tmp_path / "speech.wav")
    recorder.start()
    deadline = time.monotonic() + 2
    while recorder.activity_dbfs <= -42 and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    recorder.stop()
    recorder.stop()  # daemon finalization can safely call stop again
    assert recorder.measured
    assert recorder.rms_dbfs == pytest.approx(-18.0618, abs=0.001)
    assert recorder.activity_dbfs == pytest.approx(-18.0618, abs=0.001)
    assert recorder.stream.closed
    assert not recorder.reader.is_alive()
    with wave.open(str(recorder.destination)) as source:
        assert source.getframerate() == RATE
        assert source.getnframes() == recorder.total_frames


def test_missing_capture_device_is_reported_as_recording_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "_sd", lambda: SimpleNamespace(
        query_devices=lambda **kwargs: {"name": "unplugged"},
    ))
    monkeypatch.setattr(audio, "sources", list)
    with pytest.raises(MissingRecorder, match="No WASAPI"):
        audio.Recorder(tmp_path / "missing.wav").start()


def paused_recorder(tmp_path):
    recorder = audio.Recorder(tmp_path / "paused.wav")
    voice = [round(1000 * math.sin(2 * math.pi * 220 * index / RATE)) for index in range(RATE)]
    data = [0] * LEVEL_WARMUP_FRAMES + voice + [0] * (11 * RATE)
    recorder._observe(struct.pack(f"<{len(data)}h", *data))
    recorder.start = lambda: None
    recorder.stop = lambda: None
    return recorder


def test_windows_measure_reports_activity_despite_a_long_pause(tmp_path, monkeypatch):
    recorder = paused_recorder(tmp_path)
    assert recorder.rms_dbfs < -42
    monkeypatch.setattr(audio, "Recorder", lambda destination: recorder)
    monkeypatch.setattr(audio, "time", SimpleNamespace(sleep=lambda seconds: None))
    assert audio.measure() > -42


def test_windows_backend_mic_test_uses_the_shared_activity_gate(tmp_path, monkeypatch):
    recorder = paused_recorder(tmp_path)
    assert recorder.rms_dbfs < -42
    monkeypatch.setattr(audio, "Recorder", lambda destination: recorder)
    # Execute the real task method without importing the Win32 desktop boundary
    # on Linux. The acoustic decision is independent of native window handles.
    source = Path(__file__).resolve().parents[1] / "src/nabria/windows/backend.py"
    tree = ast.parse(source.read_text())
    backend_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Backend")
    method = next(node for node in backend_class.body if isinstance(node, ast.FunctionDef) and node.name == "_task")
    ticks = iter([0, 5])
    from nabria import config
    namespace = {"__package__": "nabria.windows", "Path": Path, "tempfile": tempfile,
                 "config": config, "time": SimpleNamespace(monotonic=lambda: next(ticks))}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    task = SimpleNamespace(task_cancel=threading.Event(), settings={"silence_threshold_dbfs": -42},
                           task_running=True)
    emitted = []
    task.emit = lambda **event: emitted.append(event)
    namespace["_task"](task, "mic_test", {})
    assert next(event for event in emitted if event["event"] == "mic_result")["heard"] is True
    assert not task.task_running
