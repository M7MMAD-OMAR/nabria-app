"""The palette.

Self-contained. The app ships its own dark theme and needs nothing from the
desktop to look right -- no colour daemon, no generated file, no particular
shell. That is the point: it has to look the same on a bare Sway session as it
does under a fully themed desktop, and a missing file must never be the
difference between a considered design and a fallback.

Two optional overrides, both off on a normal install:

  ~/.config/<app>/palette.json   the user's own colours, always honoured
  PALETTE_PATH                   a desktop that generates a Material You
                                 palette from the wallpaper. Opt-in via
                                 `follow_desktop_palette`, because a design
                                 that quietly hands its colours to whatever
                                 shell is installed is not a design.

Either is merged over the built-in values, so a partial file changes only what
it names.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# A desktop-generated Material You palette, if one happens to exist. Optional
# by design -- this used to be the source of truth, which tied the app to one
# particular shell.
PALETTE_PATH = (
    Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    / "quickshell/user/generated/colors.json"
)

# The shipped dark theme: warm neutrals with a single coral accent. Chosen to
# sit quietly over a document at the bottom of the screen and still be legible
# against both a white page and a dark terminal.
DARK = {
    "primary": "#ff9d7d",           # the voice: bars, the done line
    "tertiary": "#f0c48a",          # thinking
    "error": "#ff6f5e",             # far enough from primary to read as wrong
    "surface_container": "#1c1613",  # the pill
    "outline_variant": "#4a3a35",   # its edge
    "on_surface": "#f4e6e0",
    "card": "#241d19",             # a raised surface: wizard choice cards
}




def to_hex(colour: tuple[float, float, float], lighten: float = 0.0) -> str:
    """Back to `#rrggbb`, since GTK CSS wants text and Cairo wants floats.

    Both directions live here so the palette has one home; the wizard used to
    convert back itself, having received values this module had just converted.
    """
    return "#" + "".join(
        f"{round(min(1.0, channel + lighten) * 255):02x}" for channel in colour
    )


# How long the whole accent question may take, connection included. It is on
# the path that draws the first window, and a desktop whose portal cannot
# answer in that long has no accent colour worth waiting for.
ACCENT_TIMEOUT = 1.5


def _ask_accent() -> str | None:
    """The D-Bus half, run on a thread it is allowed to hang on.

    `ReadOne` first, `Read` after it: the second is the older name and is
    doubly wrapped, and a desktop shipping only one of the two is not worth a
    version check.
    """
    from gi.repository import Gio, GLib

    def ask(method: str):
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return connection.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings",
            method,
            GLib.Variant("(ss)", ("org.freedesktop.appearance", "accent-color")),
            None,
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )

    reply = None
    for method in ("ReadOne", "Read"):
        try:
            reply = ask(method)
            break
        except GLib.Error:
            continue
    if reply is None:
        return None

    # (v) -> v -> (ddd), with the older call wrapping it once more.
    value = reply.unpack()
    while isinstance(value, tuple) and len(value) == 1:
        value = value[0]
    if not isinstance(value, tuple) or len(value) != 3:
        return None
    try:
        channels = [float(channel) for channel in value]
    except (TypeError, ValueError):
        return None
    # The specification says an unset accent is (-1, -1, -1), and a desktop
    # that answers with something outside the range is not answering.
    if not all(0.0 <= channel <= 1.0 for channel in channels):
        return None
    return to_hex((channels[0], channels[1], channels[2]))


def desktop_accent() -> str | None:
    """The desktop's accent colour, or None -- which is the usual answer.

    Read through `org.freedesktop.appearance/accent-color` on the settings
    portal rather than from any one desktop's own configuration key. That is
    the cross-desktop form of the question, and both backends this project has
    measured implement the portal (docs/DESKTOPS.md); reading a GNOME gsetting
    instead would answer for one desktop and be silently wrong on the rest.

    "Not set" is a normal result and not a failure. Most sessions have no
    accent colour at all -- measured on the machine this was written on, where
    the portal answers and the key does not exist -- and the shipped coral is
    the right answer there, so every path out of here that is not a colour is
    None rather than an exception.

    **On a thread, with a deadline on the whole thing.** `call_sync` takes a
    timeout and `bus_get_sync` takes none, so a session-bus address that
    accepts a connection and never finishes the handshake blocks forever --
    measured at over 25 seconds against a stub socket before the test was
    killed. This runs where the daemon builds its first window, so that is not
    a slow start, it is a daemon that never appears and says nothing about why.
    A thread that is still waiting when the deadline passes is abandoned, not
    joined: it is a daemon thread with nothing to write to, and the answer it
    is waiting for stopped being wanted.
    """
    import threading

    answer: list[str | None] = []

    def work() -> None:
        try:
            answer.append(_ask_accent())
        except Exception:  # noqa: BLE001 - a colour is never worth a crash
            answer.append(None)

    thread = threading.Thread(target=work, daemon=True, name="nabria-accent")
    thread.start()
    thread.join(ACCENT_TIMEOUT)
    return answer[0] if answer else None


def add_css(css: str) -> None:
    """Register a stylesheet above the desktop theme's own.

    Priority matters and is the whole reason this is a function: the theme
    writes `window { background: @window_bg_color; }` into
    ~/.config/gtk-4.0/gtk.css, which loads at PRIORITY_USER and would otherwise
    win -- painting an opaque rectangle around the indicator. The default
    display rather than a window's, because an unrealised window has none yet
    and the provider would attach to nothing.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk

    provider = Gtk.CssProvider()
    if hasattr(provider, "load_from_string"):
        provider.load_from_string(css)
    else:
        provider.load_from_data(css.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 100
    )


def _rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    if len(value) == 3:  # #abc
        value = "".join(channel * 2 for channel in value)
    if len(value) not in (6, 8):
        raise ValueError(f"not a hex colour: {value}")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _read(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load(
    config_dir: Path | None = None, follow_desktop: bool = False
) -> dict[str, tuple[float, float, float]]:
    palette = dict(DARK)
    if follow_desktop:
        # Weakest desktop source first. The accent names one colour; a
        # generated palette names many and is the more specific answer, so it
        # goes over the top; and the user's own file wins over both, below.
        accent = desktop_accent()
        if accent:
            palette["primary"] = accent
        palette.update(_read(PALETTE_PATH))
    if config_dir is not None:
        palette.update(_read(config_dir / "palette.json"))

    colours: dict[str, tuple[float, float, float]] = {}
    for name, default in DARK.items():
        try:
            colours[name] = _rgb(str(palette.get(name, default)))
        except (ValueError, IndexError):
            # One bad entry falls back on its own rather than discarding the
            # whole file: a typo in a hand-written palette should cost that
            # colour, not the theme.
            colours[name] = _rgb(default)
    return colours
