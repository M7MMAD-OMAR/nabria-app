"""The dictation daemon: control socket, state machine, GTK main loop.

One long-lived process owns the orb, the recorder and the whisper server. The
hotkey does not launch anything -- it sends one line to a Unix socket, which is
why pressing it feels instant and why a keypress can never race a second copy
of the app into existence.
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import config, history, inject, notify
from .orb import Orb
from .recorder import Recorder
from .whisper import WhisperServer

LEVEL_POLL_MS = 50


class Daemon:
    def __init__(self) -> None:
        self.settings = config.load()
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.log_file = config.LOG_PATH.open("a", encoding="utf-8", buffering=1)

        # A unique application id makes a second copy impossible: if one is
        # already running, the new process hands its activation over and exits.
        self.application = Gtk.Application(application_id=config.APP_ID)
        self.application.connect("activate", self._on_activate)
        self.started = False
        self.orb: Orb | None = None

        self.whisper = WhisperServer(self.settings, self.log)
        self.recorder: Recorder | None = None
        self.state = "idle"
        self.level_source = 0
        self.deadline_source = 0
        self.take_path = config.STATE_DIR / "take.wav"

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_file.write(f"{stamp} {message}\n")

    # -- GTK ---------------------------------------------------------------

    def _on_activate(self, application: Gtk.Application) -> None:
        # activate fires again every time another process tries to launch the
        # daemon. Re-running setup would rebind the socket and leak a second
        # orb, so the first call is the only one that does anything.
        if self.started:
            return
        self.started = True
        # Holding the application keeps the main loop alive even though no
        # window is on screen: the orb only exists while dictating.
        application.hold()
        for number in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, self._quit)
        self.orb = Orb(application, self.settings)
        self.orb.hide()
        self._serve()
        if self.settings.get("prewarm"):
            threading.Thread(target=self._prewarm, daemon=True).start()
        self.log("daemon ready")

    def _prewarm(self) -> None:
        try:
            self.whisper.ensure()
        except Exception as exc:  # noqa: BLE001
            self.log(f"prewarm failed: {exc}")

    def run(self) -> int:
        # An empty argv list makes GApplication skip its activate signal
        # entirely and exit immediately; argv[:1] is what it expects.
        return self.application.run(sys.argv[:1])

    # -- control socket ----------------------------------------------------

    def _serve(self) -> None:
        # A leftover socket file from a crashed daemon would make bind() fail,
        # so it is removed first; the runtime dir is per-session and per-user,
        # so nothing else can legitimately own this path.
        try:
            config.SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(config.SOCKET_PATH))
        os.chmod(config.SOCKET_PATH, 0o600)
        server.listen(8)

        def accept_loop() -> None:
            while True:
                connection, _ = server.accept()
                with connection:
                    command = connection.recv(64).decode("utf-8", "replace").strip()
                    reply = self.dispatch(command)
                    connection.sendall(reply.encode("utf-8"))

        threading.Thread(target=accept_loop, daemon=True, name="dictate-control").start()

    def dispatch(self, command: str) -> str:
        if command == "status":
            return self.state
        if command == "last":
            return history.last()
        if command in {"toggle", "start", "stop", "cancel"}:
            GLib.idle_add(self._handle, command)
            return "ok"
        if command == "quit":
            GLib.idle_add(self._quit)
            return "ok"
        return f"unknown command: {command}"

    def _quit(self) -> bool:
        self.whisper.stop()
        self.application.quit()
        return GLib.SOURCE_REMOVE

    def _handle(self, command: str) -> bool:
        try:
            if command == "cancel":
                self._cancel()
            elif command == "start" or (command == "toggle" and self.state == "idle"):
                self._start()
            elif command == "stop" or (command == "toggle" and self.state == "recording"):
                self._stop()
        except Exception:  # noqa: BLE001 - never let a bad take kill the daemon
            self.log(traceback.format_exc())
            self._fail("خطأ في الإملاء", str(config.LOG_PATH))
        return GLib.SOURCE_REMOVE

    # -- dictation ---------------------------------------------------------

    def _start(self) -> None:
        if self.state != "idle":
            return
        self.recorder = Recorder(self.take_path)
        self.recorder.start()
        self.state = "recording"
        assert self.orb
        self.orb.show("recording")
        self.level_source = GLib.timeout_add(LEVEL_POLL_MS, self._poll_level)
        limit = int(self.settings.get("max_seconds", 300))
        if limit > 0:
            self.deadline_source = GLib.timeout_add_seconds(limit, self._deadline)
        # Loading the model takes seconds on a cold start. Starting it now, in
        # parallel with the recording, hides that behind the time you spend
        # talking instead of adding it to the wait afterwards.
        threading.Thread(target=self._prewarm, daemon=True).start()
        self.log("recording started")

    def _poll_level(self) -> bool:
        if self.state != "recording" or not self.recorder or not self.orb:
            return GLib.SOURCE_REMOVE
        with self.recorder.lock:
            level = self.recorder.peak_dbfs
        self.orb.set_level(level)
        return GLib.SOURCE_CONTINUE

    def _deadline(self) -> bool:
        self.deadline_source = 0
        if self.state == "recording":
            self.log("hit max_seconds, stopping")
            self._stop()
        return GLib.SOURCE_REMOVE

    def _clear_timers(self) -> None:
        for attribute in ("level_source", "deadline_source"):
            source = getattr(self, attribute)
            if source:
                GLib.source_remove(source)
                setattr(self, attribute, 0)

    def _cancel(self) -> None:
        if self.state != "recording" or not self.recorder:
            return
        self._clear_timers()
        recorder, self.recorder = self.recorder, None
        self.state = "idle"
        threading.Thread(target=recorder.stop, daemon=True).start()
        assert self.orb
        self.orb.flash("error", 0.9)
        self.log("cancelled")

    def _stop(self) -> None:
        if self.state != "recording" or not self.recorder:
            return
        self._clear_timers()
        recorder, self.recorder = self.recorder, None
        self.state = "working"
        assert self.orb
        self.orb.show("working")
        threading.Thread(
            target=self._finish, args=(recorder,), daemon=True, name="dictate-transcribe"
        ).start()

    def _finish(self, recorder: Recorder) -> None:
        """Runs off the main loop: stop capture, transcribe, type."""
        started = time.monotonic()
        try:
            recorder.stop()
            if recorder.error:
                raise RuntimeError(recorder.error)
            if recorder.total_frames == 0:
                raise RuntimeError(f"no audio captured. {recorder.recent_stderr()}")

            threshold = float(self.settings.get("silence_threshold_dbfs", -42.0))
            if recorder.rms_dbfs <= threshold:
                self.log(f"silent take ({recorder.rms_dbfs:.1f} dBFS RMS), skipped")
                GLib.idle_add(self._done, "", "", 0.0)
                return

            text = self.whisper.transcribe(self.take_path)
            elapsed = time.monotonic() - started
            # On disk before it is typed: from here on, no failure downstream
            # can lose the words. `dictate last` reads them back.
            history.append(text, recorder.seconds, elapsed)
            if text and self.settings.get("always_copy"):
                inject.to_clipboard(text)
            backend = ""
            if text:
                backend = inject.deliver(text, str(self.settings.get("inject", "auto")))
            self.log(
                f"{recorder.seconds:.1f}s audio -> {len(text)} chars in {elapsed:.1f}s "
                f"via {backend or 'nothing'}"
            )
            GLib.idle_add(self._done, text, backend, elapsed)
        except inject.InjectionError as exc:
            self.log(f"injection failed: {exc}")
            GLib.idle_add(
                self._fail, "تعذّرت الكتابة", "النص في الحافظة — الصقه بـ Ctrl+V"
            )
        except Exception as exc:  # noqa: BLE001
            self.log(traceback.format_exc())
            GLib.idle_add(self._fail, "فشل التفريغ", str(exc)[:160])
        finally:
            self.take_path.unlink(missing_ok=True)

    def _done(self, text: str, backend: str, elapsed: float) -> bool:
        self.state = "idle"
        assert self.orb
        self.orb.flash("error" if not text else "done")
        return GLib.SOURCE_REMOVE

    def _fail(self, summary: str, body: str = "") -> bool:
        self.state = "idle"
        if self.orb:
            self.orb.flash("error", 1.6)
        notify.send(summary, body, urgency="critical")
        return GLib.SOURCE_REMOVE


def main() -> int:
    return Daemon().run()
