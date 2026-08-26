"""Puts the transcript into whatever window currently has focus.

Two independent mechanisms, tried in order, because neither works everywhere:

  wtype    speaks the Wayland virtual-keyboard protocol straight to the
           compositor. No daemon, no permissions, but a handful of toolkits
           ignore virtual keyboards.
  ydotool  injects at the kernel level through /dev/uinput, so nothing can
           tell it apart from the real keyboard. Needs ydotoold running.

If both fail the text still reaches the clipboard, so a dictation is never
simply lost.
"""

from __future__ import annotations

import shutil
import subprocess

TIMEOUT = 30


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


BACKENDS = {"wtype": _wtype, "ydotool": _ydotool}


def deliver(text: str, preference: str = "auto") -> str:
    """Type the text; returns the backend that actually did it."""
    if not text:
        return "none"

    if preference == "clipboard":
        to_clipboard(text)
        return "clipboard"

    order = ["wtype", "ydotool"] if preference == "auto" else [preference]
    failures: list[str] = []
    for name in order:
        backend = BACKENDS.get(name)
        if backend is None or not shutil.which(name):
            failures.append(f"{name}: not installed")
            continue
        try:
            backend(text)
            return name
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
            failures.append(f"{name}: {detail or exc.returncode}")
        except (subprocess.TimeoutExpired, OSError) as exc:
            failures.append(f"{name}: {exc}")

    to_clipboard(text)
    raise InjectionError("; ".join(failures))
