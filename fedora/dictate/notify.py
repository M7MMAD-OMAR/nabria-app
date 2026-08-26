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
        summary,
    ]
    if body:
        command.append(body)
    subprocess.run(command, check=False, timeout=10)
