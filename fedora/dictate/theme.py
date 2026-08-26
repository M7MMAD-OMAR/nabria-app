"""Reads the desktop's live Material You palette.

illogical-impulse regenerates this file from the wallpaper on every theme
change, so taking the colours from it means the orb follows the rest of the
desktop instead of carrying a hand-picked palette that drifts out of step.
"""

from __future__ import annotations

import json
from pathlib import Path

PALETTE_PATH = (
    Path.home() / ".local/state/quickshell/user/generated/colors.json"
)

# Used only when the palette file is missing, e.g. on a fresh machine.
FALLBACK = {
    "primary": "#ffb5a0",
    "error": "#ffb4ab",
    "tertiary": "#e5c48c",
    "surface_container": "#271d1b",
    "outline_variant": "#53433f",
    "on_surface": "#f1dfda",
}


def _rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def load() -> dict[str, tuple[float, float, float]]:
    palette = dict(FALLBACK)
    try:
        palette.update(json.loads(PALETTE_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    colours: dict[str, tuple[float, float, float]] = {}
    for name, default in FALLBACK.items():
        raw = palette.get(name, default)
        try:
            colours[name] = _rgb(str(raw))
        except (ValueError, IndexError):
            colours[name] = _rgb(default)
    return colours
