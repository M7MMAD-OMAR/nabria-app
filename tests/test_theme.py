"""The palette, and the two optional places it can come from.

The app ships its own colours and must look the same on a bare Sway session as
under a fully themed desktop, so what is checked here is mostly that the
overrides stay *optional* -- and that reading one cannot take the window down
with it.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi", reason="PyGObject is not installed")

from nabria import theme  # noqa: E402




def test_the_desktop_accent_is_used_only_when_asked_for(monkeypatch, tmp_path):
    """The app ships its own colour and looks the same everywhere by default.

    A design that quietly hands its accent to whatever desktop is installed is
    not a design, so this is behind the same switch as the generated palette
    rather than behind a second one nobody would find.
    """
    monkeypatch.setattr(theme, "desktop_accent", lambda: "#00ff00")
    monkeypatch.setattr(theme, "PALETTE_PATH", tmp_path / "absent.json")

    assert theme.to_hex(theme.load(tmp_path, False)["primary"]) == theme.DARK["primary"]
    assert theme.to_hex(theme.load(tmp_path, True)["primary"]) == "#00ff00"


def test_a_desktop_with_no_accent_keeps_the_shipped_one(monkeypatch, tmp_path):
    # The usual case, and not a failure: measured on the machine this was
    # written on, the portal answers and the key does not exist.
    monkeypatch.setattr(theme, "desktop_accent", lambda: None)
    monkeypatch.setattr(theme, "PALETTE_PATH", tmp_path / "absent.json")
    assert theme.to_hex(theme.load(tmp_path, True)["primary"]) == theme.DARK["primary"]


def test_a_hand_written_palette_still_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(theme, "desktop_accent", lambda: "#00ff00")
    monkeypatch.setattr(theme, "PALETTE_PATH", tmp_path / "absent.json")
    (tmp_path / "palette.json").write_text('{"primary": "#0000ff"}', encoding="utf-8")
    assert theme.to_hex(theme.load(tmp_path, True)["primary"]) == "#0000ff"


def test_the_accent_reader_never_raises_on_this_machine():
    """Whatever the session is, it answers with a colour or with None.

    It runs on the path that draws the first window, so an exception here is a
    window that never appears -- and every desktop answers this differently or
    not at all.
    """
    accent = theme.desktop_accent()
    assert accent is None or accent.startswith("#")
