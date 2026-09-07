"""Unicode paste with an owned snapshot of the previous clipboard formats."""

from __future__ import annotations

import ctypes as C
from contextlib import contextmanager
import time
from ctypes import wintypes as W

from .desktop import api, kernel32, user32

ole32 = C.WinDLL("ole32")
OleDuplicateData = api(ole32, "OleDuplicateData", W.HANDLE, W.HANDLE, W.WORD, W.UINT)
gdi32 = C.WinDLL("gdi32")
CopyEnhMetaFile = api(gdi32, "CopyEnhMetaFileW", W.HANDLE, W.HANDLE, W.LPCWSTR)
GetClipboardData = api(user32, "GetClipboardData", W.HANDLE, W.UINT)
EnumClipboardFormats = api(user32, "EnumClipboardFormats", W.UINT, W.UINT)


class Medium(C.Structure):
    _fields_ = [("type", W.DWORD), ("handle", W.HANDLE), ("release", C.c_void_p)]


ReleaseStgMedium = api(ole32, "ReleaseStgMedium", None, C.POINTER(Medium))


def _free(snapshot):
    for format_id, handle in snapshot:
        kind = {2: 16, 9: 16, 3: 32, 14: 64, 0x82: 16, 0x83: 32, 0x8E: 64}.get(format_id, 1)
        ReleaseStgMedium(C.byref(Medium(kind, handle, None)))
    snapshot.clear()


def _snapshot():
    saved = []
    try:
        format_id = 0
        while True:
            C.set_last_error(0)
            format_id = EnumClipboardFormats(format_id)
            if not format_id:
                if C.get_last_error():
                    raise C.WinError(C.get_last_error())
                return saved
            handle = GetClipboardData(format_id)
            kind = {0x82: 2, 0x83: 3, 0x8E: 14}.get(format_id, format_id)
            copy = (CopyEnhMetaFile(handle, None) if kind == 14
                    else OleDuplicateData(handle, kind, 0)) if handle else None
            if not copy:
                raise RuntimeError("Could not preserve a clipboard format")
            saved.append((format_id, copy))
    except Exception:
        _free(saved)
        raise


OpenClipboard = api(user32, "OpenClipboard", W.BOOL, W.HWND)
CloseClipboard = api(user32, "CloseClipboard", W.BOOL)
EmptyClipboard = api(user32, "EmptyClipboard", W.BOOL)
SetClipboardData = api(user32, "SetClipboardData", W.HANDLE, W.UINT, W.HANDLE)
GetClipboardSequenceNumber = api(user32, "GetClipboardSequenceNumber", W.DWORD)
GlobalAlloc = api(kernel32, "GlobalAlloc", W.HGLOBAL, W.UINT, C.c_size_t)
GlobalLock = api(kernel32, "GlobalLock", C.c_void_p, W.HGLOBAL)
GlobalUnlock = api(kernel32, "GlobalUnlock", W.BOOL, W.HGLOBAL)
GlobalFree = api(kernel32, "GlobalFree", W.HGLOBAL, W.HGLOBAL)
CreateWindow = api(user32, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR,
                   W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int,
                   W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p)
DestroyWindow = api(user32, "DestroyWindow", W.BOOL, W.HWND)
GetAsyncKeyState = api(user32, "GetAsyncKeyState", C.c_short, C.c_int)


class Keyboard(C.Structure):
    _fields_ = [("vk", W.WORD), ("scan", W.WORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("extra", C.c_size_t)]


class Mouse(C.Structure):
    _fields_ = [("x", W.LONG), ("y", W.LONG), ("data", W.DWORD),
                ("flags", W.DWORD), ("time", W.DWORD), ("extra", C.c_size_t)]


class Payload(C.Union):
    _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]


class Input(C.Structure):
    _anonymous_ = ("payload",)
    _fields_ = [("type", W.DWORD), ("payload", Payload)]


