"""Getting the transcript into the focused window.

Two properties matter more than which backend wins. First, a transcript is
never lost: if every mechanism fails the text is still on the clipboard.
Second, borrowing the clipboard never destroys anything -- not a copied image,
and not something the user copied while the paste was in flight.
"""

from __future__ import annotations

import subprocess

import pytest

from nabria import inject


@pytest.fixture
def recorder(monkeypatch):
    """Record every command that would have been run, and run none of them."""
    calls: list[list[str]] = []

    def fake_run(command, *args, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(inject.subprocess, "run", fake_run)
    monkeypatch.setattr(inject.shutil, "which", lambda name: f"/usr/bin/{name}")
    return calls


def test_empty_text_does_nothing(recorder):
    assert inject.deliver("") == "none"
    assert recorder == []


def test_auto_prefers_paste(recorder):
    # wtype and ydotool type one character at a time: 2.59s for 585 characters
    # against 0.02s for a paste, and every keystroke is a compositor round trip.
    assert inject.deliver("hello", "auto") == "paste"
    assert any("wl-copy" in call[0] for call in recorder)


def test_explicit_preference_is_not_second_guessed(recorder):
    assert inject.deliver("hello", "wtype") == "wtype"
    assert recorder[0][0] == "wtype"


def test_clipboard_preference_never_sends_a_keystroke(recorder):
    assert inject.deliver("hello", "clipboard") == "clipboard"
    assert all("wtype" not in call[0] and "ydotool" not in call[0] for call in recorder)


def test_falls_through_to_the_next_backend(monkeypatch):
    attempted: list[str] = []

    def fail_paste(text):
        attempted.append("paste")
        raise inject.InjectionError("no wl-copy")

    def ok_wtype(text):
        attempted.append("wtype")

    monkeypatch.setitem(inject.BACKENDS, "paste", fail_paste)
    monkeypatch.setitem(inject.BACKENDS, "wtype", ok_wtype)
    monkeypatch.setattr(inject.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert inject.deliver("hello", "auto") == "wtype"
    assert attempted == ["paste", "wtype"]


def test_total_failure_still_leaves_the_text_on_the_clipboard(monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(inject, "to_clipboard", lambda text: copied.append(text))
    monkeypatch.setattr(inject.shutil, "which", lambda name: None)
    for name in inject.BACKENDS:
        monkeypatch.setitem(
            inject.BACKENDS, name,
            lambda text: (_ for _ in ()).throw(inject.InjectionError("nope")),
        )

    with pytest.raises(inject.InjectionError):
        inject.deliver("important words", "auto")
    assert copied == ["important words"]


def test_missing_backend_is_reported_by_name(monkeypatch):
    monkeypatch.setattr(inject, "to_clipboard", lambda text: None)
    monkeypatch.setattr(inject.shutil, "which", lambda name: None)
    with pytest.raises(inject.InjectionError) as raised:
        inject.deliver("hello", "wtype")
    assert "not installed" in str(raised.value)


def test_terminals_get_ctrl_shift_v(monkeypatch, recorder):
    monkeypatch.setattr(inject, "_focused_class", lambda: "kitty")
    inject._send_paste_key()
    assert recorder[0] == ["wtype", "-M", "ctrl", "-M", "shift", "-k", "v", "-m", "ctrl", "-m", "shift"]


def test_ordinary_windows_get_ctrl_v(monkeypatch, recorder):
    monkeypatch.setattr(inject, "_focused_class", lambda: "firefox")
    inject._send_paste_key()
    assert "shift" not in recorder[0]


def test_ydotool_modifiers_unwind_in_reverse(monkeypatch, recorder):
    # A modifier released before the key it modifies leaves ctrl stuck down,
    # and the next thing the user types goes somewhere unexpected.
    monkeypatch.setattr(inject, "_focused_class", lambda: "kitty")
    monkeypatch.setattr(inject.shutil, "which", lambda name: None if name == "wtype" else "/usr/bin/x")
    inject._send_paste_key()
    assert recorder[0][2:] == ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]


def test_clipboard_is_restored_when_untouched(monkeypatch):
    restored: list[tuple[str, bytes]] = []

    def fake_run(command, *args, **kwargs):
        if command[:2] == ["wl-copy", "--type"]:
            restored.append((command[2], kwargs.get("input", b"")))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(inject.subprocess, "run", fake_run)
    monkeypatch.setattr(inject.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(inject, "_clipboard_text", lambda: "our transcript")

    inject._restore_clipboard(("image/png", b"\x89PNG"), "our transcript")
    assert restored == [("image/png", b"\x89PNG")]


def test_a_newer_copy_is_never_clobbered(monkeypatch):
    # If the user copied something while the transcript was on the clipboard,
    # putting the old contents back would destroy the newer copy -- worse than
    # leaving a transcript there.
    calls: list[list[str]] = []
    monkeypatch.setattr(inject.subprocess, "run",
                        lambda command, *a, **k: calls.append(list(command)))
    monkeypatch.setattr(inject.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(inject, "_clipboard_text", lambda: "something the user copied")

    inject._restore_clipboard(("text/plain", b"older"), "our transcript")
    assert calls == []


def test_an_image_on_the_clipboard_is_carried_back_by_type(monkeypatch):
    # Reading an image back as text and writing that back would replace it
    # with mojibake, which is a permanent loss rather than a borrowed one.
    monkeypatch.setattr(inject, "_wl_paste", lambda *args:
                        b"image/png\ntext/plain\n" if args == ("--list-types",) else b"\x89PNG\x00\x01")
    assert inject._clipboard_snapshot() == ("image/png", b"\x89PNG\x00\x01")


def test_empty_clipboard_snapshot_is_none(monkeypatch):
    monkeypatch.setattr(inject, "_wl_paste", lambda *args: None)
    assert inject._clipboard_snapshot() is None
