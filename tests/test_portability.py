"""Behaviour on machines that are not the one this was written on.

Every case here is a thing that is simply absent somewhere: PipeWire, a
compositor that will say what has focus, layer shell. The rule throughout is
that a missing optional piece degrades, a missing essential one says what to
install, and neither produces a stack trace.
"""

from __future__ import annotations

import subprocess

import pytest

from nabria import inject, recorder


def test_recording_without_pipewire_says_what_to_install(monkeypatch, tmp_path):
    monkeypatch.setattr(recorder.shutil, "which", lambda name: None)
    take = recorder.Recorder(tmp_path / "take.wav")

    with pytest.raises(recorder.MissingRecorder) as raised:
        take.start()
    message = str(raised.value)
    assert "pw-record" in message
    # Points at the installer rather than restating package names. It used to
    # name them, and its list contradicted install.sh's on Arch -- only one of
    # the two can actually be checked against a distribution.
    assert "install.sh" in message


def test_focus_probe_falls_through_to_nothing(monkeypatch):
    # GNOME, and anything else with no way to ask. "" means Ctrl+V, which is
    # right everywhere except a terminal -- the safer of the two guesses.
    monkeypatch.setattr(inject.shutil, "which", lambda name: None)
    assert inject._focused_class() == ""


def test_focus_probe_skips_a_compositor_that_is_not_running(monkeypatch):
    # swaymsg installed but sway not running: it exits non-zero with no JSON,
    # and the next probe must still get its turn.
    monkeypatch.setattr(
        inject.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"swaymsg", "niri"} else None
    )

    def fake_run(command, *args, **kwargs):
        if command[0] == "swaymsg":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout='{"app_id": "firefox"}', stderr="")

    monkeypatch.setattr(inject.subprocess, "run", fake_run)
    assert inject._focused_class() == "firefox"


def test_sway_tree_is_searched_for_the_focused_window(monkeypatch):
    tree = """
    {"nodes": [
       {"nodes": [
          {"focused": false, "app_id": "firefox"},
          {"focused": true, "app_id": "foot"}
       ], "floating_nodes": []}
     ], "floating_nodes": []}
    """
    monkeypatch.setattr(inject.shutil, "which",
                        lambda name: "/usr/bin/swaymsg" if name == "swaymsg" else None)
    monkeypatch.setattr(inject.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=tree))
    assert inject._focused_class() == "foot"
    assert "foot" in inject.TERMINAL_CLASSES  # so this one gets Ctrl+Shift+V


def test_sway_reports_xwayland_windows_by_their_class(monkeypatch):
    # XWayland clients have no app_id; the class is under window_properties.
    tree = '{"focused": true, "window_properties": {"class": "xterm"}, "nodes": []}'
    monkeypatch.setattr(inject.shutil, "which",
                        lambda name: "/usr/bin/swaymsg" if name == "swaymsg" else None)
    monkeypatch.setattr(inject.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=tree))
    assert inject._focused_class() == "xterm"


def test_malformed_compositor_output_does_not_raise(monkeypatch):
    monkeypatch.setattr(inject.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(inject.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="not json"))
    assert inject._focused_class() == ""
