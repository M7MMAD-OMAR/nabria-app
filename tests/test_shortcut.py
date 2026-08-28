"""Which compositor we think we are on, and what we tell the user to paste.

Getting this wrong is not fatal -- the fallback is a generic instruction --
but getting it wrong *confidently* is: a Hyprland line pasted into a sway
config is a worse outcome than "bind this command however your desktop does".
"""

from __future__ import annotations

import pytest

from nabria import shortcut


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("HYPRLAND_INSTANCE_SIGNATURE", "SWAYSOCK", "NIRI_SOCKET",
                 "XDG_CURRENT_DESKTOP"):
        monkeypatch.delenv(name, raising=False)


def test_nothing_known_still_gives_a_usable_answer():
    assert shortcut.detect() == ""
    lines = shortcut.instructions()
    assert any("nabria toggle" in line for line in lines)


@pytest.mark.parametrize(
    "variable,expected",
    [
        ("HYPRLAND_INSTANCE_SIGNATURE", "hyprland"),
        ("SWAYSOCK", "sway"),
        ("NIRI_SOCKET", "niri"),
    ],
)
def test_socket_variables_identify_the_compositor(monkeypatch, variable, expected):
    monkeypatch.setenv(variable, "/run/whatever")
    assert shortcut.detect() == expected


def test_hyprland_wins_over_a_generic_desktop_name(monkeypatch):
    # XDG_CURRENT_DESKTOP is frequently set to something unhelpful alongside
    # the real thing, so the specific signal has to be checked first.
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert shortcut.detect() == "hyprland"


@pytest.mark.parametrize("desktop,expected", [
    ("KDE", "kde"), ("GNOME", "gnome"), ("sway", "sway"),
    ("Hyprland", "hyprland"), ("niri", "niri"),
])
def test_desktop_name_is_matched_case_insensitively(monkeypatch, desktop, expected):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    assert shortcut.detect() == expected


def test_each_compositor_gets_its_own_syntax(monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    assert any(line.startswith("bind = ") for line in shortcut.instructions())

    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE")
    monkeypatch.setenv("SWAYSOCK", "/run/sway")
    assert any(line.startswith("bindsym ") for line in shortcut.instructions())

    monkeypatch.delenv("SWAYSOCK")
    monkeypatch.setenv("NIRI_SOCKET", "/run/niri")
    assert any("spawn" in line for line in shortcut.instructions())


def test_the_first_line_says_where_and_the_rest_are_pasteable(monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    where, *lines = shortcut.instructions()
    assert where.endswith(":")
    assert lines and all("nabria" in line for line in lines)
