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


_serial = iter(range(1, 10_000))


@pytest.fixture
def application():
    # A fresh id per test, and NON_UNIQUE. Without the flag the application
    # would hand its activation to the daemon already running on this machine;
    # without the unique id the second registration collides, because the first
    # is still exported on the session bus.
    from gi.repository import Gio

    app = Gtk.Application(
        application_id=f"com.sbarah.NabriaTest{next(_serial)}",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register()
    yield app


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
