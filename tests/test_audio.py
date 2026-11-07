"""Microphone tests judge the same sustained input as dictation."""

import io
import math
import shutil
import struct
import wave

import pytest

from nabria import audio
from nabria.recorder import LEVEL_WARMUP_FRAMES, RATE, SILENT_DBFS


def write_wav(path, body):
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(RATE)
        sink.writeframes(struct.pack(f"<{len(body)}h", *body))
    return path


def paused_signal():
    voice = [round(1000 * math.sin(2 * math.pi * 220 * index / RATE)) for index in range(RATE)]
    return [0] * LEVEL_WARMUP_FRAMES + voice + [0] * (11 * RATE)


def test_a_pause_does_not_make_the_microphone_test_reject_sustained_input(tmp_path):
    path = write_wav(tmp_path / "paused.wav", paused_signal())
    assert audio._rms_dbfs(path) < -42
    assert audio._activity_dbfs(path) > -42


@pytest.mark.parametrize("body", [
    [0] * RATE,
    [30000] + [0] * RATE,
    [40 if index % 2 else -40 for index in range(RATE)],
])
def test_silence_click_and_low_stationary_noise_do_not_pass_the_mic_test(tmp_path, body):
    path = write_wav(tmp_path / "noise.wav", [0] * LEVEL_WARMUP_FRAMES + body)
    assert audio._activity_dbfs(path) < -42


def test_opening_pop_is_not_microphone_activity(tmp_path):
    path = write_wav(tmp_path / "pop.wav", [20000] * LEVEL_WARMUP_FRAMES + [0] * RATE)
    assert audio._activity_dbfs(path) == SILENT_DBFS


def test_linux_measure_reports_activity_rather_than_the_diluted_mean(tmp_path, monkeypatch):
    source = write_wav(tmp_path / "source.wav", paused_signal())

    class FinishedCapture:
        def __init__(self, command, **kwargs):
            shutil.copyfile(source, command[-1])
            self.stderr = io.BytesIO()

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(audio.subprocess, "Popen", FinishedCapture)
    assert audio.measure() > -42
