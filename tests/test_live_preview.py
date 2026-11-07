"""Live text is advisory; the WAV and final delivery remain authoritative."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest


gi = pytest.importorskip("gi", reason="PyGObject is not installed")
gi.require_version("Gtk", "4.0")
try:
    from nabria import app
except (ImportError, ValueError) as exc:
    pytest.skip(f"GTK 4 is unavailable: {exc}", allow_module_level=True)


def bare_daemon(recorder):
    daemon = app.Daemon.__new__(app.Daemon)
    daemon.recording = recorder
    daemon.live_stop = None
    daemon.live_session = None
    daemon.live_result = None
    daemon.settings = {}
    daemon.jobs = 0
    daemon.jobs_lock = threading.Lock()
    daemon.orb = SimpleNamespace(set_transcript=lambda text: None)
    daemon.log = lambda message: None
    return daemon


def test_live_preview_does_not_start_for_a_non_streaming_backend(monkeypatch):
    recorder = SimpleNamespace(read_live=lambda offset: (b"", offset))
    daemon = bare_daemon(recorder)
    daemon.whisper = object()
    monkeypatch.setattr(app.threading, "Thread", lambda *a, **kw: pytest.fail("unexpected worker"))
    daemon._start_live(recorder)
    assert daemon.live_stop is None


def test_live_preview_delivers_pcm_off_the_capture_thread_and_closes(monkeypatch):
    recorder = SimpleNamespace(read_live=lambda offset: (b"pcm audio" if offset == 0 else b"", 10))
    daemon = bare_daemon(recorder)
    stop = threading.Event()
    sent = []
    closed = []
    queued = []

    def feed(data):
        sent.append(data)
        stop.set()

    def start_live(callback):
        callback("provisional words")
        return SimpleNamespace(feed=feed, close=lambda: closed.append(True))

    monkeypatch.setattr(app.GLib, "idle_add", lambda *args: queued.append(args))
    daemon._preview(recorder, stop, start_live)
    assert sent == [b"pcm audio"]
    assert closed == [True]
    assert queued[0][1:] == (recorder, stop, "provisional words")


@pytest.mark.parametrize("state", ["stopped", "cancelled", "new_take"])
def test_a_stale_live_response_cannot_replace_the_visible_take(state):
    recorder = object()
    daemon = bare_daemon(recorder)
    words = []
    daemon.orb.set_transcript = words.append
    stop = threading.Event()
    if state == "new_take":
        daemon.recording = object()
    else:
        stop.set()
        daemon.recording = None
    assert not daemon._preview_text(recorder, stop, "stale words")
    assert words == []


def test_only_the_active_recording_can_receive_live_text():
    recorder = object()
    daemon = bare_daemon(recorder)
    words = []
    daemon.orb.set_transcript = words.append
    assert not daemon._preview_text(recorder, threading.Event(), "current words")
    assert words == ["current words"]


def test_cancellation_does_not_wait_for_a_warming_live_engine(monkeypatch):
    recorder = SimpleNamespace(destination="take.wav", read_live=lambda offset: pytest.fail("audio sent after cancellation"))
    daemon = bare_daemon(recorder)
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    def start_live(callback):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(feed=lambda data: None, close=closed.set)

    daemon.whisper = SimpleNamespace(start_live=start_live)
    daemon._start_live(recorder)
    assert entered.wait(2)
    daemon._stop_live()
    assert daemon.live_stop is None
    assert not closed.is_set()
    release.set()
    assert closed.wait(2)


@pytest.mark.parametrize("fault", ["connect", "read", "send"])
def test_preview_failure_leaves_the_authoritative_recording_untouched(fault):
    path = object()

    def read(offset):
        if fault == "read":
            raise ValueError("fell behind")
        return b"audio", 5

    recorder = SimpleNamespace(read_live=read, destination=path)
    daemon = bare_daemon(recorder)
    lines = []
    closed = []
    daemon.log = lines.append

    def start_live(callback):
        if fault == "connect":
            raise ConnectionError("could not connect")

        def send(data):
            raise ConnectionError("connection lost")

        return SimpleNamespace(feed=send, close=lambda: closed.append(True))

    daemon._preview(recorder, threading.Event(), start_live)
    assert daemon.recording is recorder
    assert recorder.destination is path
    assert any("keeping full-take transcription" in line for line in lines)
    assert closed == ([] if fault == "connect" else [True])


@pytest.mark.parametrize("superseded", [False, True])
def test_live_failure_notifies_only_the_current_take(monkeypatch, superseded):
    recorder = SimpleNamespace(destination="take.wav")
    daemon = bare_daemon(recorder)
    words, notices, queued = [], [], []
    daemon.orb.set_transcript = words.append
    stop = threading.Event()
    monkeypatch.setattr(app.GLib, "idle_add", lambda callback, *args: queued.append((callback, args)))
    monkeypatch.setattr(app.notify, "send", lambda *args, **kwargs: notices.append(args))
    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.run = target
        def start(self):
            self.run()
    monkeypatch.setattr(app.threading, "Thread", ImmediateThread)
    def unavailable(callback):
        raise ConnectionError("live connection failed")
    daemon._preview(recorder, stop, unavailable)
    if superseded:
        daemon.recording = object()
    for callback, args in queued:
        callback(*args)
    assert bool(notices) is not superseded
    assert words == ([] if superseded else [""])
    if not superseded:
        daemon._preview_text(recorder, stop, "late provisional text")
        assert words == [""]


def test_a_finished_take_does_not_hide_a_new_recording():
    recorder = object()
    daemon = bare_daemon(recorder)
    daemon.orb.flash = lambda *args: pytest.fail("old take changed the live indicator")
    assert not daemon._done("previous take")


def test_preview_text_never_reaches_history_or_paste_before_final_transcription(tmp_path, monkeypatch):
    path = tmp_path / "take.wav"
    path.write_bytes(b"authoritative audio")
    recorder = SimpleNamespace(
        destination=path, stop=lambda: None, error="", total_frames=16000,
        seconds=1.0, rms_dbfs=-20.0, max_peak_dbfs=-5.0, measured=True,
        recent_stderr=lambda: "",
    )
    daemon = bare_daemon(recorder)
    daemon.settings = {"keep_audio": False}
    daemon._note_gpu_fallback = lambda: None
    daemon.whisper = SimpleNamespace(transcribe=lambda path: "final authoritative words")
    saved = []
    pasted = []
    monkeypatch.setattr(app.history, "append", lambda *args: saved.append(args))
    monkeypatch.setattr(app.inject, "deliver", lambda text, *args, **kwargs: pasted.append(text) or "paste")
    monkeypatch.setattr(app.GLib, "idle_add", lambda *args: None)
    daemon._preview_text(recorder, threading.Event(), "provisional")
    daemon._preview_text(recorder, threading.Event(), "provisional words")
    assert saved == pasted == []
    daemon.recording = None
    daemon._transcribe(recorder)
    assert len(saved) == 1
    assert saved[0][0] == "final authoritative words"
    assert pasted == ["final authoritative words"]


def test_a_cancelled_worker_does_not_open_a_live_connection():
    recorder = object()
    daemon = bare_daemon(recorder)
    stop = threading.Event()
    stop.set()
    daemon._preview(recorder, stop, lambda callback: pytest.fail("cancelled take opened a connection"))


def test_stop_live_interrupts_the_active_socket_without_joining():
    recorder = object()
    daemon = bare_daemon(recorder)
    stop = threading.Event()
    calls = []
    daemon.live_stop = stop
    daemon.live_session = (stop, SimpleNamespace(cancel=lambda: calls.append("cancel")))
    daemon._stop_live()
    assert stop.is_set()
    assert calls == ["cancel"]
    assert daemon.live_session is None


def test_final_overlay_closes_after_the_result_is_delivered():
    daemon = bare_daemon(None)
    hidden = []
    daemon.orb = SimpleNamespace(hide=lambda: hidden.append(True))
    assert not daemon._done("the complete final sentence")
    assert hidden == [True]
