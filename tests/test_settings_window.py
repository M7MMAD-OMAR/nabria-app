"""The settings window, and the one thing in it that is not a setting.

The launcher entry opens this window, and for anyone who has not bound a key
it is the only way into the application at all -- on GNOME and KDE the
shortcut is a settings dialog somebody has to find first. So the button that
takes a dictation from here is not a convenience; without it, a fresh install
on those desktops is installed and unusable until the user goes hunting.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi", reason="PyGObject is not installed")
gi.require_version("Gtk", "4.0")
try:
    from gi.repository import Gtk  # noqa: F401
except (ImportError, ValueError) as exc:  # pragma: no cover
    pytest.skip(f"GTK 4 is unavailable: {exc}", allow_module_level=True)

from conftest import display_available  # noqa: E402

if not display_available():  # pragma: no cover - environment dependent
    pytest.skip("no display", allow_module_level=True)

from nabria import i18n, settings_window  # noqa: E402


def open_window(application, settings, state="idle"):
    pressed = []
    window = settings_window.SettingsWindow(
        application, settings, lambda *_: None,
        on_toggle=lambda: pressed.append(True),
        state=lambda: state,
    )
    return window, pressed


def test_the_button_says_what_pressing_it_will_do(application, fresh_config):
    settings = fresh_config.load()
    for state, key in (
        ("idle", "settings.record.start"),
        ("recording", "settings.record.stop"),
        ("working", "settings.record.working"),
    ):
        window, _ = open_window(application, settings, state)
        assert window.record.get_label() == i18n.t(key)
        window.destroy()


def test_there_is_nothing_to_press_while_a_take_is_being_typed(
    application, fresh_config
):
    # The audio is already recorded and is on its way to the document. There
    # is no start and no stop in that state, and a live button would be
    # offering one.
    window, _ = open_window(application, fresh_config.load(), "working")
    assert not window.record.get_sensitive()
    window.destroy()


def test_pressing_it_asks_the_daemon(application, fresh_config):
    window, pressed = open_window(application, fresh_config.load())
    window.record.emit("clicked")
    assert pressed == [True]
    window.destroy()


def test_without_a_daemon_there_is_no_button_at_all(application, fresh_config):
    """Rather than one that does nothing.

    This is how the screenshot script and most of the suite build the window,
    and a dead control in a published picture is worse than none.
    """
    window = settings_window.SettingsWindow(
        application, fresh_config.load(), lambda *_: None
    )
    assert not hasattr(window, "record")
    assert window.record_source == 0
    window.destroy()


def test_closing_the_window_stops_the_poll(application, fresh_config):
    """It reads the daemon's state on a timer, and the timer has to go.

    A source left behind holds this window alive and goes on calling into it
    after it is gone -- and the window is rebuilt from scratch every time it is
    opened, so they would accumulate one per opening.
    """
    window, _ = open_window(application, fresh_config.load())
    assert window.record_source
    window.emit("close-request")
    assert window.record_source == 0
    window.destroy()
