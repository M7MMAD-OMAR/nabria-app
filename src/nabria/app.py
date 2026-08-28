"""The dictation daemon: control socket, state machine, GTK main loop.

One long-lived process owns the orb, the recorder and the whisper server. The
hotkey does not launch anything -- it sends one line to a Unix socket, which is
why pressing it feels instant and why a keypress can never race a second copy
of the app into existence.

Recording and transcription are independent: a new take can be started while
the previous one is still being transcribed. Finished takes go through a single
worker thread, so they are always typed in the order they were spoken, and a
keypress is never ignored just because the daemon happens to be busy.
"""

from __future__ import annotations

import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import config, history, inject, notify
from .orb import Orb
from .recorder import MissingRecorder, Recorder

LEVEL_POLL_MS = 50
FAILED_DIR = config.DATA_DIR / "failed"
TAKES_DIR = config.DATA_DIR / "takes"
# How long an engine reload waits for in-flight takes before going ahead anyway.
RELOAD_DRAIN_SECONDS = 30.0


def _default_source_name() -> str:
    """Description of the source pw-record captures from, for error messages."""
    if not shutil.which("wpctl"):
        return "the default input"
    try:
        result = subprocess.run(
            ["wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "the default input"
    for line in result.stdout.splitlines():
        key, _, value = line.strip().lstrip("* ").partition(" = ")
        if key == "node.description":
            return value.strip('"') or "the default input"
    return "the default input"


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
        self.settings_window = None
        self.shortcuts = None

        from .whisper import WhisperServer

        self.whisper = WhisperServer(self.settings, self.log)
        self.recording: Recorder | None = None
        self.pending: queue.Queue[Recorder] = queue.Queue()
        self.jobs = 0
        self.jobs_lock = threading.Lock()
        self.takes = 0
        self.silent_run = 0
        self.level_source = 0
        self.deadline_source = 0

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_file.write(f"{stamp} {message}\n")

    @property
    def state(self) -> str:
        if self.recording is not None:
            return "recording"
        with self.jobs_lock:
            return "working" if self.jobs else "idle"

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
        if not self.orb.layered:
            # Worth a line in the log, because every symptom of it -- the
            # indicator in the wrong place, hidden behind a fullscreen window --
            # otherwise looks like a bug in the indicator itself.
            self.log(
                "layer shell unavailable: the indicator is an ordinary window. "
                "Install gtk4-layer-shell, or expect it to be covered."
            )
        self._serve()
        self._bind_portal_shortcuts()
        threading.Thread(
            target=self._worker, daemon=True, name="nabria-transcribe"
        ).start()
        if self.settings.get("prewarm"):
            threading.Thread(target=self._prewarm, daemon=True).start()
        self.log("daemon ready")
        if not config.models():
            # No model means nothing can be transcribed, so there is no sense
            # in waiting for a keypress to say so. Keyed off what is on disk
            # rather than a "first run" flag, which would leave the wizard
            # marked done while the app stayed unusable -- and this way it is
            # also the repair path when a model is deleted.
            GLib.idle_add(self._show_wizard)

    def _bind_portal_shortcuts(self) -> None:
        """Ask the desktop to own our hotkeys, if it is willing.

        Purely additive. The socket and any hand-bound key work exactly as
        before whether this succeeds, fails, or is never attempted -- so every
        failure here is a log line and nothing more. A daemon that refused to
        start because a portal was unhappy would be a far worse tool than one
        whose shortcut has to be bound by hand.
        """
        from . import portal

        if not portal.enabled():
            return
        try:
            self.shortcuts = portal.GlobalShortcuts(self._portal_activated, self.log)
            self.shortcuts.start()
        except Exception as exc:  # noqa: BLE001
            self.log(f"could not set up portal shortcuts: {exc}")

    def _portal_activated(self, shortcut_id: str) -> None:
        # Same entry point as the socket, so a portal key and a hand-bound key
        # are indistinguishable from here down.
        self.dispatch(shortcut_id)

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
                    connection.sendall(self.dispatch(command).encode("utf-8"))

        threading.Thread(target=accept_loop, daemon=True, name="nabria-control").start()

    def dispatch(self, command: str) -> str:
        if command == "status":
            return self.state
        if command == "last":
            return history.last()
        if command in {"toggle", "start", "stop", "cancel"}:
            GLib.idle_add(self._handle, command)
            return "ok"
        if command == "settings":
            GLib.idle_add(self._show_settings)
            return "ok"
        if command == "quit":
            GLib.idle_add(self._quit)
            return "ok"
        return f"unknown command: {command}"

    # -- settings window ---------------------------------------------------

    def _show_settings(self) -> bool:
        window = getattr(self, "settings_window", None)
        if window is not None and window.get_visible():
            window.present()
            return GLib.SOURCE_REMOVE
        # Rebuilt each time rather than kept around: the model list, the input
        # devices and the history are all read at construction, and every one
        # of them can change while the window is closed.
        from .settings_window import SettingsWindow

        window = SettingsWindow(self.application, self.settings, self._apply_setting)
        window.connect("close-request", self._on_settings_closed)
        self.settings_window = window
        window.present()
        return GLib.SOURCE_REMOVE

    def _show_wizard(self) -> bool:
        from .wizard import Wizard

        def finished() -> None:
            # The settings the wizard wrote are already in self.settings, but
            # the engine may have been started against no model at all.
            self.settings = config.load()
            self.whisper.settings = self.settings
            self.whisper.stop()
            self.log(f"setup finished, model {self.settings.get('model')}")

        window = Wizard(self.application, self.settings, finished)
        window.present()
        self.wizard = window
        return GLib.SOURCE_REMOVE

    def _on_settings_closed(self, _window) -> bool:
        self.settings_window = None
        return False  # let the window close

    def _apply_setting(self, key: str, value: object) -> None:
        """Live-edit one setting: memory first, disk second, server last."""
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        try:
            config.save(self.settings)
        except OSError as exc:
            self.log(f"could not save config: {exc}")
        self.log(f"setting {key} = {value!r}")
        # model, language and vocabulary are all baked into the whisper server
        # command line at startup, so the loaded server is now stale. Stopping
        # it is enough -- the next take starts a fresh one, which is the same
        # path the idle unload already uses.
        if key in {"model", "language", "vocabulary"}:
            threading.Thread(
                target=self._reload_engine, daemon=True, name="nabria-reload"
            ).start()

    def _reload_engine(self) -> None:
        """Drop the loaded server, but not out from under a take in flight.

        Killing it mid-request would fail whatever is being transcribed right
        then -- and changing the model is exactly when someone is most likely
        to have just spoken.

        Bounded, because `pending.join()` also waits for takes queued *after*
        the change: someone who switches model and keeps dictating would never
        drain the queue, and the old model would go on serving every take with
        nothing to say it had not switched. Past the deadline the reload wins
        and at worst one take fails, which is recoverable -- silently ignoring
        the setting is not.
        """
        deadline = time.monotonic() + RELOAD_DRAIN_SECONDS
        while time.monotonic() < deadline:
            with self.jobs_lock:
                if self.jobs == 0:
                    break
            time.sleep(0.1)
        else:
            self.log("engine reload no longer waiting for the queue")
        self.whisper.stop()

    def _quit(self) -> bool:
        if self.shortcuts is not None:
            # Closing the portal session drops its bindings, so a restarted
            # daemon does not accumulate duplicates of them.
            self.shortcuts.stop()
        self.whisper.stop()
        self.application.quit()
        return GLib.SOURCE_REMOVE

    def _handle(self, command: str) -> bool:
        try:
            if command == "cancel":
                self._cancel()
            elif command == "stop":
                self._stop()
            elif command == "start":
                self._start()
            elif command == "toggle":
                # A take in flight never blocks a new one, so toggle only ever
                # asks one question: am I recording right now?
                self._stop() if self.recording else self._start()
        except MissingRecorder as exc:
            # Not a bug and not recoverable by retrying: the machine has no
            # PipeWire. Say what to install instead of filing a stack trace.
            self.log(str(exc))
            self._fail("لا يمكن التسجيل", str(exc))
        except Exception:  # noqa: BLE001 - never let a bad take kill the daemon
            self.log(traceback.format_exc())
            self._fail("خطأ في الإملاء", str(config.LOG_PATH))
        return GLib.SOURCE_REMOVE

    # -- dictation ---------------------------------------------------------

    def _start(self) -> None:
        if self.recording is not None:
            return
        self.takes += 1
        recorder = Recorder(config.STATE_DIR / f"take-{self.takes}.wav")
        recorder.start()
        self.recording = recorder
        assert self.orb
        self.orb.show("recording")
        self.level_source = GLib.timeout_add(LEVEL_POLL_MS, self._poll_level)
        limit = int(self.settings.get("max_seconds", 0))
        if limit > 0:
            self.deadline_source = GLib.timeout_add_seconds(limit, self._deadline)
        # Loading the model takes seconds on a cold start. Starting it now, in
        # parallel with the recording, hides that behind the time spent talking
        # instead of adding it to the wait afterwards.
        threading.Thread(target=self._prewarm, daemon=True).start()
        self.log(f"take {self.takes}: recording started")

    def _poll_level(self) -> bool:
        recorder = self.recording
        if recorder is None or not self.orb:
            return GLib.SOURCE_REMOVE
        with recorder.lock:
            level = recorder.peak_dbfs
        self.orb.set_level(level)
        return GLib.SOURCE_CONTINUE

    def _deadline(self) -> bool:
        self.deadline_source = 0
        if self.recording is not None:
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
        recorder, self.recording = self.recording, None
        if recorder is None:
            return
        self._clear_timers()
        threading.Thread(
            target=lambda: (recorder.stop(), recorder.destination.unlink(missing_ok=True)),
            daemon=True,
        ).start()
        assert self.orb
        self.orb.flash("error", 0.9)
        self.log("cancelled")

    def _stop(self) -> None:
        recorder, self.recording = self.recording, None
        if recorder is None:
            return
        self._clear_timers()
        with self.jobs_lock:
            self.jobs += 1
        self.pending.put(recorder)
        assert self.orb
        self.orb.show("working")

    # -- transcription worker ---------------------------------------------

    def _worker(self) -> None:
        """One take at a time, in the order they were spoken."""
        while True:
            recorder = self.pending.get()
            try:
                self._transcribe(recorder)
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())
                GLib.idle_add(self._fail, "فشل التفريغ", str(config.LOG_PATH))
            finally:
                with self.jobs_lock:
                    self.jobs -= 1
                self.pending.task_done()
                GLib.idle_add(self._settle)

    def _transcribe(self, recorder: Recorder) -> None:
        started = time.monotonic()
        wav_path = recorder.destination
        keep_audio = False
        try:
            recorder.stop()
            if recorder.error:
                raise RuntimeError(recorder.error)
            if recorder.total_frames == 0:
                raise RuntimeError(f"no audio captured. {recorder.recent_stderr()}")

            if not recorder.measured:
                # Shorter than the level warm-up, so no level was ever taken.
                # It is not silence and it is not evidence about the input --
                # counting it as either would let a few stray double-presses
                # accuse a perfectly healthy microphone.
                self.log(f"take too short to measure ({recorder.seconds:.1f}s), skipped")
                GLib.idle_add(self._done, "")
                return

            threshold = float(self.settings.get("silence_threshold_dbfs", -42.0))
            if recorder.rms_dbfs <= threshold:
                self.log(f"silent take ({recorder.rms_dbfs:.1f} dBFS RMS), skipped")
                self._note_silent_take(recorder.rms_dbfs, threshold)
                GLib.idle_add(self._done, "")
                return

            # Audio came through, so whatever the input is, it is working.
            self.silent_run = 0
            text = self.whisper.transcribe(wav_path)
            elapsed = time.monotonic() - started
            # The recording is filed before the transcript is, so a history
            # entry never names a WAV that is not there yet -- but only when
            # there will be a row to name it. history.append drops empty
            # transcripts, and a retained take nothing references is a file
            # that can never be reached and that nothing prunes.
            audio = (
                self._retain(wav_path)
                if text and self.settings.get("keep_audio")
                else ""
            )
            # On disk before it is typed: from here on nothing downstream can
            # lose the words. `nabria last` reads them back.
            history.append(text, recorder.seconds, elapsed, audio)
            if text and self.settings.get("always_copy"):
                inject.to_clipboard(text)

            backend = ""
            if text:
                try:
                    backend = inject.deliver(text, str(self.settings.get("inject", "auto")))
                except inject.InjectionError as exc:
                    self.log(f"injection failed: {exc}")
                    GLib.idle_add(
                        self._fail, "تعذّرت الكتابة", "النص في الحافظة — الصقه بـ Ctrl+V"
                    )
                    return
            self.log(
                f"{recorder.seconds:.1f}s audio -> {len(text)} chars in {elapsed:.1f}s "
                f"via {backend or 'nothing'}"
            )
            GLib.idle_add(self._done, text)
        except Exception:
            # The audio is the one thing that cannot be produced again, so a
            # failed take keeps its WAV instead of deleting it.
            keep_audio = True
            raise
        finally:
            if keep_audio and wav_path.exists():
                FAILED_DIR.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                kept = FAILED_DIR / f"{stamp}.wav"
                shutil.move(str(wav_path), kept)
                self.log(f"kept failed take at {kept}")
            else:
                wav_path.unlink(missing_ok=True)

    def _note_silent_take(self, level: float, threshold: float) -> None:
        """Speak up about silence only once it stops looking like a choice.

        One silent take is the ordinary case: the key gets pressed and then the
        thought does not arrive. The indicator's flat line already says nothing
        was heard, and a notification on top of that is noise the user cannot
        turn off by behaving differently.

        A microphone that is muted, unplugged, or pinned to an input with
        nothing wired to it is silent *every* time. Counting consecutive silent
        takes separates the two without guessing at levels: any successful take
        clears the count.
        """
        # Guarded: this runs inside the take's try block, so a hand-edited
        # config holding "three" would raise, file the take into failed/ and
        # report a transcription failure -- turning a typo into what looks
        # like a broken transcriber.
        try:
            after = int(self.settings.get("silent_notice_after", 3) or 0)
        except (TypeError, ValueError):
            self.log("silent_notice_after is not a number, using 3")
            after = 3
        self.silent_run += 1
        if not after or self.silent_run != after:
            return
        self.log(f"{self.silent_run} silent takes in a row, notifying")
        notify.send(
            "Dictation is not hearing the microphone",
            f"{self.silent_run} takes in a row with nothing above "
            f"{threshold:.0f} dBFS — the last was {level:.0f} dBFS from "
            f"{_default_source_name()}. Check the input device.",
        )

    def _retain(self, wav_path) -> str:
        """Move a transcribed take into takes/ and return its path.

        Copying rather than moving would leave the original to be deleted in
        `finally` and double the write; moving it means the later unlink simply
        finds nothing, which it already tolerates.
        """
        try:
            TAKES_DIR.mkdir(parents=True, exist_ok=True)
            # Second resolution alone collides: a short take is transcribed in
            # a few tenths, so two queued back to back land on one name and
            # shutil.move overwrites the first without a word -- leaving the
            # earlier history row pointing at the later take's audio.
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            kept = TAKES_DIR / f"{stamp}.wav"
            suffix = 2
            while kept.exists():
                kept = TAKES_DIR / f"{stamp}-{suffix}.wav"
                suffix += 1
            shutil.move(str(wav_path), kept)
            return str(kept)
        except OSError as exc:
            # Losing the audio must never cost the transcript.
            self.log(f"could not keep audio: {exc}")
            return ""

    # -- orb state ---------------------------------------------------------

    def _done(self, text: str) -> bool:
        assert self.orb
        self.orb.flash("error" if not text else "done")
        return GLib.SOURCE_REMOVE

    def _fail(self, summary: str, body: str = "") -> bool:
        if self.orb:
            self.orb.flash("error", 1.6)
        notify.send(summary, body, urgency="critical")
        return GLib.SOURCE_REMOVE

    def _settle(self) -> bool:
        """Once the last queued take is done, stop showing the spinner."""
        if self.orb and self.state == "idle" and self.orb.state in {"working", "loading"}:
            self.orb.hide()
        return GLib.SOURCE_REMOVE


def main() -> int:
    return Daemon().run()
