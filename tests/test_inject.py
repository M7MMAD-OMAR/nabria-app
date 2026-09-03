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

    def fail_paste(text, terminals=()):
        attempted.append("paste")
        raise inject.InjectionError("no wl-copy")

    def ok_wtype(text, terminals=()):
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
            lambda text, terminals=(): (_ for _ in ()).throw(inject.InjectionError("nope")),
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
    # ydotool is tried first, so this is its shape: 42 is shift, and it is
    # pressed with ctrl and released after the key.
    assert recorder[0] == ["ydotool", "key",
                           "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]


def test_wtype_sends_ctrl_shift_v_when_ydotool_is_absent(monkeypatch, recorder):
    # The fallback has to carry the terminal's modifier too, or a machine
    # without ydotoold pastes a control character into its shell.
    monkeypatch.setattr(inject, "_focused_class", lambda: "kitty")
    monkeypatch.setattr(
        inject.shutil, "which",
        lambda name: None if name == "ydotool" else "/usr/bin/x",
    )
    inject._send_paste_key()
    assert recorder[0] == ["wtype", "-M", "ctrl", "-M", "shift", "-k", "v",
                           "-m", "ctrl", "-m", "shift"]


def test_the_paste_key_prefers_ydotool_over_wtype(monkeypatch, recorder):
    """Order is the fix, and it is the reverse of the typing order.

    Measured into a real focused entry on Hyprland 0.56.2: wtype's Ctrl+V
    landed 0 times in 15 while exiting 0 every time, so the daemon logged
    "typed via paste" for transcripts that never arrived. ydotool landed 12
    in 12. A sender that reports success for work it did not do has to go
    last, or the log lies about the one thing it exists to report.
    """
    monkeypatch.setattr(inject, "_focused_class", lambda: "firefox")
    assert inject._send_paste_key() == "ydotool"
    assert recorder[0][0] == "ydotool"


def test_a_dead_ydotool_falls_back_to_wtype(monkeypatch):
    # ydotoold not running is the ordinary case on a fresh machine, and it
    # must degrade to the other sender rather than losing the paste.
    monkeypatch.setattr(inject, "_focused_class", lambda: "firefox")
    sent: list[list[str]] = []

    def fake_run(command, *args, **kwargs):
        if command[0] == "ydotool":
            raise subprocess.CalledProcessError(1, command, stderr=b"no socket")
        sent.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(inject.subprocess, "run", fake_run)
    monkeypatch.setattr(inject.shutil, "which", lambda name: "/usr/bin/x")

    assert inject._send_paste_key() == "wtype"
    assert sent[0][0] == "wtype"


def test_both_paste_senders_failing_is_reported(monkeypatch):
    # Not silently swallowed: deliver falls through to typing, and the reason
    # both senders failed is what tells a reader why.
    monkeypatch.setattr(inject, "_focused_class", lambda: "firefox")
    monkeypatch.setattr(inject.shutil, "which", lambda name: None)
    with pytest.raises(inject.InjectionError) as raised:
        inject._send_paste_key()
    assert "ydotool" in str(raised.value) and "wtype" in str(raised.value)


def test_an_xwayland_window_is_not_pasted_into(monkeypatch):
    """Paste cannot reach an XWayland client through a broken bridge.

    An XWayland window reads the X11 selection, which the compositor has to
    bridge from Wayland. Measured on this machine that bridge is dead in both
    directions: `wl-copy` followed by `xclip -o` returned nothing, forty times
    out of forty, while `wl-paste` read the value back fine. Pasting there
    inserts whatever X11 held before, so `_paste` refuses and `deliver` falls
    through to typing, which does not use the clipboard at all.
    """
    monkeypatch.setattr(inject, "_focused_is_xwayland", lambda: True)
    monkeypatch.setattr(inject.shutil, "which", lambda name: "/usr/bin/x")
    with pytest.raises(inject.InjectionError, match="XWayland"):
        inject._paste("the words I said")


def test_a_native_window_is_still_pasted_into(monkeypatch, recorder):
    # The guard must not cost the fast path everywhere else: typing a minute
    # of speech one keystroke at a time is the outcome it exists to avoid.
    monkeypatch.setattr(inject, "_focused_is_xwayland", lambda: False)
    monkeypatch.setattr(inject, "_clipboard_snapshot", lambda: None)
    monkeypatch.setattr(inject, "_focused_class", lambda: "firefox")
    inject._paste("the words I said")
    assert any(command[0] == "wl-copy" for command in recorder)


def test_an_unknown_compositor_is_treated_as_native(monkeypatch):
    # Answering True for a window this cannot ask about would send every
    # dictation down the slow typing path on every desktop but Hyprland.
    monkeypatch.setattr(inject.shutil, "which", lambda name: None)
    assert inject._focused_is_xwayland() is False


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


def test_terminal_matching_is_case_insensitive_and_extensible():
    # The shipped list is a guess about software this machine cannot
    # enumerate, so a terminal missing from it must be fixable without a
    # source edit -- otherwise the paste keystroke silently does nothing.
    assert inject.is_terminal("kitty")
    assert inject.is_terminal("KiTTY")
    assert not inject.is_terminal("firefox")
    assert inject.is_terminal("my-terminal", ("my-terminal",))


def test_an_unknown_focus_is_not_treated_as_a_terminal():
    assert inject.is_terminal("") is False


def test_arabic_is_never_typed_in_auto_mode(monkeypatch, recorder):
    """Non-ASCII narrows auto to paste alone.

    wtype and ydotool synthesize keystrokes on the focused window's active
    layout, and a toolkit sitting on a Latin layout turns every Arabic
    character into the key it shares with -- measured 2026-09-03 into a
    native Electron window: 796 characters landed as
    "123456783590-=-3-6q39q1..." while exiting 0, the same text landing
    intact in a GTK entry. A window cannot be asked what layout it is on,
    so auto must not type what typing can corrupt.
    """
    typed: list[str] = []
    monkeypatch.setattr(inject, "_focused_class", lambda: "Hermes")
    monkeypatch.setattr(inject, "_focused_is_xwayland", lambda: False)
    monkeypatch.setattr(inject, "_clipboard_snapshot", lambda: None)
    monkeypatch.setitem(inject.BACKENDS, "wtype",
                        lambda text, terminals=(): typed.append(text))
    monkeypatch.setitem(inject.BACKENDS, "ydotool",
                        lambda text, terminals=(): typed.append(text))

    assert inject.deliver("نص عربي", "auto") == "paste"
    assert typed == []  # not one keystroke of Arabic was synthesized


def test_arabic_survives_a_dead_paste_instead_of_being_typed(monkeypatch):
    """The old code's fall-through is the bug: paste fails, wtype garbles.

    The text must end up on the clipboard behind a notification instead of
    being typed into the focused window as keyboard mush.
    """
    monkeypatch.setattr(inject.shutil, "which", lambda name: None)
    copied: list[str] = []
    monkeypatch.setattr(inject, "to_clipboard", lambda text: copied.append(text))

    with pytest.raises(inject.InjectionError):
        inject.deliver("نص عربي", "auto")
    assert copied == ["نص عربي"]


def test_a_fallen_through_paste_records_its_reason(monkeypatch):
    """deliver explains itself: "via wtype" with no reason is unreadable."""
    notes: list[str] = []
    monkeypatch.setitem(inject.BACKENDS, "paste",
                        lambda text, terminals=(): (_ for _ in ()).throw(
                            inject.InjectionError("no paste key sender")))
    monkeypatch.setitem(inject.BACKENDS, "wtype", lambda text, terminals=(): None)
    monkeypatch.setattr(inject.shutil, "which", lambda name: "/usr/bin/x")

    assert inject.deliver("hello", "auto", log=notes.append) == "wtype"
    assert any("paste" in note and "failed" in note for note in notes)


def test_ascii_still_falls_through_normally(monkeypatch):
    # The paste-only rule is about corruption, not about ASCII: an English
    # transcript keeps the whole ladder, typing included.
    monkeypatch.setitem(inject.BACKENDS, "paste",
                        lambda text, terminals=(): (_ for _ in ()).throw(
                            inject.InjectionError("no wl-copy")))
    monkeypatch.setitem(inject.BACKENDS, "wtype", lambda text, terminals=(): None)
    monkeypatch.setattr(inject.shutil, "which", lambda name: "/usr/bin/x")

    assert inject.deliver("hello", "auto") == "wtype"


def test_an_explicit_typing_preference_is_honoured_even_for_arabic(recorder):
    # `inject: wtype` in the config is the user's own call, and it is the
    # documented escape hatch -- second-guessing it would break the machine
    # where typing is the only mechanism that works at all.
    assert inject.deliver("نص عربي", "wtype") == "wtype"
    assert recorder[0][0] == "wtype"
