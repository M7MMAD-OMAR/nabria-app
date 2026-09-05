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


def test_an_xwayland_window_is_pasted_into_through_x11(monkeypatch):
    """The X11 selection is what an XWayland window actually reads.

    Measured on Hyprland 0.56.2, with the `wl-copy` owner alive and serving
    `wl-paste` fine, an X11 client could not convert the selection even to
    TARGETS, ten times out of ten -- so a wl-copy'd transcript is offered to a
    selection the target never looks at. Taking the X11 selection instead was
    measured landing an Arabic transcript into a focused XWayland entry.

    Before this, `_paste` refused outright and `deliver` fell through to
    typing; for Arabic, which is never typed, that left no path at all.
    """
    taken: list[str] = []
    monkeypatch.setattr(inject, "_focused_is_xwayland", lambda: True)
    monkeypatch.setattr(inject, "_x11_clipboard_text", lambda: None)
    monkeypatch.setattr(inject, "_to_x11_clipboard", lambda text: taken.append(text))
    monkeypatch.setattr(inject, "_send_paste_key", lambda terminals=(): "ydotool")

    inject._paste("the words I said")
    assert taken == ["the words I said"]


def test_an_xwayland_paste_never_uses_wl_copy(monkeypatch, recorder):
    # wl-copy takes a selection this window cannot read, so sending it there
    # would be the silent no-op the whole XWayland branch exists to remove.
    monkeypatch.setattr(inject, "_focused_is_xwayland", lambda: True)
    monkeypatch.setattr(inject, "_x11_clipboard_text", lambda: None)
    monkeypatch.setattr(inject, "_x11_display", lambda: ":0")
    monkeypatch.setattr(inject, "_focused_class", lambda: "firefox")

    inject._paste("the words I said")
    assert not any(command[0] == "wl-copy" for command in recorder)


def test_no_x_display_falls_through_instead_of_pretending(monkeypatch):
    """A daemon with no DISPLAY must fail loudly enough to fall through.

    The systemd unit starts before Hyprland imports DISPLAY into the user
    manager, so the daemon's own environment has none: measured, the running
    unit carried WAYLAND_DISPLAY and no DISPLAY at all. `_x11_display` looks
    at the socket directory for that reason, and when even that is empty the
    only honest answer is that the window cannot be reached.
    """
    monkeypatch.setattr(inject, "_focused_is_xwayland", lambda: True)
    monkeypatch.setattr(inject, "_x11_display", lambda: "")
    with pytest.raises(inject.InjectionError, match="X display"):
        inject._paste("the words I said")


def test_arabic_reaches_an_xwayland_window(monkeypatch):
    """The whole bug, end to end.

    Auto narrows to paste for non-ASCII, paste used to refuse XWayland, and
    so an Arabic transcript dictated into an XWayland window had no delivery
    path whatsoever and was left on the clipboard to be pasted by hand.
    """
    taken: list[str] = []
    monkeypatch.setattr(inject, "_focused_is_xwayland", lambda: True)
    monkeypatch.setattr(inject, "_x11_clipboard_text", lambda: None)
    monkeypatch.setattr(inject, "_to_x11_clipboard", lambda text: taken.append(text))
    monkeypatch.setattr(inject, "_send_paste_key", lambda terminals=(): "ydotool")

    assert inject.deliver("نص عربي", "auto") == "paste"
    assert taken == ["نص عربي"]


def test_an_x11_selection_that_is_not_text_is_not_restored(monkeypatch):
    """A copied image must not come back as mojibake.

    The Wayland snapshot carries a MIME type for exactly this reason. The X11
    side keeps the same rule by refusing to snapshot what does not decode as
    text, so the worst case is a transcript left on the clipboard rather than
    a destroyed image.
    """
    monkeypatch.setattr(inject, "_x11_display", lambda: ":0")
    monkeypatch.setattr(inject.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        inject, "_x11_run",
        lambda command, display, text=None: subprocess.CompletedProcess(
            command, 0, stdout=b"\xff\xd8\xff\xe0 not utf-8", stderr=b"",
        ),
    )
    assert inject._x11_clipboard_text() is None


def test_the_x11_setter_is_never_asked_to_capture_output(monkeypatch):
    """xclip forks a child that inherits the pipes and holds them open.

    Measured: `subprocess.run(..., capture_output=True)` hung for the full
    timeout while the text had in fact landed on the selection. A paste that
    blocks for thirty seconds is worse than one that fails.
    """
    seen: list[dict] = []

    def fake_run(command, *args, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(inject.subprocess, "run", fake_run)
    monkeypatch.setattr(inject.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(inject, "_x11_display", lambda: ":0")

    inject._to_x11_clipboard("the words I said")
    assert seen and seen[0].get("capture_output") is not True
    assert seen[0].get("stdout") is subprocess.DEVNULL
    assert seen[0].get("stderr") is subprocess.DEVNULL


def test_the_x11_setter_is_given_a_display(monkeypatch):
    # The daemon has none of its own, so inheriting the environment is not
    # enough and this is the difference between working and "Can't open
    # display" in production.
    seen: list[dict] = []

    def fake_run(command, *args, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(inject.subprocess, "run", fake_run)
    monkeypatch.setattr(inject.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(inject, "_x11_display", lambda: ":7")

    inject._to_x11_clipboard("the words I said")
    assert seen[0]["env"]["DISPLAY"] == ":7"


def test_a_display_is_found_when_the_environment_has_none(monkeypatch, tmp_path):
    # `X0_` and friends share the socket directory with real displays, so this
    # cannot be a startswith.
    (tmp_path / "X0_").touch()
    (tmp_path / "X1").touch()
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(inject, "Path", lambda _: tmp_path)
    assert inject._x11_display() == ":1"


def test_the_clipboard_fallback_reaches_an_xwayland_window(monkeypatch, recorder):
    """`to_clipboard` is the net under every failure, so it has the same problem.

    On an XWayland target a wl-copy'd transcript cannot be pasted even by
    hand, which is precisely what the user reported.
    """
    taken: list[str] = []
    monkeypatch.setattr(inject, "_focused_is_xwayland", lambda: True)
    monkeypatch.setattr(inject, "_to_x11_clipboard", lambda text: taken.append(text))

    inject.to_clipboard("the words I said")
    assert taken == ["the words I said"]
    # Both selections, because the user may well paste it somewhere else.
    assert any(command[0] == "wl-copy" for command in recorder)


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


def test_the_paste_key_named_to_the_user_matches_the_window(monkeypatch):
    """A fallen-through transcript is only recoverable if the key is right.

    Ctrl+V in a terminal inserts nothing, and the user is being told this
    about text they cannot see, so a wrong key reads as the whole take having
    been lost.
    """
    monkeypatch.setattr(inject, "_focused_class", lambda: "kitty")
    assert inject.paste_key() == "Ctrl+Shift+V"
    monkeypatch.setattr(inject, "_focused_class", lambda: "firefox")
    assert inject.paste_key() == "Ctrl+V"
    # The user's own additions to the terminal list have to count here too,
    # or the setting fixes the keystroke and not the sentence about it.
    monkeypatch.setattr(inject, "_focused_class", lambda: "st-256color")
    assert inject.paste_key(("st-256color",)) == "Ctrl+Shift+V"
