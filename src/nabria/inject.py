"""Puts the transcript into whatever window currently has focus.

Three mechanisms, tried in order, because none works everywhere:

  paste    puts the text on the clipboard and sends one paste keystroke. The
           whole transcript lands at once, in constant time.
  wtype    speaks the Wayland virtual-keyboard protocol straight to the
           compositor. No daemon, no permissions, but a handful of toolkits
           ignore virtual keyboards.
  ydotool  injects at the kernel level through /dev/uinput, so nothing can
           tell it apart from the real keyboard. Needs ydotoold running.

Paste comes first because the other two type character by character: a minute
of speech is well over a thousand keystrokes, which is seconds of watching text
crawl across the screen, and every one of them is a round trip through the
compositor. One paste is one event no matter how long the transcript is.

**The paste keystroke goes through ydotool first, and wtype only as a
fallback** -- the reverse of the order used for typing, and measured rather
than assumed. Into a real focused text entry on Hyprland 0.56.2,
`wtype -M ctrl -k v -m ctrl` landed 0 times out of 15 while exiting 0 every
time, so the daemon logged "typed via paste" for transcripts that never
arrived anywhere. ydotool landed 12 out of 12 once given PASTE_SETTLE. A tool
that reports success for work it did not do is the exact failure this whole
application exists to prevent, so the sender that cannot be trusted to report
its own failure is tried last.

XWayland clients read the X11 CLIPBOARD selection, which is not the selection
`wl-copy` takes. Where the compositor bridges the two this is invisible; where
it does not, a pasted transcript never reaches those windows. Measured on
Hyprland 0.56.2 with the `wl-copy` owner still alive and serving `wl-paste`
fine, an X11 client could not convert the selection even to TARGETS, ten times
out of ten. So for an XWayland target the selection is taken from the X11 side
instead, with `xclip` or `xsel`, and that was measured landing an Arabic
transcript into a focused XWayland entry through the ordinary paste keystroke.

The previous version of this file refused to paste into XWayland at all and
fell through to typing. For Arabic that left no path whatsoever, since typing
non-ASCII is refused too, so every dictation into such a window ended on the
clipboard for the user to paste by hand.

If everything fails the text is still on the clipboard, so a dictation is never
simply lost.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

TIMEOUT = 30

# Terminals paste on Ctrl+Shift+V, because Ctrl+V is a control character there.
# Matched against the focused window's class; anything unlisted gets Ctrl+V.
#
# A shipped list is a guess about software this machine cannot enumerate, and a
# terminal that is not on it gets a keystroke that does nothing -- so the
# `terminal_classes` setting extends it, the same escape hatch `gpu_select` and
# `inject` already provide for the other shipped guesses. Matched casefolded,
# because window classes are not consistently capitalised.
TERMINAL_CLASSES = ("kitty", "foot", "alacritty", "wezterm", "org.wezfurlong.wezterm",
                    "com.mitchellh.ghostty", "konsole", "org.gnome.Console", "xterm")

# How long to let the focused window settle before the paste keystroke.
#
# Measured on Hyprland 0.56.2 into a real focused text entry: firing the key
# immediately after wl-copy landed 11 times in 12, with a 120 ms pause 12 in
# 12. The clipboard offer and the key are two separate trips through the
# compositor, and the target has to have processed the first before the
# second arrives or it pastes what was there before, or nothing.
PASTE_SETTLE = 0.12

# Order in which the paste keystroke is attempted.
#
# ydotool first, which is the reverse of the typing order below, and measured
# rather than assumed: `wtype -M ctrl -k v -m ctrl` landed 0 times in 15 into
# a focused GTK entry on this compositor while EXITING 0 every time, so the
# app reported "typed via paste" for a transcript that never arrived. That is
# the failure this whole tool exists to prevent, so the sender that cannot be
# trusted to report its own failure goes last.
#
# wtype is kept as the fallback because it needs no daemon: on a machine with
# no ydotoold it is the only thing that can send the key at all.
PASTE_SENDERS = ("ydotool", "wtype")

# How long the pasted text stays on the clipboard before the previous contents
# come back. Long enough for the target application to have read the offer --
# the clipboard is a live negotiation on Wayland, not a value that is handed
# over, so restoring it immediately would cancel the paste being served.
RESTORE_DELAY = 1.5


# How the X11 CLIPBOARD selection is taken, for an XWayland target.
#
# Both read the text on stdin and both fork a process that stays alive to serve
# the selection, because X11 selections are owned rather than stored. xsel is
# listed first for a measured reason, see `_to_x11_clipboard`.
X11_SETTERS = (
    ("xsel", ("xsel", "--clipboard", "--input")),
    ("xclip", ("xclip", "-selection", "clipboard", "-i")),
)

# ...and how it is read back, to check our text is still there before restoring.
X11_GETTERS = (
    ("xsel", ("xsel", "--clipboard", "--output")),
    ("xclip", ("xclip", "-selection", "clipboard", "-o")),
)


class InjectionError(RuntimeError):
    pass


def _x11_display() -> str:
    """The X display for the X11 clipboard tools, or "" when there is none.

    Inheriting `DISPLAY` is not enough and this is the trap the whole XWayland
    path turns on. The daemon is a systemd user unit, and it starts before
    Hyprland imports DISPLAY into the user manager: measured on this machine,
    the running unit's environment carries WAYLAND_DISPLAY and XDG_RUNTIME_DIR
    and no DISPLAY at all, while `systemctl --user show-environment` has since
    picked up DISPLAY=:0. So every xclip call would fail with "Can't open
    display" in the daemon while working perfectly in a terminal.

    The socket directory is used rather than asking systemd because it needs no
    subprocess on a path that runs before every paste. XAUTHORITY is
    deliberately not hunted for: measured, xclip authenticated to Xwayland with
    DISPLAY alone and no XAUTHORITY set.
    """
    display = os.environ.get("DISPLAY", "")
    if display:
        return display
    try:
        names = sorted(entry.name for entry in Path("/tmp/.X11-unix").iterdir())
    except OSError:
        return ""
    for name in names:
        # `X0` is a display; `X0_` and friends are not, so this is not a
        # `startswith` -- an Xwayland socket directory holds both.
        if name.startswith("X") and name[1:].isdigit():
            return ":" + name[1:]
    return ""


def _x11_run(command: tuple[str, ...], display: str,
             text: str | None = None) -> subprocess.CompletedProcess:
    """Run an X11 clipboard tool with a display it can actually open.

    Writing discards both streams rather than capturing them, and that is not
    tidiness. xclip forks a child to serve the selection, because an X11
    selection is owned rather than stored, and that child inherits the pipes:
    measured, `subprocess.run(..., capture_output=True)` hung for the full
    timeout while the text had in fact landed. So a failed write reports its
    exit status and not its message, which is why `_to_x11_clipboard` prefers
    the tool that can be asked.
    """
    environment = {**os.environ, "DISPLAY": display}
    if text is None:
        # Reading does not fork, so its output can be collected normally.
        return subprocess.run(
            list(command), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=TIMEOUT, env=environment,
        )
    return subprocess.run(
        list(command), input=text.encode("utf-8"),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=TIMEOUT, env=environment,
    )


def _to_x11_clipboard(text: str) -> str:
    """Take the X11 CLIPBOARD selection. Returns the tool that took it.

    xsel is tried before xclip only because xclip cannot be asked why it
    failed, per `_x11_run`. Both were measured serving the selection correctly,
    including Arabic UTF-8, and both keep serving it after this returns.
    """
    display = _x11_display()
    if not display:
        raise InjectionError("no X display, so an XWayland window cannot be reached")
    failures: list[str] = []
    for name, command in X11_SETTERS:
        if not shutil.which(name):
            failures.append(f"{name}: not installed")
            continue
        try:
            result = _x11_run(command, display, text)
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append(f"{name}: {exc}")
            continue
        if result.returncode == 0:
            return name
        failures.append(f"{name}: exit {result.returncode}")
    raise InjectionError("; ".join(failures) or "no X11 clipboard tool is installed")


def _x11_clipboard_text() -> str | None:
    """The X11 selection as text, or None when there is none to be had.

    Text only, and deliberately. The Wayland snapshot carries a MIME type
    because reading a copied image back as text and writing that back would
    replace the image with mojibake; here the same rule is kept by refusing to
    snapshot anything that does not come back as text at all. A selection this
    cannot represent is left alone rather than guessed at, which costs a
    transcript staying on the clipboard and destroys nothing.
    """
    display = _x11_display()
    if not display:
        return None
    for name, command in X11_GETTERS:
        if not shutil.which(name):
            continue
        try:
            result = _x11_run(command, display)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        try:
            return result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            # Not text. Restoring this would corrupt it, so there is nothing
            # to snapshot and the caller must not restore.
            return None
    return None


def _restore_x11_clipboard(previous: str, pasted: str) -> None:
    # Same rule as the Wayland side: only if our own text is still there.
    # Something copied in the meantime is newer than what we borrowed, and
    # putting the old contents back would destroy it.
    if _x11_clipboard_text() != pasted:
        return
    try:
        _to_x11_clipboard(previous)
    except (InjectionError, OSError, subprocess.SubprocessError):
        pass


def _wtype(text: str, terminals: tuple[str, ...] = ()) -> None:
    # `terminals` is accepted and unused: every backend takes the same
    # arguments so `deliver` can call them all the same way. A signature that
    # varied per backend would put a special case in the dispatch loop.
    subprocess.run(["wtype", text], check=True, timeout=TIMEOUT, capture_output=True)


def _ydotool(text: str, terminals: tuple[str, ...] = ()) -> None:
    # Typing from stdin rather than argv: escape processing is off for stdin,
    # so backslashes and newlines in the transcript arrive verbatim.
    subprocess.run(
        ["ydotool", "type", "--key-delay", "1", "--key-hold", "1", "--file", "-"],
        input=text.encode("utf-8"),
        check=True,
        timeout=TIMEOUT,
        capture_output=True,
    )


def to_clipboard(text: str) -> None:
    """Leave the text somewhere the user can paste it from by hand.

    This is the net under every delivery failure, and what `always_copy` and
    `inject: clipboard` use, so it has to answer the same question `_paste`
    does: a wl-copy'd transcript is not reachable from an XWayland window on
    this compositor, not even manually, so the fallback whose whole job is
    that nothing is lost would leave the user unable to paste what they had
    just dictated. Both selections are taken when the focused window is
    XWayland, because the user is as likely to paste it somewhere else.

    Wayland first, X11 second, so that where a bridge does work the X11
    selection ends up owned by the tool measured to serve it rather than by
    `wl-copy`. Failures are swallowed: this is already the last resort.
    """
    if shutil.which("wl-copy"):
        subprocess.run(["wl-copy", "--", text], check=False, timeout=TIMEOUT)
    if _focused_is_xwayland():
        try:
            _to_x11_clipboard(text)
        except (InjectionError, OSError, subprocess.SubprocessError):
            pass


def _wl_paste(*args: str) -> bytes | None:
    if not shutil.which("wl-paste"):
        return None
    try:
        result = subprocess.run(["wl-paste", *args], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _clipboard_text() -> str | None:
    data = _wl_paste("--no-newline")
    return None if data is None else data.decode("utf-8", "replace")


def _clipboard_snapshot() -> tuple[str, bytes] | None:
    """Whatever the clipboard holds, as (mime type, bytes).

    Typed rather than text-only on purpose. Pasting has to take ownership of
    the clipboard, so a copied image is displaced either way -- but reading it
    back as text and writing that back would replace the image with mojibake
    and destroy it for good. Carrying the type through hands it back intact.
    """
    listed = _wl_paste("--list-types")
    if not listed:
        return None
    types = [line.strip() for line in listed.decode("utf-8", "replace").splitlines()]
    types = [mime for mime in types if mime]
    if not types:
        return None
    data = _wl_paste("--type", types[0])
    return None if data is None else (types[0], data)


def _ask_hyprland() -> str:
    result = subprocess.run(
        ["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=5
    )
    return str(json.loads(result.stdout).get("class", ""))


def _ask_sway() -> str:
    result = subprocess.run(
        ["swaymsg", "-t", "get_tree"], capture_output=True, text=True, timeout=5
    )

    def focused(node: dict) -> dict | None:
        if node.get("focused"):
            return node
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            found = focused(child)
            if found:
                return found
        return None

    window = focused(json.loads(result.stdout)) or {}
    # app_id on Wayland, window_properties.class for XWayland clients.
    return str(window.get("app_id") or window.get("window_properties", {}).get("class", ""))


def _ask_niri() -> str:
    result = subprocess.run(
        ["niri", "msg", "--json", "focused-window"],
        capture_output=True, text=True, timeout=5,
    )
    return str(json.loads(result.stdout).get("app_id", ""))


# Asking "what has focus" has no cross-desktop answer, so each compositor is
# asked in its own language and the list simply ends where knowledge does.
FOCUS_PROBES = (
    ("hyprctl", _ask_hyprland),
    ("swaymsg", _ask_sway),
    ("niri", _ask_niri),
)


def _focused_class() -> str:
    """The focused window's class, or "" where nothing can say.

    "" is a real answer, not a failure: it means Ctrl+V, which is right
    everywhere except a terminal. Guessing Ctrl+Shift+V instead would be wrong
    far more often.
    """
    for command, probe in FOCUS_PROBES:
        if not shutil.which(command):
            continue
        try:
            found = probe()
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            continue
        if found:
            return found
    return ""


def is_terminal(window_class: str, extra: tuple[str, ...] = ()) -> bool:
    if not window_class:
        # Nothing could say what has focus. Ctrl+V is right everywhere but a
        # terminal; guessing the other way would be wrong far more often.
        return False
    known = {name.casefold() for name in (*TERMINAL_CLASSES, *extra)}
    return window_class.casefold() in known


def paste_key(terminals: tuple[str, ...] = ()) -> str:
    """The keystroke that would paste into the focused window right now.

    For telling the user, not for pressing. When delivery falls through, the
    notification has to name the key they should actually press, and in a
    terminal that is not Ctrl+V -- being told the wrong one, about a
    transcript they cannot see, is the moment this tool looks broken.
    """
    return "Ctrl+Shift+V" if is_terminal(_focused_class(), terminals) else "Ctrl+V"


def _paste_with_ydotool(shift: bool) -> None:
    # Linux input event codes: 29 ctrl, 42 shift, 47 v. `:1` press, `:0`
    # release, and they have to unwind in reverse or the modifier sticks.
    keys = ["29:1"] + (["42:1"] if shift else []) + ["47:1", "47:0"]
    keys += (["42:0"] if shift else []) + ["29:0"]
    subprocess.run(
        ["ydotool", "key", *keys], check=True, timeout=TIMEOUT, capture_output=True
    )


def _paste_with_wtype(shift: bool) -> None:
    modifiers = ["-M", "ctrl"] + (["-M", "shift"] if shift else [])
    release = ["-m", "ctrl"] + (["-m", "shift"] if shift else [])
    subprocess.run(
        ["wtype", *modifiers, "-k", "v", *release],
        check=True, timeout=TIMEOUT, capture_output=True,
    )


PASTE_KEY_SENDERS = {"ydotool": _paste_with_ydotool, "wtype": _paste_with_wtype}


def _send_paste_key(terminals: tuple[str, ...] = ()) -> str:
    """Send the paste keystroke. Returns the tool that sent it.

    Both senders exit 0 whether or not the compositor delivered anything, so
    the return value says which tool ran, not that the text arrived. Ordering
    is what buys reliability here rather than checking: measured into a real
    focused entry on Hyprland 0.56.2, wtype landed 0 of 15 while reporting
    success every time, and ydotool 12 of 12 once given PASTE_SETTLE.
    """
    shift = is_terminal(_focused_class(), terminals)
    # The focus query above already costs a round trip to the compositor, so
    # part of the settle is usually paid before this sleep. It is kept
    # unconditional because the query is skipped when no probe is installed.
    time.sleep(PASTE_SETTLE)
    failures: list[str] = []
    for name in PASTE_SENDERS:
        if not shutil.which(name):
            failures.append(f"{name}: not installed")
            continue
        try:
            PASTE_KEY_SENDERS[name](shift)
            return name
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            detail = getattr(exc, "stderr", b"") or b""
            failures.append(f"{name}: {detail.decode('utf-8', 'replace').strip() or exc}")
    raise InjectionError("; ".join(failures) or "no way to send a paste keystroke")


def _restore_clipboard(previous: tuple[str, bytes], pasted: str) -> None:
    # Only if our own text is still there. If something was copied in the
    # meantime, putting the old contents back would destroy the newer copy --
    # a far worse outcome than leaving a transcript on the clipboard.
    if _clipboard_text() != pasted:
        return
    mime, data = previous
    try:
        subprocess.run(
            ["wl-copy", "--type", mime], input=data, check=False, timeout=TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _focused_is_xwayland() -> bool:
    """Whether the focused window is an XWayland client.

    Only Hyprland is asked, because it is the only compositor here that
    reports it directly and a wrong guess is worse than no guess. It decides
    which selection the transcript is put on, so answering True for a native
    Wayland window would hand it to X11, where that window cannot read it.
    Anything unknown is treated as native, which is the answer that needs no
    extra tool installed and works wherever the two selections are bridged.
    """
    if not shutil.which("hyprctl"):
        return False
    try:
        result = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(json.loads(result.stdout).get("xwayland"))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return False


def _paste_xwayland(text: str, terminals: tuple[str, ...] = ()) -> None:
    """Paste into an XWayland window by way of the X11 selection.

    That window reads X11's CLIPBOARD, and on this compositor nothing crosses
    into it from Wayland, so `wl-copy` offers the text to a selection the
    target never looks at and the paste keystroke inserts whatever X11 held
    before. Taking the X11 selection directly is what reaches it, measured
    landing an Arabic transcript into a focused XWayland entry.

    Raising here is a real fallback and not a dead end: `deliver` moves on to
    typing, which does not use the clipboard at all.
    """
    previous = _x11_clipboard_text()
    _to_x11_clipboard(text)
    _send_paste_key(terminals)
    if previous is not None and previous != text:
        timer = threading.Timer(RESTORE_DELAY, _restore_x11_clipboard, (previous, text))
        timer.daemon = True
        timer.start()


def _paste(text: str, terminals: tuple[str, ...] = ()) -> None:
    if _focused_is_xwayland():
        _paste_xwayland(text, terminals)
        return
    if not shutil.which("wl-copy"):
        raise InjectionError("wl-copy is not installed")
    previous = _clipboard_snapshot()
    subprocess.run(["wl-copy", "--", text], check=True, timeout=TIMEOUT)
    _send_paste_key(terminals)
    if previous is not None and previous[1] != text.encode("utf-8"):
        timer = threading.Timer(RESTORE_DELAY, _restore_clipboard, (previous, text))
        timer.daemon = True
        timer.start()


BACKENDS = {"paste": _paste, "wtype": _wtype, "ydotool": _ydotool}


def deliver(text: str, preference: str = "auto",
            terminals: tuple[str, ...] = (), log=None) -> str:
    """Type the text; returns the backend that actually did it.

    `log`, when given, receives one English line per backend failure, so a
    fall-through explains itself in nabria.log instead of the reader seeing
    only "via wtype" and having to guess why the paste never happened.
    """
    if not text:
        return "none"

    if preference == "clipboard":
        to_clipboard(text)
        return "clipboard"

    def note(message: str) -> None:
        if log is not None:
            log(message)

    # Typing is only safe for pure ASCII. wtype and ydotool synthesize
    # keystrokes on the active layout, so a toolkit that is sitting on a
    # Latin layout turns every Arabic character into the key it shares with
    # -- measured 2026-09-03 into a native (not XWayland) Electron window:
    # 796 characters went out as "123456783590-=-3-6q39q1..." and exited 0,
    # while the same text landed intact in a GTK entry. There is no way to
    # ask a window what layout it is on, so the safe policy is not to type
    # non-ASCII at all: auto narrows to paste, and if the paste cannot be
    # sent the text is left on the clipboard behind a notification rather
    # than mangled into the focused window. An explicit `inject: wtype` in
    # the config is the user's call and is honoured as asked.
    if preference == "auto" and not text.isascii():
        note("paste only: non-ASCII text would be garbled by typing")
        order = ["paste"]
    else:
        order = ["paste", "wtype", "ydotool"] if preference == "auto" else [preference]
    failures: list[str] = []
    for name in order:
        backend = BACKENDS.get(name)
        if backend is None:
            failures.append(f"{name}: unknown backend")
            continue
        # paste is not a command of its own -- it composes wl-copy with
        # whichever key-sending tool is present, and checks that itself.
        if name != "paste" and not shutil.which(name):
            failures.append(f"{name}: not installed")
            continue
        try:
            backend(text, terminals)
            return name
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
            failures.append(f"{name}: {detail or exc.returncode}")
            note(f"{name} failed: {detail or exc.returncode}")
        except (subprocess.TimeoutExpired, OSError, InjectionError) as exc:
            failures.append(f"{name}: {exc}")
            note(f"{name} failed: {exc}")

    to_clipboard(text)
    raise InjectionError("; ".join(failures))