SendInput = api(user32, "SendInput", W.UINT, W.UINT, C.POINTER(Input), C.c_int)


@contextmanager
def _clipboard():
    # EmptyClipboard needs a real owner HWND. NULL can make SetClipboardData
    # fail even though OpenClipboard itself succeeded.
    hwnd = CreateWindow(0, "STATIC", "Nabria clipboard", 0, 0, 0, 0, 0, W.HWND(-3), None, None, None)
    if not hwnd:
        raise C.WinError(C.get_last_error())
    try:
        deadline = time.monotonic() + 2
        while not OpenClipboard(hwnd):
            if time.monotonic() >= deadline:
                raise RuntimeError("The clipboard is busy")
            time.sleep(0.02)
        try:
            yield
        finally:
            CloseClipboard()
    finally:
        DestroyWindow(hwnd)


def _set_text(text: str, preserve=False):
    saved = []
    with _clipboard():
        data = text.encode("utf-16le") + b"\0\0"
        handle = GlobalAlloc(0x0002, len(data))
        if not handle:
            raise MemoryError("Could not allocate clipboard text")
        try:
            pointer = GlobalLock(handle)
            if not pointer:
                raise C.WinError(C.get_last_error())
            C.memmove(pointer, data, len(data))
            GlobalUnlock(handle)
            # OleGetClipboard can return a live wrapper around the current
            # clipboard. Replacing it invalidates that wrapper, so materialize
            # each format before EmptyClipboard instead of keeping a COM proxy.
            if preserve:
                saved = _snapshot()
            if not EmptyClipboard() or not SetClipboardData(13, handle):
                raise C.WinError(C.get_last_error())
            handle = None
        except Exception:
            _free(saved)
            raise
        finally:
            if handle:
                GlobalFree(handle)
    return saved, GetClipboardSequenceNumber()


def _restore(saved, sequence):
    with _clipboard():
        # Check while holding the clipboard lock: a newer copy must win even
        # if it happened while we were waiting for another owner to release it.
        if GetClipboardSequenceNumber() != sequence:
            return
        if not EmptyClipboard():
            raise C.WinError(C.get_last_error())
        while saved:
            format_id, handle = saved[0]
            if not SetClipboardData(format_id, handle):
                raise C.WinError(C.get_last_error())
            saved.pop(0)  # Windows owns it only after successful publication.


def _paste_key():
    # A fast transcription can finish while the user still holds the toggle
    # modifiers. Wait for release instead of synthesizing a different shortcut.
    deadline = time.monotonic() + 3
    while any(GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
        if time.monotonic() >= deadline:
            raise RuntimeError("Release the shortcut keys, then paste with Ctrl+V")
        time.sleep(0.01)
    keys = [(0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)]
    events = (Input * len(keys))(*[
        Input(type=1, keyboard=Keyboard(vk=key, flags=flags)) for key, flags in keys
    ])
    if SendInput(len(events), events, C.sizeof(Input)) != len(events):
        # UIPI blocks injection into elevated applications. Preserve the text
        # and report the failure, rather than claiming that it was delivered.
        raise RuntimeError("Windows blocked paste. Paste manually with Ctrl+V")


def to_clipboard(text: str) -> None:
    _set_text(text)


def deliver(text: str, preference: str = "auto", terminals=(), log=None) -> str:
    from ..inject import InjectionError
    if not text:
        return "none"
    if preference == "clipboard":
        _set_text(text)
        return "clipboard"
    previous = []
    try:
        previous, sequence = _set_text(text, preserve=True)
        _paste_key()
        time.sleep(1.5)
        try:
            _restore(previous, sequence)
        except Exception as exc:
            if log:
                log(f"Could not restore the previous clipboard: {exc}")
        return "windows-paste"
    except Exception as exc:
        try:
            _set_text(text)
        except Exception:
            pass
        raise InjectionError(str(exc)) from exc
    finally:
        _free(previous)
