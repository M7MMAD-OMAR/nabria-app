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

If everything fails the text is still on the clipboard, so a dictation is never
simply lost.
"""

from __future__ import annotations

import shutil
import subprocess
import threading

TIMEOUT = 30

# Terminals paste on Ctrl+Shift+V, because Ctrl+V is a control character there.
# Matched against the focused window's class; anything unlisted gets Ctrl+V.
TERMINAL_CLASSES = ("kitty", "foot", "alacritty", "wezterm", "org.wezfurlong.wezterm",
                    "com.mitchellh.ghostty", "konsole", "org.gnome.Console", "xterm")

# How long the pasted text stays on the clipboard before the previous contents
# come back. Long enough for the target application to have read the offer --
# the clipboard is a live negotiation on Wayland, not a value that is handed
# over, so restoring it immediately would cancel the paste being served.
RESTORE_DELAY = 1.5


class InjectionError(RuntimeError):
    pass


def _wtype(text: str) -> None:
    subprocess.run(["wtype", text], check=True, timeout=TIMEOUT, capture_output=True)


def _ydotool(text: str) -> None:
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
    if shutil.which("wl-copy"):
        subprocess.run(["wl-copy", "--", text], check=False, timeout=TIMEOUT)


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


def _focused_class() -> str:
    if not shutil.which("hyprctl"):
        return ""
    try:
        result = subprocess.run(
            ["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=5
        )
        import json

        return str(json.loads(result.stdout).get("class", ""))
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def _send_paste_key() -> None:
    shift = _focused_class() in TERMINAL_CLASSES
    if shutil.which("wtype"):
        modifiers = ["-M", "ctrl"] + (["-M", "shift"] if shift else [])
        release = ["-m", "ctrl"] + (["-m", "shift"] if shift else [])
        subprocess.run(
            ["wtype", *modifiers, "-k", "v", *release],
            check=True, timeout=TIMEOUT, capture_output=True,
        )
        return
    if shutil.which("ydotool"):
        # Linux input event codes: 29 ctrl, 42 shift, 47 v. `:1` press, `:0`
        # release, and they have to unwind in reverse or the modifier sticks.
        keys = ["29:1"] + (["42:1"] if shift else []) + ["47:1", "47:0"]
        keys += (["42:0"] if shift else []) + ["29:0"]
        subprocess.run(
            ["ydotool", "key", *keys], check=True, timeout=TIMEOUT, capture_output=True
        )
        return
    raise InjectionError("no way to send a paste keystroke")


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


def _paste(text: str) -> None:
    if not shutil.which("wl-copy"):
        raise InjectionError("wl-copy is not installed")
    previous = _clipboard_snapshot()
    subprocess.run(["wl-copy", "--", text], check=True, timeout=TIMEOUT)
    _send_paste_key()
    if previous is not None and previous[1] != text.encode("utf-8"):
        timer = threading.Timer(RESTORE_DELAY, _restore_clipboard, (previous, text))
        timer.daemon = True
        timer.start()


BACKENDS = {"paste": _paste, "wtype": _wtype, "ydotool": _ydotool}


def deliver(text: str, preference: str = "auto") -> str:
    """Type the text; returns the backend that actually did it."""
    if not text:
        return "none"

    if preference == "clipboard":
        to_clipboard(text)
        return "clipboard"

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
            backend(text)
            return name
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
            failures.append(f"{name}: {detail or exc.returncode}")
        except (subprocess.TimeoutExpired, OSError, InjectionError) as exc:
            failures.append(f"{name}: {exc}")

    to_clipboard(text)
    raise InjectionError("; ".join(failures))
