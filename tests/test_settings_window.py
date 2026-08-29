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
    from gi.repository import GLib, Gtk  # noqa: F401
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


def test_destroying_the_window_stops_the_poll_too(application, fresh_config):
    """`destroy` and `close-request` are not two names for one event.

    GTK emits the first when the program closes the window and the second when
    the user does, and the screenshot script destroys this window once per
    picture. With only the second connected, the source stayed live and went on
    setting a label on a button inside a destroyed window -- holding the whole
    widget tree, the model list and the history rows alive for the rest of the
    process, one per shot.
    """
    window, _ = open_window(application, fresh_config.load())
    assert window.record_source
    window.destroy()
    # The next tick is what notices, since `destroy()` emits no signal a
    # handler can be hung on. What matters is that the source goes, and that
    # nothing touches the destroyed window on the way out.
    assert window._refresh_record() == GLib.SOURCE_REMOVE
    assert window.record_source == 0


# -- letting go of things -----------------------------------------------------


def test_a_destructive_button_asks_before_it_does_it(application, fresh_config):
    """Two presses, and no dialog.

    `Gtk.AlertDialog` arrived in GTK 4.10 and Debian stable ships 4.8, so a
    dialog here would be two code paths for one question -- on a control whose
    entire job is to be unambiguous.
    """
    done = []
    button = settings_window._Confirm("Delete", "Really delete?", lambda: done.append(1))

    button.emit("clicked")
    assert button.get_label() == "Really delete?"
    assert button.has_css_class("destructive-action")
    assert done == [], "one press was enough, which is the bug this prevents"

    button.emit("clicked")
    assert done == [1]
    assert button.get_label() == "Delete", "it stayed armed after doing it"
    assert not button.has_css_class("destructive-action")


def test_deleting_the_transcripts_takes_the_audio_with_them(fresh_config, tmp_path):
    """Otherwise nothing that matters has been deleted.

    `keep_audio` leaves a recording of everything ever said in the room. A
    person who deleted their transcripts and was left with the audio has been
    told something untrue.
    """
    from nabria import history

    # Asserted, not assumed. This test deleted the author's own transcripts
    # when the path was resolved at import and this file imported the module
    # directly instead of through the fixture that reloaded it.
    assert history._path().is_relative_to(tmp_path)

    audio = fresh_config.STATE_DIR / "take-1.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"RIFF....")
    # The directory app.py actually files failed takes into. Writing this as
    # STATE_DIR/failed made the test pass while clear() swept a directory
    # nothing was ever written to, leaving every failed take on disk.
    failed = fresh_config.FAILED_DIR / "take-2.wav"
    failed.parent.mkdir(parents=True, exist_ok=True)
    failed.write_bytes(b"RIFF....")

    history.append("something said out loud", 2.0, 1.0, audio=str(audio))
    assert history.recent(10)

    assert history.clear() == 1
    assert history.recent(10) == []
    assert not audio.exists()
    # Nothing in the log ever pointed at this one -- it is a take that could
    # not be transcribed, which is exactly why it was kept.
    assert not failed.exists()


def test_removing_a_model_gives_the_disk_back(fresh_config):
    from nabria import models

    fresh_config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model = fresh_config.MODEL_DIR / "ggml-base.bin"
    model.write_bytes(b"lmgg" + b"\0" * 100)

    assert models.remove(model) is True
    assert models.models_in(fresh_config.MODEL_DIR) == []
    # Twice is not an error: the file is gone either way, which is what was
    # asked for.
    assert models.remove(model) is False


def test_removing_a_model_whose_link_is_already_dead(fresh_config, tmp_path):
    """`exists()` is False for a dangling link and the name is still taken.

    `adopt` leaves one of these when the original is deleted, and a delete
    button that cannot clear it would leave a name nothing can reuse.
    """
    from nabria import models

    fresh_config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    gone = tmp_path / "gone.bin"
    gone.write_bytes(b"lmgg")
    link = fresh_config.MODEL_DIR / "ggml-x.bin"
    link.symlink_to(gone)
    gone.unlink()

    assert models.remove(link) is True
    assert not link.is_symlink()
