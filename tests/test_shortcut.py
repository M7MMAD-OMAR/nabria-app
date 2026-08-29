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


def test_only_the_desktops_with_an_appendable_file_offer_it(tmp_path, monkeypatch):
    """niri, KDE and GNOME must not, and each for its own reason.

    niri's binds live inside a `binds {}` block, so appending at the end
    parses fine and does nothing -- the worst of the three outcomes. KDE and
    GNOME have a settings dialog and no file to touch at all.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for name in ("hypr", "sway"):
        (tmp_path / name).mkdir()
    (tmp_path / "hypr/hyprland.conf").write_text("", encoding="utf-8")
    (tmp_path / "sway/config").write_text("", encoding="utf-8")

    for variable in ("HYPRLAND_INSTANCE_SIGNATURE", "NIRI_SOCKET", "SWAYSOCK"):
        monkeypatch.delenv(variable, raising=False)

    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    assert shortcut.config_file() == tmp_path / "hypr/hyprland.conf"
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE")

    monkeypatch.setenv("SWAYSOCK", "/run/sway.sock")
    assert shortcut.config_file() == tmp_path / "sway/config"
    monkeypatch.delenv("SWAYSOCK")

    for desktop in ("niri", "KDE", "GNOME", ""):
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
        assert shortcut.config_file() is None, desktop


def test_a_config_that_is_not_there_is_not_offered(tmp_path, monkeypatch):
    """The failure this feature shipped with, and the reason it was found.

    The machine it was written on has no `~/.config/hypr/hyprland.conf` at
    all -- its configuration is generated, and the key was already bound from
    a file the search never looked at. Creating one at the default path gives
    a file the compositor ignores and a wizard that says the key is bound.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    assert shortcut.config_file() is None

    (tmp_path / "hypr").mkdir()
    (tmp_path / "hypr/hyprland.conf").write_text("", encoding="utf-8")
    assert shortcut.config_file() is not None


def test_xdg_config_home_is_honoured(tmp_path, monkeypatch):
    """`config.py` honours it, so this must too, or they disagree about where
    somebody's configuration lives."""
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "elsewhere"))
    (tmp_path / "elsewhere/hypr").mkdir(parents=True)
    (tmp_path / "elsewhere/hypr/hyprland.conf").write_text("", encoding="utf-8")
    assert shortcut.config_file() == tmp_path / "elsewhere/hypr/hyprland.conf"


def test_it_appends_and_never_rewrites(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    original = "# years of somebody's config\nbind = SUPER, Q, killactive\n"
    config.write_text(original, encoding="utf-8")

    shortcut.bind(config)

    written = config.read_text(encoding="utf-8")
    assert written.startswith(original), "the existing file was modified, not appended to"
    assert shortcut.MARKER in written and shortcut.MARKER_END in written
    assert f"exec, {shortcut.command(shortcut.TOGGLE)}" in written


def test_the_old_file_is_kept_and_never_overwritten(tmp_path, monkeypatch):
    """A second run must not copy the file over its own backup.

    That is exactly how the pristine original is lost, and the second run is
    the case where somebody wants it back.
    """
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    config.write_text("original\n", encoding="utf-8")
    backup = tmp_path / "hyprland.conf.nabria-backup"

    shortcut.bind(config)
    assert backup.read_text() == "original\n"

    shortcut.bind(config)
    assert backup.read_text() == "original\n", "the backup was overwritten"


def test_the_backup_does_not_widen_the_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    config.write_text("secret\n", encoding="utf-8")
    config.chmod(0o600)
    shortcut.bind(config)
    mode = (tmp_path / "hyprland.conf.nabria-backup").stat().st_mode & 0o777
    assert mode == 0o600, f"a private config was copied as {mode:o}"


def test_a_failed_write_leaves_the_file_untouched(tmp_path, monkeypatch):
    """A full disk must not leave `bind = CTR` in somebody's config.

    Appending in place did exactly that, and then reported that the write had
    failed -- while the compositor reloaded the truncated file. Reproduced
    with a file-size limit, which is what a quota looks like from here.
    """
    import resource

    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    original = "bind = SUPER, Q, killactive\n"
    config.write_text(original, encoding="utf-8")

    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (len(original) + 20, hard))
    try:
        with pytest.raises(OSError):
            shortcut.bind(config)
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))

    assert config.read_text(encoding="utf-8") == original
    assert not (tmp_path / "hyprland.conf.nabria-new").exists()


def test_a_config_that_is_not_utf8_does_not_crash_it(tmp_path, monkeypatch):
    """UnicodeDecodeError is a ValueError, so it slips past every `except
    OSError` and reaches the user as a button that does nothing."""
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    config.write_bytes("# Gr\xfc\xdfe von J\xf6rg\n".encode("latin-1"))

    assert shortcut.already_bound(config) is False
    shortcut.bind(config)
    assert shortcut.command(shortcut.TOGGLE) in config.read_text(errors="replace")


def test_a_key_bound_in_an_included_file_counts(tmp_path, monkeypatch):
    """Split configurations are the norm, and this project's author runs one.

    Reading only the top-level file reports "not bound" for a key that is
    bound, and appends a second binding for it.
    """
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "conf.d/keys.conf").write_text(
        f"bind = CTRL ALT, Q, exec, {shortcut.command(shortcut.TOGGLE)}\n",
        encoding="utf-8",
    )
    config = tmp_path / "hyprland.conf"

    config.write_text("source = conf.d/keys.conf\n", encoding="utf-8")
    assert shortcut.already_bound(config), "a relative include was not followed"

    # The form people actually write, and the one a literal read finds nothing in.
    config.write_text("source = conf.d/*.conf\n", encoding="utf-8")
    assert shortcut.already_bound(config), "a glob include was not followed"

    config.write_text("# source = conf.d/keys.conf\n", encoding="utf-8")
    assert not shortcut.already_bound(config), "a commented-out include was followed"


def test_a_key_bound_by_hand_counts_as_bound(tmp_path, monkeypatch):
    """Everyone who installed before this button existed pasted it themselves."""
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
    config.write_text("", encoding="utf-8")
    assert not shortcut.already_bound(config)
    shortcut.bind(config)
    assert shortcut.already_bound(config), "a second run would append it again"


def test_a_file_without_a_trailing_newline_is_not_welded_onto(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    config = tmp_path / "hyprland.conf"
    config.write_text("bind = SUPER, Q, killactive", encoding="utf-8")
    shortcut.bind(config)
    lines = config.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "bind = SUPER, Q, killactive"
    assert shortcut.MARKER in lines


def test_a_symlinked_config_stays_a_symlink(tmp_path, monkeypatch):
    """Dotfiles are commonly symlinked into a repository. Replacing the link
    with a regular file takes the file out of that repository silently."""
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    real = tmp_path / "dotfiles" / "hyprland.conf"
    real.parent.mkdir()
    real.write_text("bind = SUPER, Q, killactive\n", encoding="utf-8")
    link = tmp_path / "hyprland.conf"
    link.symlink_to(real)

    shortcut.bind(link)

    assert link.is_symlink(), "the symlink was replaced by a regular file"
    assert shortcut.command(shortcut.TOGGLE) in real.read_text(encoding="utf-8")
