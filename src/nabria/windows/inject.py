"""Unicode paste with an OLE clipboard loan that preserves all data formats."""

from __future__ import annotations

import ctypes as C
import time
from ctypes import wintypes as W

from .desktop import api, kernel32, user32

ole32 = C.WinDLL("ole32")
OleInitialize = api(ole32, "OleInitialize", C.c_long, C.c_void_p)
OleUninitialize = api(ole32, "OleUninitialize", None)
OleGetClipboard = api(ole32, "OleGetClipboard", C.c_long, C.POINTER(C.c_void_p))
OleSetClipboard = api(ole32, "OleSetClipboard", C.c_long, C.c_void_p)
OleFlushClipboard = api(ole32, "OleFlushClipboard", C.c_long)
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


def _set_text(text: str) -> None:
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
                if not EmptyClipboard() or not SetClipboardData(13, handle):
                    raise C.WinError(C.get_last_error())
                handle = None
            finally:
                if handle:
                    GlobalFree(handle)
        finally:
            CloseClipboard()
    finally:
        DestroyWindow(hwnd)


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
    previous = C.c_void_p()
    initialized = OleInitialize(None) >= 0
    try:
        if not initialized or OleGetClipboard(C.byref(previous)) < 0:
            raise RuntimeError("Could not preserve the clipboard")
        _set_text(text)
        sequence = GetClipboardSequenceNumber()
        _paste_key()
        # Keep the IDataObject in this COM apartment until the receiving app
        # has read the paste. The worker is serial, so loans cannot overlap.
        time.sleep(1.5)
        if GetClipboardSequenceNumber() == sequence:
            if OleSetClipboard(previous) < 0 or OleFlushClipboard() < 0:
                if log:
                    log("Could not restore the previous clipboard")
        return "windows-paste"
    except Exception as exc:
        try:
            _set_text(text)
        except Exception:
            pass
        raise InjectionError(str(exc)) from exc
    finally:
        if previous:
            table = C.cast(previous, C.POINTER(C.POINTER(C.c_void_p))).contents
            C.WINFUNCTYPE(W.ULONG, C.c_void_p)(table[2])(previous)
        if initialized:
            OleUninitialize()
