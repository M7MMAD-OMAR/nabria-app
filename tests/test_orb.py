"""The indicator, and what it does where layer shell is not available.

GNOME does not implement the protocol, the typelib is a separate package
everywhere, and a Flatpak sandbox is refused it outright. None of those may
stop dictation from working -- the indicator degrading is a worse experience,
an application that will not start is a broken one.
"""

from __future__ import annotations

import importlib

import pytest

gi = pytest.importorskip("gi", reason="PyGObject is not installed")
gi.require_version("Gtk", "4.0")
try:
    from gi.repository import Gtk
except (ImportError, ValueError) as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"GTK 4 is unavailable: {exc}", allow_module_level=True)

from conftest import display_available  # noqa: E402

if not display_available():  # pragma: no cover - environment dependent
    pytest.skip("no display", allow_module_level=True)

from nabria import orb as orb_module  # noqa: E402


@pytest.fixture
def without_layer_shell(monkeypatch):
    """Stand in for a machine where the typelib is not installed."""
    monkeypatch.setattr(orb_module, "LayerShell", None)
    return orb_module


def test_the_module_imports_without_the_typelib():
    # The import used to be unconditional, so a missing package raised at
    # import time and took the whole daemon with it.
    assert importlib.reload(orb_module) is not None


def test_availability_reports_false_without_the_typelib(without_layer_shell):
    assert without_layer_shell.layer_shell_available() is False


def test_the_orb_still_builds_without_layer_shell(without_layer_shell, application):
    indicator = without_layer_shell.Orb(application, {"orb_position": "bottom-center"})
    assert indicator.layered is False
    assert indicator.window.get_decorated() is False
    assert indicator.window.get_resizable() is False


def test_the_orb_uses_layer_shell_when_it_can(application):
    if not orb_module.layer_shell_available():
        pytest.skip("layer shell is not available here either")
    indicator = orb_module.Orb(application, {"orb_position": "bottom-center"})
    assert indicator.layered is True


def test_every_anchor_name_resolves_to_a_real_edge():
    # The table holds strings so it can be read without the typelib; that
    # trades an import error for a typo nobody would notice until the
    # indicator failed to appear in one particular corner.
    if orb_module.LayerShell is None:
        pytest.skip("no typelib to resolve against")
    for vertical, horizontal in orb_module.ANCHORS.values():
        assert hasattr(orb_module.LayerShell.Edge, vertical)
        if horizontal is not None:
            assert hasattr(orb_module.LayerShell.Edge, horizontal)


def test_states_all_have_an_accent_colour():
    for state in ("loading", "recording", "working", "done", "error"):
        assert state in orb_module.ACCENTS


def test_live_transcript_keeps_plain_full_text_and_uses_a_bounded_card(without_layer_shell, application):
    indicator = without_layer_shell.Orb(application, {})
    text = "هلأ عم جرّب Claude Desktop مع Python 3.14، والكلام لازم يطلع متل ما بحكي. " * 30
    indicator.set_transcript(text)
    assert indicator.transcript_label.get_text() == text
    assert not indicator.transcript_label.get_use_markup()
    assert indicator.transcript_label.get_wrap()
    assert indicator.transcript_label.get_direction() == Gtk.TextDirection.RTL
    assert indicator.area.get_content_width() == without_layer_shell.TRANSCRIPT_W
    assert indicator.area.get_content_height() <= without_layer_shell.TRANSCRIPT_MAX_H
    assert indicator.transcript_scroll.get_visible()
    indicator.window.destroy()


def test_working_preserves_live_text_and_clear_returns_to_the_small_orb(without_layer_shell, application, monkeypatch):
    indicator = without_layer_shell.Orb(application, {})
    calls = []
    monkeypatch.setattr(indicator.window, "present", lambda: calls.append("present"))
    indicator.show("recording")
    indicator.set_transcript("This is the complete sentence.")
    indicator.show("working")
    indicator.set_transcript("This is the complete sentence, updated.")
    assert calls == ["present"]
    assert indicator.transcript_label.get_text() == "This is the complete sentence, updated."
    assert indicator.transcript_label.get_direction() == Gtk.TextDirection.LTR
    indicator.clear_transcript()
    assert indicator.transcript_label.get_text() == ""
    assert not indicator.transcript_scroll.get_visible()
    assert indicator.area.get_content_width() == without_layer_shell.WINDOW_W
    assert indicator.area.get_content_height() == without_layer_shell.WINDOW_H
    indicator.window.destroy()


def test_hide_clears_the_previous_take(without_layer_shell, application):
    indicator = without_layer_shell.Orb(application, {})
    indicator.set_transcript("previous take")
    indicator.hide()
    assert indicator.transcript_label.get_text() == ""
    assert indicator.transcript == ""
    indicator.window.destroy()


def test_transcript_height_stays_fixed_as_the_conversation_grows(without_layer_shell, application):
    indicator = without_layer_shell.Orb(application, {})
    indicator.set_transcript("A short sentence.")
    short_height = indicator.area.get_content_height()
    text = "هلأ عم جرّب كلام طويل مع Claude Desktop وPython. " * 200
    indicator.set_transcript(text)
    assert indicator.area.get_content_height() == short_height
    assert short_height == without_layer_shell.TRANSCRIPT_MAX_H
    assert indicator.transcript_label.get_text() == text
    indicator.clear_transcript()
    indicator.window.destroy()


def test_a_new_update_follows_the_tail_even_after_scrolling_back(without_layer_shell, application, monkeypatch):
    from types import SimpleNamespace

    indicator = without_layer_shell.Orb(application, {})
    indicator.set_transcript("An earlier sentence.")
    if indicator._scroll_source:
        without_layer_shell.GLib.source_remove(indicator._scroll_source)
        indicator._scroll_source = 0
    values = []
    adjustment = SimpleNamespace(
        get_upper=lambda: 1200.0, get_page_size=lambda: 128.0,
        set_value=values.append,
    )
    monkeypatch.setattr(indicator.transcript_scroll, "get_vadjustment", lambda: adjustment)
    indicator.set_transcript("An earlier sentence. A new sentence follows.")
    assert indicator._scroll_source
    without_layer_shell.GLib.source_remove(indicator._scroll_source)
    indicator._follow_tail()
    assert values == [1072.0]
    indicator.clear_transcript()
    indicator.window.destroy()
