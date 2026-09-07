"""Exercise the WASAPI adapter's PCM and shutdown paths without a microphone."""

import threading
import time
import wave
from types import SimpleNamespace

import pytest

from nabria.windows import audio
from nabria.recorder import MissingRecorder, RATE


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
    while not recorder.measured and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    recorder.stop()
    recorder.stop()  # daemon finalization can safely call stop again
    assert recorder.measured
    assert recorder.rms_dbfs == pytest.approx(-18.0618, abs=0.001)
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
