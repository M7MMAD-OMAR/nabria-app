"""Transcript cleaning.

Whisper reliably invents a polite sentence out of room tone, in whichever
language it guessed. Those are training data bleeding through, never something
anyone said, and typing them into a document is worse than typing nothing.
The RMS gate catches most of it; this catches the rest.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from nabria import gpu, whisper
from nabria.whisper import clean


def test_leading_space_is_trimmed():
    # Whisper emits a leading space on essentially every segment.
    assert clean(" hello there") == "hello there"


def test_internal_whitespace_is_collapsed():
    assert clean("hello\n  there\tworld") == "hello there world"


def test_english_hallucinations_are_dropped():
    for phrase in ("Thanks for watching!", "thank you", "  Bye. ", "You"):
        assert clean(phrase) == "", phrase


def test_arabic_hallucinations_are_dropped():
    assert clean("شكرا للمشاهدة") == ""
    assert clean("اشتركوا في القناة") == ""


def test_arabic_matching_survives_diacritics_and_alef_variants():
    # The same sentence comes back spelled differently run to run, so the
    # filter normalises before matching -- otherwise it catches one spelling
    # and lets the next one through.
    assert clean("شُكْرًا للمشاهدة") == ""
    assert clean("شكراً للمشاهدة.") == ""


def test_a_real_sentence_containing_a_hallucination_phrase_is_kept():
    # The filter matches whole transcripts, not substrings. Someone dictating
    # "thank you for the report" must not lose it.
    assert clean("thank you for the report") == "thank you for the report"
    assert clean("شكرا للمشاهدة يا شباب") == "شكرا للمشاهدة يا شباب"


def test_empty_and_whitespace_only():
    assert clean("") == ""
    assert clean("   \n ") == ""


# -- starting the engine ---------------------------------------------------
#
# A GPU that cannot be used is not a reason to lose the take. Measured on a
# hybrid laptop: the discrete card was busy, whisper-server aborted inside
# `whisper_model_load` with a null buffer, and the audio went to failed/ with
# "whisper server exited with code -6" as the only explanation anywhere --
# because the engine's own stderr was being sent to DEVNULL.


CRASH = """import os, signal, sys
sys.stderr.write("ggml_vulkan: Device memory allocation of size 1623920640 failed\\n")
sys.stderr.write("/build/ggml/src/ggml-backend.cpp:60: GGML_ASSERT(buffer) failed\\n")
sys.stderr.flush()
os.kill(os.getpid(), signal.SIGABRT)
"""

# Binds the port it is given and then sits still, which is all `_spawn` waits
# for -- it treats an accepted connection as readiness.
SERVE = """import socket, sys, time
listener = socket.socket()
listener.bind(("127.0.0.1", int(sys.argv[1])))
listener.listen(8)
time.sleep(300)
"""


@pytest.fixture
def engine(tmp_path):
    """A WhisperServer whose binary and model exist but are never really run."""
    binary = tmp_path / "whisper-server"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_bytes(b"x")

    lines: list[str] = []
    server = whisper.WhisperServer(
        {
            "server_binary": str(binary), "model": str(model),
            "language": "ar", "threads": 4, "vocabulary": "",
            "server_port": 0, "idle_unload_seconds": 0, "gpu_select": "auto",
        },
        lines.append,
    )
    server.log_lines = lines  # type: ignore[attr-defined]
    yield server
    server.stop()


def _stage(server, monkeypatch, *, gpu_available: bool):
    """Swap the real binary for a crash on GPU and a listener on CPU."""
    server.device = gpu.Plan(gpu_available, 1 if gpu_available else None, "test device")
    real_popen = subprocess.Popen

    def fake(command, **kwargs):
        port = command[command.index("--port") + 1]
        on_gpu = "-ng" not in command
        script = CRASH if (on_gpu and gpu_available) else SERVE
        return real_popen([sys.executable, "-c", script, port], **kwargs)

    monkeypatch.setattr(whisper.subprocess, "Popen", fake)


def test_a_gpu_that_cannot_start_falls_back_to_the_cpu(engine, monkeypatch):
    _stage(engine, monkeypatch, gpu_available=True)

    engine.ensure()

    assert engine.is_running(), "the take was lost to a GPU that could not start"
    assert engine.gpu_failed is True
    assert any("falling back to the CPU" in line for line in engine.log_lines)


def test_the_engines_own_reason_reaches_the_log(engine, monkeypatch):
    """The signal number alone is unattributable.

    "-6" is the same answer for a busy GPU, a missing driver and a corrupt
    model. The engine says which on its stderr, and that used to go to
    DEVNULL -- so the log recorded a failure it could not explain, in the one
    file this project tells people to read first.
    """
    _stage(engine, monkeypatch, gpu_available=True)

    engine.ensure()

    reported = [line for line in engine.log_lines if "GPU start failed" in line][0]
    assert "GGML_ASSERT" in reported
    assert "Device memory allocation" in reported


def test_the_fallback_is_remembered_for_the_session(engine, monkeypatch):
    # Retrying the card on every take would cost a crash and its startup
    # timeout each time, to arrive at the same answer.
    _stage(engine, monkeypatch, gpu_available=True)
    engine.ensure()
    engine.stop()

    engine.ensure()

    starts = [line for line in engine.log_lines if "starting whisper server" in line]
    assert len(starts) == 3, "GPU, CPU, then CPU again"
    assert starts[-1].endswith("on the CPU")


def test_a_cpu_start_that_fails_is_still_an_error(engine, monkeypatch):
    """The fallback must not swallow a real fault.

    With no GPU in the picture there is nothing left to fall back to, so a
    dead engine has to be reported rather than retried into silence.
    """
    engine.device = gpu.Plan(False, None, "test device: CPU")
    real_popen = subprocess.Popen
    monkeypatch.setattr(
        whisper.subprocess, "Popen",
        lambda command, **kwargs: real_popen([sys.executable, "-c", CRASH], **kwargs),
    )

    with pytest.raises(RuntimeError, match="GGML_ASSERT"):
        engine.ensure()
    assert engine.gpu_failed is False, "there was no GPU attempt to blame"


def test_a_missing_binary_is_not_retried_on_the_cpu(engine):
    # It would fail identically the second time, and "the GPU start failed"
    # would be a wrong explanation for a file that is not there.
    engine.settings["server_binary"] = "/nonexistent/whisper-server"
    with pytest.raises(FileNotFoundError):
        engine.ensure()
    assert engine.gpu_failed is False


# A writer that holds the pipe open past the process's death is not what
# breaks this: the poll loop only notices the exit on its next 0.2s tick, by
# which time any prompt writer has long flushed. What broke it was the drain
# thread not being SCHEDULED, measured 4 times in 120 runs under load. So the
# thread is stalled directly here, which is that condition made deterministic.
CRASH_WITH_SLOW_DRAIN = """import os, signal, sys
sys.stderr.write("ggml_vulkan: Device memory allocation failed\\n")
sys.stderr.write("/build/ggml/src/ggml-backend.cpp:60: GGML_ASSERT(buffer) failed\\n")
sys.stderr.flush()
os.kill(os.getpid(), signal.SIGABRT)
"""


def test_the_reason_survives_a_drain_thread_that_has_not_run(engine, monkeypatch):
    """The engine's reason must not depend on thread scheduling.

    `_stderr_summary` used to sleep a tenth of a second and hope the reader
    had finished. That is a guess about the scheduler, and under load it lost
    the reason 4 times in 120 runs, leaving exactly the bare "exited with code
    -6" that keeping stderr was meant to eliminate.

    The starvation is reproduced here by delaying the reader past any fixed
    sleep, so this fails against the sleeping version instead of flaking.
    """
    engine.device = gpu.Plan(False, None, "test device: CPU")
    real_popen = subprocess.Popen
    monkeypatch.setattr(
        whisper.subprocess, "Popen",
        lambda command, **kwargs: real_popen(
            [sys.executable, "-c", CRASH_WITH_SLOW_DRAIN], **kwargs
        ),
    )
    # Stand in for a thread the scheduler has not got to yet.
    real_drain = engine._drain_stderr
    monkeypatch.setattr(
        engine, "_drain_stderr",
        lambda process: (time.sleep(0.5), real_drain(process))[1],
    )

    with pytest.raises(RuntimeError) as caught:
        engine.ensure()

    assert "GGML_ASSERT" in str(caught.value), (
        "the engine's reason was lost to a race; the log would say only a signal number"
    )
