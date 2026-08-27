"""Desktop notifications.

Anything the user needs to *read* goes here rather than into the orb: swaync
is already the system's notification surface, it stacks, it keeps a history,
and it does not sit on top of the window being typed into.
"""

from __future__ import annotations

import shutil
import subprocess

APP_NAME = "dictate"


def send(summary: str, body: str = "", urgency: str = "normal") -> None:
    if not shutil.which("notify-send"):
        return
    command = [
        "notify-send",
        "--app-name", APP_NAME,
        "--urgency", urgency,
        "--icon", "audio-input-microphone",
        # Everything after `--` is a positional argument. Without it notify-send
        # reads any text starting with a dash as an option and silently drops
        # the whole notification -- which is exactly what happened to the one
        # that reports a level, because a level reads "-66 dBFS".
        "--",
        summary,
    ]
    if body:
        command.append(body)
    subprocess.run(command, check=False, timeout=10)
