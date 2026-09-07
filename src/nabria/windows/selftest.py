"""Run inside the installed runtime, including GTK and real Win32 APIs."""

import ctypes as C
import faulthandler
import importlib
import json
import os
import subprocess
import tempfile
import time
import traceback
from pathlib import Path


def main() -> int:
    report = os.environ.get("NABRIA_TEST_REPORT")
    class Results(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            if report:
                Path(report).write_text(json.dumps(self, ensure_ascii=False, indent=2), encoding="utf-8")

    results = Results(status="running")
    trace = open(report + ".trace", "w") if report else None
    if trace:
        faulthandler.enable(file=trace)
        faulthandler.dump_traceback_later(45, file=trace)
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
        run = subprocess.run([str(engine), "--prompt", "نبرة", "--help"], capture_output=True, timeout=30)
        assert run.returncode == 0 and "نبرة".encode() in run.stderr, "Engine command line is not UTF-8"
        results["arabic_engine_arguments"] = "passed"
        first, second = Instance(), Instance()
        assert first.primary and not second.primary
        second.close()
        first.close()
        results["single_instance"] = "passed"
        saved_state = config.STATE_DIR
        with tempfile.TemporaryDirectory(prefix="nabria-check-") as temporary:
            config.STATE_DIR = Path(temporary)
            listener = control.serve(lambda text: "reply:" + text, lambda text: None)
            assert control.send("status") == "reply:status"
            results["named_pipe"] = "passed"
            # The accept thread is process-scoped; its listener is reclaimed
            # on process exit, as in the daemon.
            del listener
        config.STATE_DIR = saved_state
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
        from .notify import send as toast
        assert toast("Nabria validation", "Windows notification check").wait(timeout=20) == 0
        results["toast_runtime"] = "passed"
        import threading
        received = threading.Event()
        keys = Hotkeys(lambda action: received.set(), lambda text: None)
        keys.start()
        assert len(keys.ids) == 3
        from .desktop import api, user32
        from ctypes import wintypes as W
        post = api(user32, "PostThreadMessageW", W.BOOL, W.DWORD, W.UINT, W.WPARAM, W.LPARAM)
        assert post(keys.thread.native_id, 0x0312, 1, 0)
        assert received.wait(2), "Hotkey message was not dispatched"
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
        test_clipboard_formats()
        results["clipboard_image_and_newer_copy"] = "passed"
        pasted = test_paste()
        results["native_paste_and_restore"] = (
            "passed" if pasted else "not tested: runner denied foreground activation"
        )
        sample = os.environ.get("NABRIA_TEST_WAV")
        if sample:
            from .. import models, whisper
            with tempfile.TemporaryDirectory(prefix="nabria-فحص-model-") as directory:
                model = models.download(models.CATALOG["base"], Path(directory))
                settings = {**config.DEFAULTS, "model": str(model), "language": "en", "gpu_select": "cpu", "threads": 2}
                engine_log = []
                server = whisper.WhisperServer(settings, engine_log.append)
                try:
                    transcript = server.transcribe(Path(sample))
                    assert "country" in transcript.lower(), transcript
                finally:
                    results["engine_log"] = engine_log + [line for line in server.stderr_tail if "timings" not in line]
                    server.stop()
                results["real_transcription"] = "passed"
        results["status"] = "passed"
    except Exception:
        results["status"] = "failed"
        results["error"] = traceback.format_exc()
    text = json.dumps(results, ensure_ascii=False, indent=2)
    if trace:
        faulthandler.cancel_dump_traceback_later()
        faulthandler.disable()
        trace.close()
    if report:
        Path(report).write_text(text, encoding="utf-8")
    elif results["status"] != "passed":
        Path(tempfile.gettempdir(), "nabria-self-test.json").write_text(text, encoding="utf-8")
    return 0 if results["status"] == "passed" else 1


def test_clipboard_formats():
    """Preserve real DIB data and leave a later user copy untouched."""
    import struct
    from . import inject
    dib = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 32, 0, 4, 0, 0, 0, 0) + b"\x20\x40\x80\xff"
    with inject._clipboard():
        assert inject.EmptyClipboard()
        handle = inject.GlobalAlloc(2, len(dib))
        pointer = inject.GlobalLock(handle)
        assert pointer
        C.memmove(pointer, dib, len(dib))
        inject.GlobalUnlock(handle)
        assert inject.SetClipboardData(8, handle)
    saved, sequence = inject._set_text("clipboard loan", preserve=True)
    try:
        inject._restore(saved, sequence)
        with inject._clipboard():
            handle = inject.GetClipboardData(8)
            assert handle
            pointer = inject.GlobalLock(handle)
            assert pointer
            try:
                assert C.string_at(pointer, len(dib)) == dib
            finally:
                inject.GlobalUnlock(handle)
    finally:
        inject._free(saved)
    saved, sequence = inject._set_text("another loan", preserve=True)
    try:
        inject.to_clipboard("newer user copy")
        inject._restore(saved, sequence)
        with inject._clipboard():
            handle = inject.GetClipboardData(13)
            assert handle
            pointer = inject.GlobalLock(handle)
            assert pointer
            try:
                assert C.wstring_at(pointer) == "newer user copy"
            finally:
                inject.GlobalUnlock(handle)
    finally:
        inject._free(saved)


