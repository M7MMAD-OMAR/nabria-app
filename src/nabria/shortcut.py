"""Telling the user how to bind the key on whatever they are running.

There is no cross-desktop way for a Wayland application to claim a global
shortcut. `org.freedesktop.portal.GlobalShortcuts` is the intended answer and
is implemented unevenly, so until that is settled the honest thing is to detect
the compositor and hand over the exact line to paste, rather than a paragraph
about where the keyboard settings live.

Detection is by environment variable only -- no processes are inspected. It is
allowed to be wrong: the fallback is a generic instruction, not an error.
"""

from __future__ import annotations

import os

TOGGLE = "toggle"
CANCEL = "cancel"
SETTINGS = "settings"


def command(action: str = TOGGLE) -> str:
    return f"nabria {action}"


def detect() -> str:
    """hyprland | sway | niri | kde | gnome | "" """
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("NIRI_SOCKET"):
        return "niri"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    for name in ("hyprland", "niri", "sway", "kde", "gnome"):
        if name in desktop:
            return name
    return ""


def instructions() -> list[str]:
    """Lines to show the user, the first of which is the one to copy."""
    where = detect()
    if where == "hyprland":
        return [
            "Add to ~/.config/hypr/hyprland.conf:",
            f"bind = CTRL ALT, Q, exec, {command(TOGGLE)}",
            f"bind = CTRL ALT SHIFT, Q, exec, {command(CANCEL)}",
            f"bind = CTRL ALT, W, exec, {command(SETTINGS)}",
        ]
    if where == "sway":
        return [
            "Add to ~/.config/sway/config:",
            f"bindsym Ctrl+Alt+q exec {command(TOGGLE)}",
            f"bindsym Ctrl+Alt+Shift+q exec {command(CANCEL)}",
        ]
    if where == "niri":
        return [
            "Add to ~/.config/niri/config.kdl, inside binds {}:",
            f'Ctrl+Alt+Q {{ spawn "nabria" "{TOGGLE}"; }}',
            f'Ctrl+Alt+Shift+Q {{ spawn "nabria" "{CANCEL}"; }}',
        ]
    if where == "kde":
        return [
            "System Settings → Keyboard → Shortcuts → Add → Command",
            command(TOGGLE),
        ]
    if where == "gnome":
        return [
            "Settings → Keyboard → View and Customise Shortcuts → Custom",
            command(TOGGLE),
        ]
    return [
        "Bind a key to this command, however your desktop does that:",
        command(TOGGLE),
    ]
