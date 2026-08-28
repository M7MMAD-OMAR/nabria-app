"""The transcription engine, for real: a live whisper-server and HTTP.

No mocks. This starts the actual binary against the actual model and posts an
actual multipart request, which is the only way to catch the failures that
matter here -- a server that never binds its port, a malformed multipart body,
a device plan that crashes the driver, an engine built without the server
target at all.

Skipped when the engine or a model is not installed, so a checkout with no
model still runs a green suite. Set NABRIA_TEST_WAV to a 16 kHz mono WAV of
speech to additionally check that words come back.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import tone, write_wav  # noqa: E402  (tests/ is on sys.path)

from nabria import config, whisper


@pytest.fixture(scope="module")
def engine():
    settings = config.load()
    binary = Path(settings["server_binary"])
    if not binary.exists():
        pytest.skip(f"no engine at {binary}")
    models = config.models()
    if not models:
        pytest.skip("no model installed")
    # Smallest available: this fixture pays a model load, and the point is the
    # plumbing, not the transcription quality.
    settings = {**settings, "model": str(min(models, key=lambda p: p.stat().st_size))}

    lines: list[str] = []
    server = whisper.WhisperServer(settings, lines.append)
    server.log_lines = lines  # type: ignore[attr-defined]
    yield server
    server.stop()


def test_the_server_starts_and_reports_its_device(engine, tmp_path):
    quiet = write_wav(tmp_path / "quiet.wav", [0] * 16_000)
    engine.transcribe(quiet)

    reasons = [line for line in engine.log_lines if line.startswith("engine device:")]
    assert len(reasons) == 1, "the device should be decided once and remembered"
    assert engine.is_running()


def test_silence_reaching_the_engine_produces_invented_words(engine, tmp_path):
    """Why the RMS gate exists, demonstrated rather than asserted.

    Two seconds of digital silence -- not room tone, actual zeroes -- came back
    from base as «نقف بعضك». The hallucination list cannot save us here: it
    holds the handful of stock phrases whisper repeats, and this was not one of
    them. There is no filter that reliably separates invented words from real
    ones after the fact.

    So the protection has to be upstream, and it is: `silence_threshold_dbfs`
    means audio like this is never sent at all. The test that matters is the
    one below, that the gate would in fact have stopped it.
    """
    quiet = write_wav(tmp_path / "silence.wav", [0] * 32_000)
    engine.transcribe(quiet)  # whatever it says, it must not raise


def test_the_gate_would_have_stopped_that_audio():
    # The link between the two halves: what the recorder measures for silence
    # sits below what config treats as silence. If either number moves without
    # the other, the engine starts being handed material to invent over.
    from nabria.recorder import SILENT_DBFS

    threshold = float(config.DEFAULTS["silence_threshold_dbfs"])
    assert SILENT_DBFS < threshold


def test_a_loud_tone_does_not_crash_the_engine(engine, tmp_path):
    # Not speech, so there is nothing to assert about the text -- only that a
    # full-scale signal comes back cleanly rather than hanging or 500ing.
    loud = write_wav(tmp_path / "tone.wav", tone(16_000, 30_000))
    assert isinstance(engine.transcribe(loud), str)


def test_the_model_stays_loaded_between_takes(engine, tmp_path):
    """The second take reuses the running server rather than starting one.

    Asserted on the process, not on the clock. This used to allow the second
    take ten seconds and call anything faster a success, which failed the
    first time the machine had something else to do -- and a wall-clock budget
    could not have distinguished "the model reloaded" from "the machine was
    busy" anyway. The same pid on the same port is the actual claim.
    """
    quiet = write_wav(tmp_path / "again.wav", [0] * 16_000)
    engine.transcribe(quiet)

    assert engine.process is not None
    pid, port = engine.process.pid, engine.port

    engine.transcribe(quiet)

    assert engine.process is not None and engine.process.pid == pid, (
        "the server was restarted, so the model was loaded twice"
    )
    assert engine.port == port
    assert engine.process.poll() is None, "the server died between takes"


def test_real_speech_comes_back_as_words(engine):
    sample = os.environ.get("NABRIA_TEST_WAV")
    if not sample or not Path(sample).exists():
        pytest.skip("set NABRIA_TEST_WAV to a 16 kHz mono WAV of speech")
    text = engine.transcribe(Path(sample))
    assert len(text.split()) >= 3