def test_paste():
    """Send a real Ctrl+V to a native EDIT control and verify restoration."""
    import threading
    from ctypes import wintypes as W
    from . import inject
    from .desktop import api, user32

    SetForegroundWindow = api(user32, "SetForegroundWindow", W.BOOL, W.HWND)
    SetFocus = api(user32, "SetFocus", W.HWND, W.HWND)
    GetWindowText = api(user32, "GetWindowTextW", C.c_int, W.HWND, W.LPWSTR, C.c_int)
    PeekMessage = api(user32, "PeekMessageW", W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT)
    TranslateMessage = api(user32, "TranslateMessage", W.BOOL, C.POINTER(W.MSG))
    DispatchMessage = api(user32, "DispatchMessageW", C.c_ssize_t, C.POINTER(W.MSG))
    hwnd = inject.CreateWindow(0, "EDIT", "", 0x10CF0004, 100, 100, 400, 200, None, None, None, None)
    assert hwnd
    errors = []
    text = "Nabria: hello مرحبا 123"
    try:
        foreground_result = SetForegroundWindow(hwnd)
        SetFocus(hwnd)
        from .desktop import GetForegroundWindow, kernel32
        actual_foreground = GetForegroundWindow()
        if actual_foreground != hwnd and actual_foreground:
            get_thread = api(user32, "GetWindowThreadProcessId", W.DWORD, W.HWND, C.c_void_p)
            current_thread = api(kernel32, "GetCurrentThreadId", W.DWORD)
            attach = api(user32, "AttachThreadInput", W.BOOL, W.DWORD, W.DWORD, W.BOOL)
            owner, current = get_thread(actual_foreground, None), current_thread()
            if owner != current and attach(current, owner, True):
                try:
                    foreground_result = SetForegroundWindow(hwnd)
                    SetFocus(hwnd)
                finally:
                    attach(current, owner, False)
            actual_foreground = GetForegroundWindow()
        if actual_foreground != hwnd and os.environ.get("NABRIA_ALLOW_NONINTERACTIVE") == "1":
            return False
        assert actual_foreground == hwnd, f"Could not focus EDIT: SetForegroundWindow={foreground_result}, foreground={actual_foreground}, edit={hwnd}"
        inject.to_clipboard("previous clipboard: سابق")

        def work():
            try:
                inject.deliver(text)
            except Exception as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=work)
        thread.start()
        deadline = time.monotonic() + 10
        while thread.is_alive() and time.monotonic() < deadline:
            message = W.MSG()
            while PeekMessage(C.byref(message), None, 0, 0, 1):
                TranslateMessage(C.byref(message))
                DispatchMessage(C.byref(message))
            time.sleep(0.005)
        thread.join(timeout=1)
        assert not thread.is_alive(), "Paste worker timed out"
        assert not errors, errors
        buffer = C.create_unicode_buffer(1024)
        GetWindowText(hwnd, buffer, len(buffer))
        assert buffer.value == text, repr(buffer.value)
        assert inject.OpenClipboard(None)
        try:
            getter = api(user32, "GetClipboardData", W.HANDLE, W.UINT)
            handle = getter(13)
            assert handle, "Restored clipboard has no Unicode text"
            pointer = inject.GlobalLock(handle)
            assert pointer, "Could not read restored Unicode text"
            restored = C.wstring_at(pointer)
            assert restored == "previous clipboard: سابق", repr(restored)
            inject.GlobalUnlock(handle)
        finally:
            inject.CloseClipboard()
        return True
    finally:
        inject.DestroyWindow(hwnd)
