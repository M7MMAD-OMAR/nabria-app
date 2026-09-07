"""Win32 hotkeys and a non-activating GTK indicator."""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W

user32 = C.WinDLL("user32", use_last_error=True)
kernel32 = C.WinDLL("kernel32", use_last_error=True)


def api(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


RegisterHotKey = api(user32, "RegisterHotKey", W.BOOL, W.HWND, C.c_int, W.UINT, W.UINT)
UnregisterHotKey = api(user32, "UnregisterHotKey", W.BOOL, W.HWND, C.c_int)
PeekMessage = api(user32, "PeekMessageW", W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT)
GetWindowLong = api(user32, "GetWindowLongPtrW", C.c_ssize_t, W.HWND, C.c_int)
SetWindowLong = api(user32, "SetWindowLongPtrW", C.c_ssize_t, W.HWND, C.c_int, C.c_ssize_t)
SetWindowPos = api(user32, "SetWindowPos", W.BOOL, W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT)
GetForegroundWindow = api(user32, "GetForegroundWindow", W.HWND)
GetWindowRect = api(user32, "GetWindowRect", W.BOOL, W.HWND, C.POINTER(W.RECT))
SystemParametersInfo = api(user32, "SystemParametersInfoW", W.BOOL, W.UINT, W.UINT, C.c_void_p, W.UINT)
CreateMutex = api(kernel32, "CreateMutexW", W.HANDLE, C.c_void_p, W.BOOL, W.LPCWSTR)
CloseHandle = api(kernel32, "CloseHandle", W.BOOL, W.HANDLE)

HOTKEYS: dict[str, str] = {}


class Instance:
    def __init__(self):
        from .control import address
        self.handle = CreateMutex(None, False, "Local\\" + address().split("\\")[-1])
        if not self.handle:
            raise C.WinError(C.get_last_error())
        self.primary = C.get_last_error() != 183

    def close(self):
        CloseHandle(self.handle)


class Hotkeys:
    def __init__(self, activated, log):
        self.activated, self.log = activated, log
        self.ids = {}
        self.source = 0

    def start(self):
        from gi.repository import GLib
        from .. import i18n, notify
        for index, (action, digit, fallback) in enumerate(
            (("toggle", 0x39, 0x78), ("cancel", 0x30, 0x79), ("settings", 0x38, 0x7A)), 1
        ):
            if RegisterHotKey(None, index, 0x400C, digit):
                label = f"Win+Shift+{chr(digit)}"
            elif RegisterHotKey(None, index, 0x4003, fallback):
                label = f"Ctrl+Alt+F{fallback - 0x6F}"
            else:
                self.log(f"Could not register hotkey for {action}")
                notify.send(i18n.t("windows.shortcut_failed"), i18n.t("windows.shortcut_help"))
                continue
            HOTKEYS[action] = label
            self.ids[index] = action
            self.log(f"Hotkey {label}: {action}")
        self.source = GLib.timeout_add(25, self._poll)

    def _poll(self):
        message = W.MSG()
        while PeekMessage(C.byref(message), None, 0x0312, 0x0312, 1):
            action = self.ids.get(message.wParam)
            if action:
                self.activated(action)
        return True

    def stop(self):
        from gi.repository import GLib
        if self.source:
            GLib.source_remove(self.source)
            self.source = 0
        for index in self.ids:
            UnregisterHotKey(None, index)
        self.ids.clear()
        HOTKEYS.clear()


def configure_overlay(window, settings):
    import gi
    gi.require_version("GdkWin32", "4.0")
    from gi.repository import GdkWin32
    window.realize()
    hwnd = GdkWin32.Win32Surface.get_handle(window.get_surface())
    # Apply before mapping: changing the style after present() is too late,
    # since focus has already left the document receiving the transcript.
    style = GetWindowLong(hwnd, -20)
    SetWindowLong(hwnd, -20, style | 0x08000000 | 0x00000020 | 0x00000080)
    work = W.RECT()
    SystemParametersInfo(0x0030, 0, C.byref(work), 0)
    bounds = W.RECT()
    GetWindowRect(hwnd, C.byref(bounds))
    width, height = max(76, bounds.right - bounds.left), max(30, bounds.bottom - bounds.top)
    margin = int(settings.get("orb_margin", 28))
    position = settings.get("orb_position", "bottom-right")
    x = work.left + margin if "left" in position else work.right - width - margin
    if "center" in position:
        x = work.left + (work.right - work.left - width) // 2
    y = work.top + margin if "top" in position else work.bottom - height - margin
    if not SetWindowPos(hwnd, W.HWND(-1), x, y, width, height, 0x0010):
        raise C.WinError(C.get_last_error())
