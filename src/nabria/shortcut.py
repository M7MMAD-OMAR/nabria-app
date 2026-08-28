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
from pathlib import Path

from . import i18n

TOGGLE = "toggle"
CANCEL = "cancel"
SETTINGS = "settings"

# Where the lines go, for the compositors whose configuration is a flat file
# that can be appended to. Deliberately not niri: its binds live *inside* a
# `binds {}` block, so appending at the end produces a file that parses and
# does nothing, which is worse than printing the lines and letting someone
# paste them in the right place.
CONFIG_FILES = {
    "hyprland": Path("~/.config/hypr/hyprland.conf"),
    "sway": Path("~/.config/sway/config"),
}

# Written above the block so it can be found and removed by hand later, and so
# a second run recognises its own work rather than appending a duplicate.
MARKER = "# nabria dictation shortcuts"


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


def config_file() -> Path | None:
    """The file the shortcut lines can be appended to, if there is one.

    None for every desktop where the answer is a settings dialog rather than a
    file -- KDE and GNOME -- and for niri, where the right place is inside a
    block rather than at the end.
    """
    path = CONFIG_FILES.get(detect())
    return path.expanduser() if path else None


def snippet() -> str:
    """The block to append, marker included."""
    lines = instructions()[1:]
    return "\n".join([MARKER, *lines]) + "\n"


def already_bound(path: Path) -> bool:
    """Whether this file already binds the key, however it came to.

    Checks for the command rather than for the marker, so a line somebody
    pasted by hand -- the documented path until now -- counts. Offering to add
    a binding that is already there is how a config file ends up with the same
    shortcut twice and a compositor warning nobody reads.
    """
    try:
        return command(TOGGLE) in path.read_text(encoding="utf-8")
    except OSError:
        return False


def bind(path: Path | None = None) -> Path:
    """Append the shortcut lines. Returns the file written.

    Someone else's configuration file, so: a copy is kept beside it first, the
    block is appended rather than inserted anywhere clever, and it starts with
    a comment naming what put it there. Nothing is ever rewritten or reordered
    -- the file this touches may be hundreds of lines somebody cares about.

    Hyprland reloads a changed config by itself. Sway does not, which is why
    the interface says so rather than leaving someone to wonder why the key
    they just bound does nothing.
    """
    path = path or config_file()
    if path is None:
        raise OSError("no configuration file for this desktop")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(path.suffix + ".nabria-backup").write_bytes(path.read_bytes())
        existing = path.read_text(encoding="utf-8")
        # A file not ending in a newline would otherwise get the marker welded
        # onto the end of whatever its last line happens to be.
        separator = "" if existing.endswith("\n") or not existing else "\n"
    else:
        existing, separator = "", ""
    with path.open("a", encoding="utf-8") as sink:
        sink.write(separator + "\n" + snippet())
    return path


def instructions() -> list[str]:
    """Lines to show the user, the first of which is the sentence.

    Only that first line is translated. Everything after it is configuration
    to be pasted verbatim -- translating `bindsym` would be a bug that looks
    like a translation -- so those lines are literal in every language, and
    the wizard isolates them so a right-to-left page cannot reorder them.
    """
    where = detect()
    if where == "hyprland":
        return [
            i18n.t("shortcut.hyprland", path=i18n.ltr("~/.config/hypr/hyprland.conf")),
            f"bind = CTRL ALT, Q, exec, {command(TOGGLE)}",
            f"bind = CTRL ALT SHIFT, Q, exec, {command(CANCEL)}",
            f"bind = CTRL ALT, W, exec, {command(SETTINGS)}",
        ]
    if where == "sway":
        return [
            i18n.t("shortcut.sway", path=i18n.ltr("~/.config/sway/config")),
            f"bindsym Ctrl+Alt+q exec {command(TOGGLE)}",
            f"bindsym Ctrl+Alt+Shift+q exec {command(CANCEL)}",
        ]
    if where == "niri":
        return [
            i18n.t("shortcut.niri", path=i18n.ltr("~/.config/niri/config.kdl")),
            f'Ctrl+Alt+Q {{ spawn "nabria" "{TOGGLE}"; }}',
            f'Ctrl+Alt+Shift+Q {{ spawn "nabria" "{CANCEL}"; }}',
        ]
    if where == "kde":
        return [i18n.t("shortcut.kde"), command(TOGGLE)]
    if where == "gnome":
        return [i18n.t("shortcut.gnome"), command(TOGGLE)]
    return [i18n.t("shortcut.generic"), command(TOGGLE)]
