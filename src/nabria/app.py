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
import uuid
from datetime import datetime
from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import audio, config, history, i18n, inject, notify
from .orb import Orb
from .recorder import MissingRecorder, Recorder, SILENT_DBFS
from .runtime import RuntimeWatch

if config.WINDOWS:
    from .windows.audio import Recorder

LEVEL_POLL_MS = 50
# How often the live silence check runs once a take is under way. A second is
# far finer than the shortest sensible warning delay and costs one lock and a
# square root, so the notice lands when it is due rather than at the next
# multiple of something coarse.
SILENCE_POLL_MS = 1000
INPUT_CHECK_SECONDS = 2.0
# config.py owns every path; these two are named here only for brevity below.
FAILED_DIR = config.FAILED_DIR
TAKES_DIR = config.TAKES_DIR
# How long an engine reload waits for in-flight takes before going ahead anyway.
RELOAD_DRAIN_SECONDS = 30.0


@dataclass
class LiveResult:
    take: str
    engine: object
    key: tuple[str, str, str]
    done: threading.Event = field(default_factory=threading.Event)
    finalize: bool = False
    expected_bytes: int = 0
    sent_bytes: int = 0
    text: str | None = None
    preview: str = ""


def _file_take(wav_path, directory, prefix=""):
    """Move a take into `directory` under a timestamp, and return where it went.

    Second resolution alone collides: a short take is transcribed in a few
    tenths, so two queued back to back land on one name and shutil.move
    overwrites the first without a word -- leaving the earlier history row
    pointing at the later take's audio. Shared by the kept and the failed
    paths: the suffix loop was written for takes/ and failed/ silently
    overwrote for months because it was a second copy of the same six lines.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stamp = prefix + datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    kept = directory / f"{stamp}.wav"
    suffix = 2
    while kept.exists():
        kept = directory / f"{stamp}-{suffix}.wav"
        suffix += 1
    shutil.move(str(wav_path), kept)
    return kept


def _file_quiet_take(wav_path, directory, log, *, max_files=20, max_bytes=50 * 1024 * 1024):
    """Keep uncertain input without allowing room tone to fill the disk.

    Only this feature's quiet-prefixed files rotate. Ordinary failed takes
    remain untouched, and the newest complete recording is always retained.
    """
    kept = _file_take(wav_path, directory, prefix="quiet-")
    used = kept.stat().st_size
    count = 1
    candidates = []
    for path in directory.glob("quiet-*.wav"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue  # The user can clear history while a take finishes.
    for _, path in sorted(candidates, reverse=True):
        if path == kept:
            continue
        try:
            size = path.stat().st_size
            if count >= max_files or used + size > max_bytes:
                path.unlink()
            else:
                count += 1
                used += size
        except OSError as exc:
            log(f"could not rotate quiet recording {path}: {exc}")
    return kept


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


def _default_input() -> tuple[str, bool | None]:
    """The capture device's name, and whether it is muted -- None if unknown.

    Muted is the one cause of silence this tool can name outright rather than
    describe, and it is also the common one: the mute key on a headset, or the
    button in a meeting application, silences the source system-wide while
    every indicator here goes on saying "recording". Naming it turns "nothing
    was heard" into an instruction.

    Never guessed at. `wpctl` may be absent or PipeWire wedged -- which is
    itself a fault worth reporting -- so an unknown mute state stays unknown
    and the caller says the weaker, true thing instead.

    Shells out twice, so it belongs off the main loop.
    """
    try:
        source = audio.default_source()
    except audio.AudioError:
        source = None
    if source is None:
        return _default_source_name(), None
    return source.get("name") or _default_source_name(), source.get("muted")


class Daemon:
    def __init__(self) -> None:
        self.settings = config.load()
        # Before any window exists: every string in the wizard, the settings
        # window and the notifications is looked up at the moment the widget is
        # built, so the language has to be chosen first.
        i18n.apply(str(self.settings.get("ui_language", "auto")))
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.log_file = config.LOG_PATH.open("a", encoding="utf-8", buffering=1)
        # config.load() runs before this file is open, so anything it had to
        # correct waits here. A number it could not read is the kind of typo
        # whose every other symptom points at the wrong component.
        for warning in config.load_warnings:
            self.log(warning)

        # A unique application id makes a second copy impossible: if one is
        # already running, the new process hands its activation over and exits.
        self.application = Gtk.Application(
            application_id=config.APP_ID,
            flags=Gio.ApplicationFlags.NON_UNIQUE if config.WINDOWS else Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.application.connect("activate", self._on_activate)
        self.started = False
        self.orb: Orb | None = None
        self.settings_window = None
        self.wizard = None
        self.shortcuts = None

        from .nemotron import create_server

        self.whisper = create_server(self.settings, self.log)
        self.recording: Recorder | None = None
        self.pending: queue.Queue[Recorder] = queue.Queue()
        self.jobs = 0
        self.jobs_lock = threading.Lock()
        self.takes = 0
        self.silent_run = 0
        self.gpu_fallback_noticed = False
        self.level_source = 0
        self.deadline_source = 0
        self.silence_source = 0
        self.live_stop: threading.Event | None = None
        self.live_session = None
        self.live_result: LiveResult | None = None
        self.runtime_watch = None if config.WINDOWS else RuntimeWatch()
        self.runtime_source = 0
        self.restart_requested = False
        self.runtime_idle_since = time.monotonic()

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
        for number in (() if config.WINDOWS else (signal.SIGTERM, signal.SIGINT)):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, self._quit)

        self.orb = self._make_orb(application)
        self.orb.hide()
        if not self.orb.layered:
            # Worth a line in the log, because every symptom of it -- the
            # indicator in the wrong place, hidden behind a fullscreen window --
            # otherwise looks like a bug in the indicator itself.
            self.log(
                "layer shell unavailable: the indicator is an ordinary window. "
                "Install gtk4-layer-shell, or expect it to be covered."
            )
        # A bind failure here used to propagate into PyGObject, which prints to
        # stderr and returns: the worker thread, the portal bind and the
        # "daemon ready" line below were all skipped, systemd still reported
        # the unit as running, and nabria.log was empty. Every keypress then
        # said "daemon is not running", which is the misdiagnosis the log
        # exists to prevent.
        try:
            self._serve()
        except OSError as exc:
            self.log(f"could not open the control socket at {config.SOCKET_PATH}: {exc}")
            raise
        # Deferred rather than run inline: it is fully additive, and asking the
        # portal can D-Bus-activate xdg-desktop-portal, which at login is not
        # yet running -- that would hold up the socket, the worker thread and
        # the wizard behind a round trip none of them need.
        GLib.idle_add(self._bind_portal_shortcuts)
        threading.Thread(
            target=self._worker, daemon=True, name="nabria-transcribe"
        ).start()
        if self.settings.get("prewarm"):
            threading.Thread(target=self._prewarm, daemon=True).start()
        if self.runtime_watch is not None:
            self.runtime_source = GLib.timeout_add_seconds(5, self._refresh_runtime)
        self.log("daemon ready")
        if config.needs_setup(self.settings):
            GLib.idle_add(self._show_wizard)
        elif config.WINDOWS and len(sys.argv) == 1:
            GLib.idle_add(self._show_settings)

    def _make_orb(self, application):
        return Orb(application, self.settings)

    def _bind_portal_shortcuts(self) -> bool:
        """Ask the desktop to own our hotkeys, if it is willing.

        Purely additive. The socket and any hand-bound key work exactly as
        before whether this succeeds, fails, or is never attempted -- so every
        failure here is a log line and nothing more. A daemon that refused to
        start because a portal was unhappy would be a far worse tool than one
        whose shortcut has to be bound by hand.
        """
        if config.WINDOWS:
            from .windows.desktop import Hotkeys
            self.shortcuts = Hotkeys(self._portal_activated, self.log)
            self.shortcuts.start()
            return GLib.SOURCE_REMOVE
        from . import portal

        if portal.enabled():
            try:
                self.shortcuts = portal.GlobalShortcuts(self._portal_activated, self.log)
                self.shortcuts.start()
            except Exception as exc:  # noqa: BLE001
                self.log(f"could not set up portal shortcuts: {exc}")
        return GLib.SOURCE_REMOVE

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
        result = self.application.run(sys.argv[:1])
        if self.restart_requested:
            # exec preserves the systemd unit identity required by the portal,
            # and the launcher's LD_PRELOAD and PYTHONPATH. The old process's
            # non-inherited sockets and GTK bus connection close on exec.
            os.execv(sys.executable, [sys.executable, "-m", "nabria", *sys.argv[1:]])
        return result

    def _refresh_runtime(self) -> bool:
        if (
            self.state != "idle" or self.settings_window is not None
            or self.wizard is not None or inject.clipboard_busy()
        ):
            self.runtime_idle_since = time.monotonic()
            return GLib.SOURCE_CONTINUE
        # Leave a brief quiet interval after work completes. Actual clipboard
        # restores are tracked above, including ones blocked on subprocesses.
        if time.monotonic() - self.runtime_idle_since < 2:
            return GLib.SOURCE_CONTINUE
        if self.runtime_watch is None or not self.runtime_watch.ready():
            return GLib.SOURCE_CONTINUE
        self.log("Python runtime updated; restarting while idle")
        self.restart_requested = True
        self.runtime_source = 0
        self._quit()
        return GLib.SOURCE_REMOVE

    # -- control socket ----------------------------------------------------

    def _serve(self) -> None:
        if config.WINDOWS:
            from .windows.control import serve
            self.control = serve(self.dispatch, self.log)
            return
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
            # One bad client must not end the loop. accept() can raise EMFILE,
            # and recv/sendall raise ConnectionReset/BrokenPipe whenever a
            # client gives up first -- which is exactly what client.py's 5s
            # timeout does. Letting any of those unwind this thread leaves the
            # socket file on disk and still bound, so every later keypress
            # connects, blocks and times out: the hotkey stops working for the
            # life of the daemon, with nothing in the log to say why.
            while True:
                try:
                    connection, _ = server.accept()
                    with connection:
                        command = connection.recv(64).decode("utf-8", "replace").strip()
                        connection.sendall(self.dispatch(command).encode("utf-8"))
                except OSError as exc:
                    self.log(f"control connection failed: {exc}")

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
        window = self.settings_window
        if window is not None and window.get_visible():
            window.present()
            return GLib.SOURCE_REMOVE
        # Rebuilt each time rather than kept around: the model list, the input
        # devices and the history are all read at construction, and every one
        # of them can change while the window is closed.
        from .settings_window import SettingsWindow

        window = SettingsWindow(
            self.application, self.settings, self._apply_setting,
            # The way in for anyone who has not bound a key -- and on the
            # desktops where binding one means finding a settings dialog, that
            # is most people on their first day.
            on_toggle=lambda: self._handle("toggle"),
            state=lambda: self.state,
        )
        window.connect("close-request", self._on_settings_closed)
        self.settings_window = window
        window.present()
        return GLib.SOURCE_REMOVE

    def _show_wizard(self) -> bool:
        from .wizard import Wizard

        def finished() -> None:
            # Released here, or the daemon holds the whole window -- six pages
            # of widgets -- for the rest of its life, in a reference cycle this
            # closure is itself part of.
            self.wizard = None
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
        # Strings are looked up as each widget is built, so re-selecting here is
        # what makes the next window come up in the new language. The one that
        # is open keeps the language it was built in -- rebuilding a window
        # underneath the combo box that is still handling its own signal is a
        # crash waiting to happen, and the hint next to the picker says so.
        if key == "ui_language":
            i18n.apply(str(value))
        # model, language and vocabulary are all baked into the whisper server
        # command line at startup, so the loaded server is now stale. Stopping
        # it is enough -- the next take starts a fresh one, which is the same
        # path the idle unload already uses.
        if key in {"model", "language", "vocabulary", "nemo_vocabulary"}:
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

        from .nemotron import create_server, is_nemotron, NemotronServer

        if isinstance(self.whisper, NemotronServer) != is_nemotron(self.settings.get("model", "")):
            self.whisper = create_server(self.settings, self.log)

    def _quit(self) -> bool:
        self._stop_live()
        if self.runtime_source:
            GLib.source_remove(self.runtime_source)
            self.runtime_source = 0
        if self.shortcuts is not None:
            # Closing the portal session drops its bindings, so a restarted
            # daemon does not accumulate duplicates of them.
            self.shortcuts.stop()
        if self.recording is not None:
            self.recording.stop()
            _file_take(self.recording.destination, FAILED_DIR)
            self.recording = None
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
            self._fail(i18n.t("app.cannot_record"), i18n.ltr(exc))
        except Exception:  # noqa: BLE001 - never let a bad take kill the daemon
            self.log(traceback.format_exc())
            self._fail(i18n.t("app.dictation_error"), i18n.ltr(config.LOG_PATH))
        return GLib.SOURCE_REMOVE

    # -- dictation ---------------------------------------------------------

    def _start(self) -> None:
        if self.recording is not None:
            return
        self.runtime_idle_since = time.monotonic()
        self.takes += 1
        recorder = Recorder(config.STATE_DIR / f"take-{uuid.uuid4().hex}.wav")
        recorder.take_number = self.takes
        recorder.start()
        self.recording = recorder
        assert self.orb
        clear = getattr(self.orb, "clear_transcript", None)
        if callable(clear):
            clear()
        self.orb.show("recording")
        self._start_live(recorder)
        self._check_input(recorder)
        self.level_source = GLib.timeout_add(LEVEL_POLL_MS, self._poll_level)
        warn_after = self._silence_warning_seconds()
        if warn_after > 0:
            self.silence_source = GLib.timeout_add(SILENCE_POLL_MS, self._poll_silence)
        limit = int(self.settings.get("max_seconds", 0))
        if limit > 0:
            self.deadline_source = GLib.timeout_add_seconds(limit, self._deadline)
        # Loading the model takes seconds on a cold start. Starting it now, in
        # parallel with the recording, hides that behind the time spent talking
        # instead of adding it to the wait afterwards.
        threading.Thread(target=self._prewarm, daemon=True).start()
        self.log(f"take {self.takes}: recording started")

    def _start_live(self, recorder: Recorder) -> None:
        self._stop_live()
        start_live = getattr(self.whisper, "start_live", None)
        if not callable(start_live) or not callable(getattr(recorder, "read_live", None)):
            return
        stop = threading.Event()
        self.live_stop = stop
        result = LiveResult(str(recorder.destination), self.whisper, self._live_key())
        recorder.live_result = result
        self.live_result = result
        threading.Thread(
            target=self._preview, args=(recorder, stop, start_live, result),
            daemon=True, name="nabria-live-preview",
        ).start()

    def _live_key(self) -> tuple[str, str, str]:
        return tuple(str(self.settings.get(key, "")) for key in ("model", "language", "nemo_vocabulary"))

    def _stop_live(self, *, finalize: bool = False, expected_bytes: int = 0) -> None:
        result = self.live_result
        self.live_result = None
        if result is not None and finalize:
            result.finalize = True
            result.expected_bytes = expected_bytes
        if self.live_stop is not None:
            self.live_stop.set()
            self.live_stop = None
        live = self.live_session
        self.live_session = None
        if live is not None and not finalize:
            cancel = getattr(live[1], "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception as exc:
                    self.log(f"could not interrupt live preview: {exc}")

    def _preview(self, recorder: Recorder, stop: threading.Event, start_live, result: LiveResult | None = None) -> None:
        session = None
        try:
            if stop.is_set():
                return
            def preview(text):
                if result is not None:
                    result.preview = text
                GLib.idle_add(self._preview_text, recorder, stop, text)

            session = start_live(preview)
            if self.live_stop is stop and not stop.is_set():
                self.live_session = (stop, session)
            offset = 0
            while not stop.is_set():
                data, offset = recorder.read_live(offset)
                if data:
                    session.feed(data)
                    if result is not None:
                        result.sent_bytes = offset
                else:
                    stop.wait(0.1)
            if result is not None and result.finalize:
                # recorder.stop() has drained the capture pipe before this
                # request. Include its last chunk before asking for a final.
                data, offset = recorder.read_live(offset)
                if data:
                    session.feed(data)
                    result.sent_bytes = offset
                finish = getattr(session, "finish", None)
                if result.sent_bytes == result.expected_bytes and callable(finish):
                    result.text = finish(timeout=1.0)
        except Exception as exc:  # noqa: BLE001 - preview must not lose the authoritative WAV
            if not stop.is_set() or (result is not None and result.finalize):
                self.log(f"live preview unavailable: {exc}; keeping full-take transcription")
                GLib.idle_add(self._preview_unavailable, recorder, stop)
        finally:
            if result is not None:
                result.done.set()
            live = self.live_session
            if live is not None and live[0] is stop:
                self.live_session = None
            if session is not None:
                try:
                    session.close()
                except Exception as exc:  # noqa: BLE001 - capture and final delivery continue
                    self.log(f"could not close live preview: {exc}")

    def _preview_text(self, recorder: Recorder, stop: threading.Event, text: str) -> bool:
        # A network response may already be queued on GTK when stop/cancel or
        # a new take arrives. It must never replace that newer take's text.
        if not stop.is_set() and self.recording is recorder and self.orb is not None:
            update = getattr(self.orb, "set_transcript", None)
            if callable(update):
                update(text)
        return GLib.SOURCE_REMOVE

    def _preview_unavailable(self, recorder, stop) -> bool:
        if stop.is_set() or self.recording is not recorder:
            return GLib.SOURCE_REMOVE
        result = getattr(recorder, "live_result", None)
        if isinstance(result, LiveResult):
            result.preview = ""
        self._preview_text(recorder, stop, "")
        # Replies already queued by the failed socket must not restore stale
        # words after the warning clears them. The full WAV stays authoritative.
        stop.set()

        def report():
            # A desktop notification can block; capture and GTK must continue.
            if self.recording is not recorder:
                return
            try:
                notify.send(i18n.t("app.live_unavailable"), i18n.t("app.live_unavailable_body"))
            except Exception as exc:
                self.log(f"could not report unavailable live preview: {exc}")
        threading.Thread(target=report, daemon=True, name="nabria-live-notice").start()
        return GLib.SOURCE_REMOVE

    def _poll_level(self) -> bool:
        recorder = self.recording
        if recorder is None or not self.orb:
            return GLib.SOURCE_REMOVE
        with recorder.lock:
            level = recorder.peak_dbfs
        self.orb.set_level(level)
        self._check_input(recorder)
        return GLib.SOURCE_CONTINUE

    def _silence_warning_seconds(self) -> float:
        """How long a take may run unheard before saying so.

        Read straight, the way `silent_notice_after` is read a few lines
        below: `config.load` has already made every numeric setting a number
        and logged anything it could not, so a call-site try/except here would
        be the exact guard commit eec6f9e removed from the call sites for
        being the thing that had to be remembered at each one. Verified: a
        hand-edited "soon" becomes 12.0 with a warning before this ever runs.
        """
        return float(self.settings.get(
            "silence_warning_seconds", config.DEFAULTS["silence_warning_seconds"]
        ))

    def _poll_silence(self) -> bool:
        """While recording: say once that nothing is arriving.

        The finished-take notice cannot help the case this exists for. Somebody
        who mutes their microphone, speaks for a minute and then stops has
        already lost the minute by the time anything can be said about it --
        and `silent_notice_after` waits for three such takes before it speaks
        at all, which is right for a habit forming and useless for the sentence
        currently being spoken.

        Once per take, never repeated: a notification that keeps arriving while
        somebody is talking is worse than the silence it is reporting. The
        source is left running afterwards only long enough to be removed here.
        """
        recorder = self.recording
        if recorder is None:
            self.silence_source = 0
            return GLib.SOURCE_REMOVE
        if recorder.warned_unheard:
            self.silence_source = 0
            return GLib.SOURCE_REMOVE
        if self._has_live_preview(recorder):
            return GLib.SOURCE_CONTINUE
        threshold = config.silence_threshold(self.settings)
        # Length and level come back with the verdict, from the one lock
        # acquisition that produced it. Re-reading them here would take the
        # lock twice more and describe a different instant of the recording.
        unheard, seconds, level = recorder.unheard(
            threshold, self._silence_warning_seconds()
        )
        if not unheard:
            return GLib.SOURCE_CONTINUE

        recorder.warned_unheard = True
        self.silence_source = 0
        self.log(
            f"take {self.takes}: {seconds:.0f}s in at {level:.1f} dBFS RMS, "
            "warning while still recording"
        )
        # wpctl, with a timeout: on the main loop that is the orb frozen
        # mid-take, which is the one thing the indicator must never do while
        # it is claiming to listen.
        threading.Thread(
            target=self._warn_unheard, args=(seconds, threshold, recorder),
            daemon=True, name="nabria-silence-warning",
        ).start()
        return GLib.SOURCE_REMOVE

    def _has_live_preview(self, recorder) -> bool:
        result = getattr(recorder, "live_result", None)
        return (
            isinstance(result, LiveResult) and result.take == str(recorder.destination)
            and result.engine is self.whisper and result.key == self._live_key()
            and bool(result.preview.strip())
        )

    def _check_input(self, recorder) -> None:
        now = time.monotonic()
        if (self.recording is not recorder or getattr(recorder, "warned_unheard", False)
                or getattr(recorder, "input_check_pending", False)
                or now < getattr(recorder, "input_check_after", 0.0)):
            return
        # Past speech remains valid for final acceptance, but cannot prove that
        # the microphone is still unmuted. Bound probes independently of PCM.
        recorder.input_check_after = now + INPUT_CHECK_SECONDS
        recorder.input_check_pending = True
        threading.Thread(target=self._probe_input, args=(recorder,), daemon=True,
                         name="nabria-input-check").start()

    def _probe_input(self, recorder) -> None:
        # Device queries spawn wpctl, so they must not delay the hotkey or GTK.
        try:
            name, muted = _default_input()
            if muted is True:
                GLib.idle_add(self._input_checked, recorder, name)
        except Exception as exc:
            self.log(f"could not check recording input: {exc}")
        finally:
            recorder.input_check_pending = False

    def _input_checked(self, recorder, name) -> bool:
        if self.recording is not recorder or recorder.warned_unheard:
            return GLib.SOURCE_REMOVE
        recorder.warned_unheard = True
        threading.Thread(target=self._notify_muted_input, args=(recorder, name),
                         daemon=True, name="nabria-input-notice").start()
        return GLib.SOURCE_REMOVE

    def _notify_muted_input(self, recorder, name) -> None:
        try:
            if self.recording is not recorder:
                return
            summary, body = self._unheard_notice(name, True, 0, config.silence_threshold(self.settings))
            notify.send(summary, body, urgency="critical")
        except Exception as exc:
            self.log(f"could not send the muted input warning: {exc}")

    def _unheard_notice(self, name: str, muted: bool | None,
                        seconds: float, threshold: float) -> tuple[str, str]:
        """The words for "nothing is arriving", shared by both notices.

        Written once because both paths report the same fault and were the
        same eight lines twice: the mid-take warning and the third-silent-take
        notice. Two copies meant a change to the muted wording had to be made
        in both, and the one that got missed would be invisible until somebody
        muted their microphone.

        `muted` is only ever named when `wpctl` actually said so. An unknown
        mute state falls to the weaker sentence, because "your microphone is
        muted" sends the user to fix something that may be fine.
        """
        if muted:
            # The only cause that can be named outright, and the common one.
            # "Your microphone is muted" is an instruction; "nothing was heard"
            # is a symptom the user still has to diagnose.
            return i18n.t("app.unheard"), i18n.t(
                "app.unheard_muted_body", source=i18n.ltr(name)
            )
        return i18n.t("app.unheard"), i18n.t(
            "app.unheard_body",
            seconds=f"{seconds:.0f}",
            threshold=i18n.ltr(f"{threshold:.0f}"),
            source=i18n.ltr(name),
        )

    def _warn_unheard(self, seconds: float, threshold: float, recorder=None) -> None:
        """Send the mid-take warning. Runs on its own thread, not the loop.

        Wrapped, because nothing else catches for it. Every other notification
        path sits inside `_worker`'s except, while this one is a bare thread:
        `notify.send` shells out with a timeout, and `TimeoutExpired` is
        raised whatever `check=False` says, so a wedged notification daemon
        would kill this thread with its traceback going to stderr rather than
        to nabria.log. That is a silent failure inside the feature whose whole
        purpose is to stop a silent failure.
        """
        try:
            source = _default_input()
            # The query may finish after stop, a newer take, or the first
            # audible syllable. Never send that stale warning.
            if recorder is not None and (
                self.recording is not recorder or self._has_live_preview(recorder)
                or getattr(recorder, "activity_dbfs", SILENT_DBFS) > threshold
            ):
                return
            summary, body = self._unheard_notice(
                *source, seconds=seconds, threshold=threshold
            )
            notify.send(summary, body, urgency="critical")
        except Exception as exc:  # noqa: BLE001 - a bare thread has no other net
            self.log(f"could not send the unheard warning: {exc}")

    def _deadline(self) -> bool:
        self.deadline_source = 0
        if self.recording is not None:
            self.log("hit max_seconds, stopping")
            self._stop()
        return GLib.SOURCE_REMOVE

    def _clear_timers(self) -> None:
        for attribute in ("level_source", "deadline_source", "silence_source"):
            source = getattr(self, attribute)
            if source:
                GLib.source_remove(source)
                setattr(self, attribute, 0)

    def _cancel(self) -> None:
        self._stop_live()
        recorder, self.recording = self.recording, None
        if recorder is None:
            return
        self.runtime_idle_since = time.monotonic()
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
        # Capture must stop at the keypress, even when an earlier take is
        # still transcribing. Otherwise the queue also queues microphone stop.
        try:
            recorder.stop()
        except Exception as exc:
            recorder.error = str(exc)
        self._stop_live(
            finalize=not bool(getattr(recorder, "error", "")),
            expected_bytes=getattr(recorder, "live_bytes", getattr(recorder, "total_frames", 0) * 2) & ~1,
        )
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
                GLib.idle_add(
                    self._fail,
                    i18n.t("app.transcribe_failed"), i18n.ltr(config.LOG_PATH),
                )
            finally:
                with self.jobs_lock:
                    self.jobs -= 1
                    self.runtime_idle_since = time.monotonic()
                self.pending.task_done()
                GLib.idle_add(self._settle)

    def _confirmed_live_text(self, recorder: Recorder) -> str | None:
        result = getattr(recorder, "live_result", None)
        if isinstance(result, LiveResult) and result.done.wait(1.5):
            expected = getattr(recorder, "live_bytes", getattr(recorder, "total_frames", 0) * 2) & ~1
            if (
                result.take == str(recorder.destination) and result.engine is self.whisper
                and result.key == self._live_key() and result.text is not None
                and result.finalize
                and expected > 0 and result.sent_bytes == result.expected_bytes == expected
            ):
                return result.text
        return None

    def _transcribe_audio(self, recorder: Recorder) -> str:
        text = self._confirmed_live_text(recorder)
        if text is not None:
            self.log("using confirmed live transcript; complete WAV replay skipped")
            return text
        return self.whisper.transcribe(recorder.destination)

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

            self.log(
                f"captured {recorder.seconds:.1f}s at {recorder.rms_dbfs:.1f} dBFS RMS, "
                f"peak {recorder.max_peak_dbfs:.1f} dBFS"
            )
            capture_warnings = recorder.recent_stderr().strip()
            if capture_warnings:
                self.log(f"capture warnings: {capture_warnings}")

            if not recorder.measured:
                # Shorter than the level warm-up, so no level was ever taken.
                # It is not silence and it is not evidence about the input --
                # counting it as either would let a few stray double-presses
                # accuse a perfectly healthy microphone.
                self.log(f"take too short to measure ({recorder.seconds:.1f}s), skipped")
                GLib.idle_add(self._done, "")
                return

            threshold = config.silence_threshold(self.settings)
            activity = getattr(recorder, "activity_dbfs", recorder.rms_dbfs)
            self.log(f"sustained input at {activity:.1f} dBFS")
            confirmed = None
            if activity <= threshold:
                nonzero = recorder.max_peak_dbfs > SILENT_DBFS
                # A click has a nonzero peak but no sustained input. Even a
                # fully received model result must not turn it into dictation.
                confirmed = (
                    self._confirmed_live_text(recorder)
                    if nonzero and activity > SILENT_DBFS else None
                )
                if not confirmed:
                    if nonzero:
                        kept = _file_quiet_take(wav_path, FAILED_DIR, self.log)
                        self.log(f"kept uncertain quiet take at {kept}")
                        try:
                            notify.send(i18n.t("app.quiet_saved"),
                                        i18n.t("app.quiet_saved_body", path=i18n.ltr(kept)))
                        except Exception as exc:
                            self.log(f"could not notify about quiet recording: {exc}")
                    else:
                        self.log("no measured input signal, skipped")
                        self._note_silent_take(recorder, recorder.rms_dbfs, threshold)
                    GLib.idle_add(self._done, "")
                    return

            # Audio came through, so whatever the input is, it is working.
            self.silent_run = 0
            text = confirmed if confirmed is not None else self._transcribe_audio(recorder)
            self._note_gpu_fallback()
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
                    backend = inject.deliver(
                        text,
                        str(self.settings.get("inject", "auto")),
                        tuple(self.settings.get("terminal_classes") or ()),
                        log=self.log,
                    )
                except inject.InjectionError as exc:
                    self.log(f"injection failed: {exc}")
                    GLib.idle_add(
                        self._fail,
                        i18n.t("app.type_failed"),
                        i18n.t(
                            "app.type_failed_body",
                            key=i18n.ltr(inject.paste_key(
                                tuple(self.settings.get("terminal_classes") or ())
                            )),
                        ),
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
            if keep_audio:
                # This runs while the transcription failure is still
                # propagating, so a raise here would replace it and the log
                # would explain a file move instead of the fault that caused
                # it. The WAV is the thing that cannot be produced again, so
                # report the failure to keep it and let the original stand.
                try:
                    kept = _file_take(wav_path, FAILED_DIR)
                    self.log(f"kept failed take at {kept}")
                except OSError as exc:
                    self.log(f"could not keep failed take at {wav_path}: {exc}")
            else:
                wav_path.unlink(missing_ok=True)

    def _note_silent_take(self, recorder: Recorder, level: float, threshold: float) -> None:
        """Speak up about silence only once it stops looking like a choice.

        One silent take is the ordinary case: the key gets pressed and then the
        thought does not arrive. The indicator's flat line already says nothing
        was heard, and a notification on top of that is noise the user cannot
        turn off by behaving differently.

        A microphone that is muted, unplugged, or pinned to an input with
        nothing wired to it is silent *every* time. Counting consecutive silent
        takes separates the two without guessing at levels: any successful take
        clears the count.

        The run is not advanced by a take that already warned mid-recording.
        The trigger below is an equality, so spending the count on a take
        whose notice was suppressed walks the run past `after` and the notice
        can never fire again: measured, with after=3, a mid-take warning on
        exactly the third take silenced all eight that followed.
        """
        # config.load() has already made this a number, and said so in the log
        # if it could not: a hand-edited "three" here used to raise inside the
        # take, file the audio into failed/ and report a broken transcriber,
        # turning a typo into what looked like a broken engine.
        generation = getattr(self, "takes", 0)
        if (getattr(recorder, "take_number", generation) != generation
                or getattr(self, "recording", None) is not None):
            return
        after = int(self.settings.get("silent_notice_after", 3) or 0)
        if recorder.warned_unheard:
            # Already said, to this face, about this take. Returning before the
            # counter moves, not after: the trigger below is an equality, so a
            # warned take that spent the count would walk the run past `after`
            # and the notice could never fire again for that microphone.
            # Measured: with after=3, a mid-take warning on exactly the third
            # take silenced all eight that followed.
            self.log("silent take already reported while recording, not notifying again")
            return
        self.silent_run += 1
        if not after or self.silent_run != after:
            return
        self.log(f"{self.silent_run} silent takes in a row, notifying")
        name, muted = _default_input()
        # A queued take can finish while the owner starts speaking again.
        # A slow device query must not attach its old warning to that new take.
        if (getattr(self, "takes", 0) != generation
                or getattr(self, "recording", None) is not None):
            # The trigger is an equality. A notice that never reached the
            # owner must not spend it and silence every later short take.
            self.silent_run -= 1
            return
        if muted:
            # Nameable, so name it: this notice otherwise describes a symptom
            # and leaves the user to work out the cause that is one key away.
            summary, body = self._unheard_notice(
                name, muted, recorder.seconds, threshold
            )
            notify.send(summary, body, urgency="critical")
            return
        notify.send(
            i18n.t("app.not_hearing"),
            i18n.t(
                "app.not_hearing_body",
                takes=self.silent_run,
                threshold=i18n.ltr(f"{threshold:.0f}"),
                level=i18n.ltr(f"{level:.0f}"),
                source=i18n.ltr(name),
            ),
        )

    def _note_gpu_fallback(self) -> None:
        """Say once that the engine is on the CPU when the GPU was meant to run.

        The fallback keeps the take, which is the point, but it also makes
        every take afterwards slower than the user has any reason to expect.
        A tool that quietly halves its own speed and says nothing is the same
        misdiagnosis this project's log exists to prevent, one layer up.

        Once per daemon, not once per take: the condition holds for the rest
        of the session by design, so a notice per take would be the same
        sentence on every dictation until the daemon is restarted.
        """
        if not self.whisper.gpu_failed or self.gpu_fallback_noticed:
            return
        self.gpu_fallback_noticed = True
        notify.send(
            i18n.t("app.gpu_fallback"),
            i18n.t("app.gpu_fallback_body", log=i18n.ltr(config.LOG_PATH)),
        )

    def _retain(self, wav_path) -> str:
        """Move a transcribed take into takes/ and return its path.

        Copying rather than moving would leave the original to be deleted in
        `finally` and double the write; moving it means the later unlink simply
        finds nothing, which it already tolerates.
        """
        try:
            return str(_file_take(wav_path, TAKES_DIR))
        except OSError as exc:
            # Losing the audio must never cost the transcript.
            self.log(f"could not keep audio: {exc}")
            return ""

    # -- orb state ---------------------------------------------------------

    def _done(self, text: str) -> bool:
        if self.recording is not None:
            return GLib.SOURCE_REMOVE
        assert self.orb
        if self.state == "idle":
            self.orb.hide()
        return GLib.SOURCE_REMOVE

    def _fail(self, summary: str, body: str = "") -> bool:
        if self.orb and self.recording is None:
            self.orb.flash("error", 1.6)
        notify.send(summary, body, urgency="critical")
        return GLib.SOURCE_REMOVE

    def _settle(self) -> bool:
        """Once the last queued take is done, stop showing the spinner."""
        self.runtime_idle_since = time.monotonic()
        if self.orb and self.state == "idle" and self.orb.state in {"working", "loading"}:
            self.orb.hide()
        return GLib.SOURCE_REMOVE


def main() -> int:
    return Daemon().run()
