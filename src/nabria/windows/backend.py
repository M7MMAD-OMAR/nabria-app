"""The Windows desktop owns this child and communicates over inherited pipes.

Only the dictation state machine lives here. No GTK windows are created on
this path; WPF owns the main window and the non-activating recording indicator.
"""

from __future__ import annotations

import json
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

from gi.repository import GLib

from .. import audio, config, gpu, history, i18n, models
from ..app import Daemon, _file_take
from .desktop import HOTKEYS, Instance


class Indicator:
    layered = True
    state = "idle"
    level = -90.0

    def __init__(self, emit):
        self.emit = emit

    def show(self, state):
        self.state = state

    def hide(self):
        self.state = "idle"

    def set_level(self, level):
        self.level = level

    def flash(self, state, seconds=1.0):
        self.emit(event="notice", state=state)
        self.state = "idle"


class Backend(Daemon):
    def __init__(self):
        self.output_lock = threading.Lock()
        self.task_cancel = threading.Event()
        self.task_running = False
        self.closing = False
        self.device_list = []
        self.model_list = []
        self.found_models = []
        self.has_gpu = False
        super().__init__()
        if not self.settings.get("setup_done") and self.settings.get("language") == "auto":
            self.settings["language"] = i18n.current()
            if not self.settings.get("vocabulary"):
                self.settings["vocabulary"] = config.LANGUAGE_PRESETS[i18n.current()]["vocabulary"]

    def emit(self, **message):
        try:
            with self.output_lock:
                sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except (BrokenPipeError, OSError):
            if not self.closing:
                GLib.idle_add(self._quit)

    def _make_orb(self, application):
        return Indicator(self.emit)

    def _on_activate(self, application):
        if self.started:
            return
        super()._on_activate(application)
        threading.Thread(target=self._read_commands, daemon=True).start()
        threading.Thread(target=self._inventory, daemon=True).start()
        GLib.timeout_add(100, self._tick)

    def _read_commands(self):
        for line in sys.stdin:
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Expected a command object")
                GLib.idle_add(self._command, message)
            except (ValueError, TypeError) as exc:
                self.emit(event="error", detail=str(exc))
        GLib.idle_add(self._quit)

    def _tick(self):
        if self.closing:
            return GLib.SOURCE_REMOVE
        recorder = self.recording
        self.emit(event="state", state=self.state, level=self.orb.level,
                  seconds=recorder.seconds if recorder else 0,
                  jobs=self.jobs, shortcuts=dict(HOTKEYS))
        return GLib.SOURCE_CONTINUE

    def _inventory(self):
        try:
            self.has_gpu = gpu.plan("auto").use_gpu
        except Exception as exc:
            self.log(f"desktop GPU inventory: {exc}")
        try:
            self.device_list = audio.sources()
        except audio.AudioError as exc:
            self.device_list = []
            self.log(f"desktop microphone inventory: {exc}")
        try:
            self.found_models = [
                {"path": str(item.path), "name": item.path.name}
                for item in models.search(model_dir=config.MODEL_DIR)
            ]
        except Exception as exc:
            self.log(f"desktop inventory: {exc}")
        GLib.idle_add(self._ready)

    def _ready(self):
        catalogue = [
            {"key": item.key, "name": item.key, "size": item.megabytes,
             "description": i18n.t(item.summary), "needsGpu": item.needs_gpu,
             "installed": models.installed(config.MODEL_DIR, item)}
            for item in models.CATALOG.values()
        ]
        self.emit(event="ready", settings=self.settings, language=i18n.current(),
                  needsSetup=config.needs_setup(self.settings), devices=self.device_list,
                  models=catalogue, found=self.found_models, hasGpu=self.has_gpu,
                  recommended=models.recommended(self.has_gpu).key,
                  history=history.recent(100), shortcuts=dict(HOTKEYS))
        return GLib.SOURCE_REMOVE

    def _show_settings(self):
        self.emit(event="navigate", page="settings")
        return GLib.SOURCE_REMOVE

    def _show_wizard(self):
        self.emit(event="navigate", page="setup")
        return GLib.SOURCE_REMOVE

    def _command(self, message):
        identifier = message.get("id")
        try:
            command = message.get("command")
            if command in {"toggle", "start", "stop", "cancel"}:
                if self.task_running:
                    raise ValueError(i18n.t("desktop.wait_task"))
                if command in {"toggle", "start"} and config.needs_setup(self.settings):
                    raise ValueError(i18n.t("desktop.setup_required"))
                self._handle(command)
            elif command == "refresh":
                threading.Thread(target=self._inventory, daemon=True).start()
            elif command == "set":
                key, value = message["key"], message.get("value")
                allowed = {"language", "ui_language", "vocabulary", "keep_audio", "input_device"}
                if key not in allowed:
                    raise ValueError("Unsupported setting")
                if key == "keep_audio":
                    if not isinstance(value, bool):
                        raise ValueError("Expected a boolean")
                elif not isinstance(value, str):
                    raise ValueError("Expected text")
                if key in {"language", "ui_language"} and value not in {"ar", "en", "auto"}:
                    raise ValueError("Unsupported language")
                self._apply_setting(key, value)
                self._ready()
            elif command in {"download", "adopt", "select_model", "mic_test"}:
                if self.task_running or self.state != "idle":
                    raise ValueError(i18n.t("desktop.wait_task"))
                self.task_running = True
                self.task_cancel.clear()
                threading.Thread(target=self._task, args=(command, message), daemon=True).start()
            elif command == "cancel_task":
                self.task_cancel.set()
            elif command == "finish_setup":
                if not Path(self.settings["model"]).is_file():
                    raise ValueError(i18n.t("desktop.setup_required"))
                self._apply_setting("setup_done", True)
                self._ready()
            elif command == "clear_history":
                if self.state != "idle":
                    raise ValueError(i18n.t("desktop.wait_task"))
                history.clear()
                self._ready()
            elif command == "quit":
                self._quit()
            else:
                raise ValueError("Unknown desktop command")
            self.emit(event="reply", id=identifier, ok=True)
        except Exception as exc:
            self.emit(event="reply", id=identifier, ok=False, detail=str(exc))
        return GLib.SOURCE_REMOVE

    def _progress(self, done, total):
        if self.task_cancel.is_set():
            raise models.DownloadError("cancelled")
        self.emit(event="progress", done=done, total=total)

    def _task(self, command, message):
        try:
            if command == "mic_test":
                from .audio import Recorder
                with tempfile.TemporaryDirectory(prefix="nabria-mic-") as folder:
                    recorder = Recorder(Path(folder) / "test.wav")
                    try:
                        recorder.start()
                        end = time.monotonic() + 4
                        while time.monotonic() < end and not self.task_cancel.wait(0.1):
                            self.emit(event="mic_level", level=recorder.peak_dbfs)
                    finally:
                        recorder.stop()
                    self.emit(event="mic_result", heard=recorder.measured and
                              recorder.rms_dbfs > config.silence_threshold(self.settings),
                              cancelled=self.task_cancel.is_set())
            else:
                if command == "adopt":
                    source = Path(message["path"])
                    item = models.identify(source)
                    if item is None:
                        raise ValueError(i18n.t("desktop.unknown_model"))
                    if item.needs_gpu and not self.has_gpu:
                        raise ValueError(i18n.t("desktop.gpu_required"))
                    path = models.adopt(source, config.MODEL_DIR, item, self._progress)
                else:
                    item = models.CATALOG[message["model"]]
                    if item.needs_gpu and not self.has_gpu:
                        raise ValueError(i18n.t("desktop.gpu_required"))
                    if command == "download":
                        path = models.download(item, config.MODEL_DIR, self._progress,
                                               self.task_cancel.is_set)
                    else:
                        path = config.MODEL_DIR / item.filename
                        if not models.verify(path, item, self._progress):
                            raise ValueError(i18n.t("desktop.unknown_model"))
                GLib.idle_add(self._model_ready, str(path))
        except Exception as exc:
            if not self.task_cancel.is_set():
                self.emit(event="error", detail=str(exc))
        finally:
            self.task_running = False
            self.emit(event="task_done")

    def _model_ready(self, path):
        self._apply_setting("model", path)
        self._ready()
        return GLib.SOURCE_REMOVE

    def _done(self, text):
        super()._done(text)
        self.emit(event="transcript", text=text, history=history.recent(100))
        return GLib.SOURCE_REMOVE

    def _fail(self, summary, body=""):
        self.emit(event="error", title=summary, detail=body)
        return super()._fail(summary, body)

    def _quit(self):
        if self.closing:
            return GLib.SOURCE_REMOVE
        self.closing = True
        self.task_cancel.set()
        # Pending captures have not reached the worker yet. Keep them alongside
        # failed takes when the desktop exits instead of leaving orphan files.
        while True:
            try:
                recorder = self.pending.get_nowait()
            except queue.Empty:
                break
            if recorder.destination.exists():
                _file_take(recorder.destination, config.FAILED_DIR)
        return super()._quit()


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stdin.reconfigure(encoding="utf-8")
    instance = Instance()
    if not instance.primary:
        return 1
    try:
        return Backend().run()
    finally:
        instance.close()
