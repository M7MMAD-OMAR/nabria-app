"""The shortcuts portal.

Only the parts that can be checked without a live portal: the request-path
construction the whole request/response pattern depends on, and the promise
that every failure is survivable. Whether a given desktop actually binds
anything is not something a unit test can answer -- that was measured by hand
and written down in docs/DESIGN.md.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gi", reason="PyGObject is not installed")

from nabria import portal  # noqa: E402


class FakeBus:
    def __init__(self, unique_name=":1.234"):
        self._name = unique_name

    def get_unique_name(self):
        return self._name


def test_request_paths_follow_the_portal_naming_rule():
    # The Response signal arrives on a path the portal derives from our bus
    # name: leading colon dropped, dots to underscores. Get this wrong and the
    # subscription matches nothing, the reply is delivered nowhere, and the
    # call hangs with no error at all.
    shortcuts = portal.GlobalShortcuts(lambda name: None, lambda message: None)
    shortcuts.bus = FakeBus(":1.234")
    assert shortcuts._request_path("tok") == (
        "/org/freedesktop/portal/desktop/request/1_234/tok"
    )


def test_request_path_handles_a_name_with_several_dots():
    shortcuts = portal.GlobalShortcuts(lambda name: None, lambda message: None)
    shortcuts.bus = FakeBus(":1.23.45")
    assert "1_23_45" in shortcuts._request_path("tok")


def test_tokens_are_unique_per_call():
    # Two requests in flight at once would otherwise subscribe to the same
    # object path and each receive the other's Response.
    shortcuts = portal.GlobalShortcuts(lambda name: None, lambda message: None)
    assert shortcuts._next_token("createsession") != shortcuts._next_token("createsession")


def test_no_session_bus_is_not_fatal(monkeypatch):
    from gi.repository import GLib

    def refuse(*args, **kwargs):
        raise GLib.Error("no bus")

    monkeypatch.setattr(portal.Gio, "bus_get_sync", refuse)
    logged: list[str] = []
    shortcuts = portal.GlobalShortcuts(lambda name: None, logged.append)

    assert shortcuts.available() is False
    shortcuts.start()  # must not raise; the manual key still works
    assert any("bus" in line for line in logged)


def test_activation_from_another_session_is_ignored():
    # Two applications can hold portal sessions at once and the signal is
    # broadcast, so acting on someone else's would start recording when an
    # unrelated program's shortcut was pressed.
    fired: list[str] = []
    shortcuts = portal.GlobalShortcuts(fired.append, lambda message: None)
    shortcuts.session = "/ours"

    class Payload:
        def __init__(self, session):
            self._session = session

        def unpack(self):
            return (self._session, "toggle", 0, {})

    shortcuts._on_activated(None, None, None, None, None, Payload("/theirs"))
    assert fired == []
    shortcuts._on_activated(None, None, None, None, None, Payload("/ours"))
    assert fired == ["toggle"]


def test_stop_without_a_session_does_nothing():
    shortcuts = portal.GlobalShortcuts(lambda name: None, lambda message: None)
    shortcuts.stop()  # no bus, no session, no exception


def test_the_shortcut_ids_match_the_daemon_commands():
    # They are dispatched straight into the socket handler, so an id that is
    # not a command silently does nothing when the key is pressed.
    from nabria.client import COMMANDS

    for name, _description, _trigger in portal.SHORTCUT_DEFINITIONS:
        assert name in COMMANDS


def test_the_portal_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("NABRIA_NO_PORTAL_SHORTCUTS", "1")
    assert portal.enabled() is False
    monkeypatch.delenv("NABRIA_NO_PORTAL_SHORTCUTS")
    assert portal.enabled() is True
