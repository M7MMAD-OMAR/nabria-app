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


# -- writing the lines into the compositor's own config ---------------------
#
# This appends to a file somebody may have spent years on, so every one of
# these is about what it must NOT do to it.


def test_only_the_desktops_with_an_appendable_file_offer_it(monkeypatch):
    """niri, KDE and GNOME must not, and each for its own reason.

    niri's binds live inside a `binds {}` block, so appending at the end
    parses fine and does nothing -- the worst of the three outcomes. KDE and
    GNOME have a settings dialog and no file to touch at all.
    """
    for variable in ("HYPRLAND_INSTANCE_SIGNATURE", "NIRI_SOCKET", "SWAYSOCK"):
        monkeypatch.delenv(variable, raising=False)

    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    assert shortcut.config_file() is not None
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE")

    monkeypatch.setenv("SWAYSOCK", "/run/sway.sock")
    assert shortcut.config_file() is not None
    monkeypatch.delenv("SWAYSOCK")

    for desktop in ("niri", "KDE", "GNOME", ""):
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
        assert shortcut.config_file() is None, desktop


def test_it_appends_and_never_rewrites(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    original = "# years of somebody's config\nbind = SUPER, Q, killactive\n"
    config.write_text(original, encoding="utf-8")

    shortcut.bind(config)

    written = config.read_text(encoding="utf-8")
    assert written.startswith(original), "the existing file was modified, not appended to"
    assert shortcut.MARKER in written
    assert f"exec, {shortcut.command(shortcut.TOGGLE)}" in written


def test_the_old_file_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    config.write_text("original\n", encoding="utf-8")
    shortcut.bind(config)
    assert (tmp_path / "hyprland.conf.nabria-backup").read_text() == "original\n"


def test_a_file_without_a_trailing_newline_is_not_welded_onto(tmp_path, monkeypatch):
    """The commonest hand-edited file there is, and the one that breaks.

    Without the separator the marker lands on the end of the last line, which
    comments out a working bind and adds one that never parses.
    """
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    config.write_text("bind = SUPER, Q, killactive", encoding="utf-8")
    shortcut.bind(config)
    lines = config.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "bind = SUPER, Q, killactive"
    assert shortcut.MARKER in lines


def test_a_key_bound_by_hand_counts_as_bound(tmp_path, monkeypatch):
    """Everyone who installed before this button existed pasted it themselves.

    Detection is on the command, not on the marker, or a second binding gets
    appended under theirs and the compositor warns about a duplicate.
    """
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    config.write_text(
        f"bind = CTRL ALT, Q, exec, {shortcut.command(shortcut.TOGGLE)}\n",
        encoding="utf-8",
    )
    assert shortcut.already_bound(config)

    config.write_text("bind = SUPER, Q, killactive\n", encoding="utf-8")
    assert not shortcut.already_bound(config)


def test_writing_it_makes_it_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    assert not shortcut.already_bound(config)
    shortcut.bind(config)
    assert shortcut.already_bound(config), "a second run would append it again"


def test_a_missing_file_is_created_with_its_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("SWAYSOCK", "/run/sway.sock")
    config = tmp_path / "never" / "existed" / "config"
    shortcut.bind(config)
    assert f"exec {shortcut.command(shortcut.TOGGLE)}" in config.read_text()
    assert not (config.parent / "config.nabria-backup").exists(), (
        "nothing to back up, so nothing should have been written"
    )
