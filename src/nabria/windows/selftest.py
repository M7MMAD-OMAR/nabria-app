"""Run inside the installed runtime, including GTK and real Win32 APIs."""

import ctypes as C
import importlib
import json
import os
import subprocess
import tempfile
import time
import traceback
from pathlib import Path


def main() -> int:
    results = {}
    report = os.environ.get("NABRIA_TEST_REPORT")
    try:
        for name in ("app", "audio", "config", "gpu", "history", "i18n", "inject",
                     "models", "notify", "orb", "settings_window", "theme", "whisper", "wizard",
                     "windows.audio", "windows.control", "windows.desktop", "windows.inject"):
            importlib.import_module("nabria." + name)
        results["imports"] = "passed"
        from gi.repository import Gio, GLib, Gtk
        from .. import config, i18n
        from ..orb import Orb
        from .desktop import Hotkeys, Instance
        from . import control
        import sounddevice
        results["audio_devices"] = len(sounddevice.query_devices())
        results["physical_microphone"] = "not tested: no physical input fixture"
        engine = Path(config.DEFAULTS["server_binary"])
        run = subprocess.run([str(engine), "--help"], capture_output=True, timeout=30)
        if run.returncode:
            raise RuntimeError(run.stderr.decode("utf-8", "replace"))
        results["engine_starts"] = "passed"
        first, second = Instance(), Instance()
        assert first.primary and not second.primary
        second.close()
        first.close()
        results["single_instance"] = "passed"
        with tempfile.TemporaryDirectory(prefix="nabria-check-") as temporary:
            config.STATE_DIR = Path(temporary)
            listener = control.serve(lambda text: "reply:" + text, lambda text: None)
            assert control.send("status") == "reply:status"
            results["named_pipe"] = "passed"
            # The accept thread is process-scoped; its listener is reclaimed
            # on process exit, as in the daemon.
            del listener
        application = Gtk.Application(application_id="com.sbarah.NabriaCheck", flags=Gio.ApplicationFlags.NON_UNIQUE)
        application.register()
        assert Gtk.init_check()
        orb = Orb(application, config.DEFAULTS)
        orb.show("recording")
        context = GLib.MainContext.default()
        end = time.monotonic() + 0.2
        while time.monotonic() < end:
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)
        orb.hide()
        orb.window.destroy()
        results["gtk_overlay"] = "passed"
        from ..settings_window import SettingsWindow
        for language in ("en", "ar"):
            i18n.apply(language)
            window = SettingsWindow(application, config.DEFAULTS.copy(), lambda *args: None)
            window.destroy()
        results["settings_both_languages"] = "passed"
        keys = Hotkeys(lambda action: None, lambda text: None)
        keys.start()
        assert len(keys.ids) == 3
        keys.stop()
        results["register_hotkeys"] = "passed"
        from .inject import Input, to_clipboard, user32, api
        from ctypes import wintypes as W
        assert C.sizeof(Input) == 40
        to_clipboard("Nabria نبرة clipboard test")
        open_clip = api(user32, "OpenClipboard", W.BOOL, W.HWND)
        get_clip = api(user32, "GetClipboardData", W.HANDLE, W.UINT)
        from .inject import GlobalLock, GlobalUnlock, CloseClipboard
        assert open_clip(None)
        try:
            handle = get_clip(13)
            pointer = GlobalLock(handle)
            assert C.wstring_at(pointer) == "Nabria نبرة clipboard test"
            GlobalUnlock(handle)
        finally:
            CloseClipboard()
        results["unicode_clipboard"] = "passed"
        results["status"] = "passed"
    except Exception:
        results["status"] = "failed"
        results["error"] = traceback.format_exc()
    text = json.dumps(results, ensure_ascii=False, indent=2)
    if report:
        Path(report).write_text(text, encoding="utf-8")
    elif results["status"] != "passed":
        Path(tempfile.gettempdir(), "nabria-self-test.json").write_text(text, encoding="utf-8")
    return 0 if results["status"] == "passed" else 1
