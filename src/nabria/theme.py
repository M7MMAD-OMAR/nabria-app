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
}

FALLBACK = DARK  # kept for callers that referred to it by the old name


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
