"""Paths and the one JSON config file.

Everything the daemon needs is resolved here so that no other module has to
know where OpenWhispr happened to leave a binary. The install script copies
(or hard-links) the whisper server and the model into this tool's own
directories precisely so that removing OpenWhispr cannot break dictation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_ID = "dev.sbarah.Dictate"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dictate"
CONFIG_PATH = CONFIG_DIR / "config.json"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "dictate"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "dictate"
LIBEXEC_DIR = Path.home() / ".local/libexec/dictate"
MODEL_DIR = DATA_DIR / "models"
LOG_PATH = STATE_DIR / "dictate.log"

# The control socket lives in the runtime dir so it dies with the login session
# and a stale file can never make the toggle command hang.
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
SOCKET_PATH = RUNTIME_DIR / "dictate.sock"

DEFAULTS: dict[str, Any] = {
    # Transcription engine. Both are filled in by scripts/install.sh.
    "server_binary": str(LIBEXEC_DIR / "whisper-server"),
    "model": str(MODEL_DIR / "ggml-large-v3-turbo.bin"),
    "language": "auto",
    # Fed to whisper as its initial prompt. Mixing both scripts is what tells
    # the model that code-switching is expected, so an Arabic sentence with
    # "terminal" or "Hyprland" in it comes out with those words spelled rather
    # than transliterated. Keep it short: a long prompt starts leaking into
    # the transcript. Set to "" to switch the bias off entirely.
    "vocabulary": "هرميز، تيرمينال، كلود، Hyprland, kitty, systemd, Fedora, Claude Code, git",
    "threads": 8,
    # Vulkan picks physical device 0, which on this hybrid laptop is the Intel
    # iGPU -- 2.5x slower than realtime on large-v3-turbo. This reorders the
    # device list by vendor:device so the RTX 4070 comes first. Identity-based,
    # so it no-ops instead of breaking if the dGPU is absent. Applied ONLY to
    # the whisper subprocess: the GTK UI must stay on the iGPU.
    "gpu_select": "10de:2860",
    "server_port": 0,  # 0 = pick a free port
    # Seconds the loaded model may sit idle before the server is stopped and
    # its ~2.5 GB of VRAM released. The next dictation reloads it while you are
    # still speaking, so the cost is usually invisible. 0 disables unloading.
    "idle_unload_seconds": 900,
    "prewarm": False,
    # Average (RMS) level below which the take is treated as "nothing was
    # said". Without the guard whisper reliably invents a polite sentence out
    # of room tone -- "شكرا للمشاهدة", "Thank you." -- and types it into the
    # document. Raise it if silence still gets through, lower it if quiet
    # speech is being dropped.
    "silence_threshold_dbfs": -42.0,
    # Keep every take's WAV instead of deleting it once transcribed. Off by
    # default because a day of dictation is a lot of audio, but it is the only
    # way to judge a transcript: without the recording there is nothing to
    # compare the words against, and nothing to feed a second model to decide
    # whether a mistake belongs to the engine or to the microphone.
    "keep_audio": False,
    # 0 means no limit. Whisper windows long audio internally, so a long
    # dictation is transcribed whole rather than cut off at a time limit.
    "max_seconds": 0,
    # Copy every transcript to the clipboard, not just the ones that failed to
    # type. Costs you the clipboard on every dictation; buys you a paste when
    # the text lands in the wrong window.
    "always_copy": False,
    # auto | wtype | ydotool | clipboard
    "inject": "auto",
    # bottom-right | bottom-left | top-right | top-left | bottom-center
    "orb_position": "bottom-right",
    "orb_margin": 28,
}


def load() -> dict[str, Any]:
    """Defaults overlaid with the user's file; unknown keys are kept, not dropped."""
    settings = dict(DEFAULTS)
    try:
        settings.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        # A corrupt config must not stop dictation from working at all.
        pass
    for key in ("server_binary", "model"):
        settings[key] = str(Path(settings[key]).expanduser())
    return settings


def save(settings: dict[str, Any]) -> None:
    """Persist the whole settings dict, Arabic kept readable rather than escaped."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def models() -> list[Path]:
    """Every model sitting in the model directory, largest last.

    Scanned rather than hardcoded: dropping a `.bin` in there is how a new
    model is offered, and a half-finished download leaves an `.aria2` or
    `.part` beside it, which is what keeps it out of the list.
    """
    try:
        found = sorted(MODEL_DIR.glob("*.bin"))
    except OSError:
        return []
    return [
        path
        for path in found
        if not path.with_suffix(path.suffix + ".aria2").exists()
        and not path.with_suffix(path.suffix + ".part").exists()
    ]


def write_default_config() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
    return CONFIG_PATH
