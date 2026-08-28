"""Paths and the one JSON config file.

Every path the daemon needs is resolved here, so no other module has to know
where the engine or the model live. The install script puts both under this
app's own directories rather than sharing another program's, which is what
keeps uninstalling anything else from breaking dictation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_ID = "com.sbarah.Nabria"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "nabria"
CONFIG_PATH = CONFIG_DIR / "config.json"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "nabria"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "nabria"
LIBEXEC_DIR = Path.home() / ".local/libexec/nabria"
MODEL_DIR = DATA_DIR / "models"
LOG_PATH = STATE_DIR / "nabria.log"

# The control socket lives in the runtime dir so it dies with the login session
# and a stale file can never make the toggle command hang.
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
SOCKET_PATH = RUNTIME_DIR / "nabria.sock"

DEFAULTS: dict[str, Any] = {
    # Transcription engine. Both are filled in by scripts/install.sh.
    "server_binary": str(LIBEXEC_DIR / "whisper-server"),
    "model": str(MODEL_DIR / "ggml-large-v3-turbo.bin"),
    "language": "auto",
    # Fed to whisper as its initial prompt: names and terms it should spell
    # rather than guess at, plus -- for a dialect -- a few of its own function
    # words. Empty by default, because a prompt is not free. Measured over one
    # utterance, a prompt of formal Arabic and Latin technical terms came out
    # *worse* than no prompt at all: it pulled spoken dialect toward the formal
    # register (هلأ became هلا) that the prompt was written in. Add words you
    # actually say. Keep it short -- a long prompt starts leaking into the
    # transcript.
    "vocabulary": "",
    "threads": 8,
    # MESA_VK_DEVICE_SELECT for the whisper subprocess only -- exporting it
    # process-wide would drag the GTK UI onto the discrete card too. "auto"
    # detects a discrete GPU from sysfs and prefers it; Vulkan otherwise
    # enumerates the integrated one first, which on a hybrid laptop is 2.5x
    # slower than realtime. "" disables the override, or set a
    # `vendor:device` by hand.
    "gpu_select": "auto",
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
    # How many silent takes in a row before saying so out loud. Pressing the
    # key and then deciding not to speak is ordinary and must stay quiet -- the
    # indicator's own flat line already reports it, and a notification for that
    # is pure noise. A microphone that is muted, unplugged or pinned to a dead
    # input produces silence *every* time, which is what this actually catches.
    # 0 disables the notification entirely.
    "silent_notice_after": 3,
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
    # auto | paste | wtype | ydotool | clipboard
    # auto tries paste first: wtype and ydotool type character by character, so
    # a minute of speech crawls onto the screen one keystroke at a time, while
    # a paste is one event whatever the length. The clipboard is borrowed for
    # it and handed back a moment later.
    "inject": "auto",
    # bottom-right | bottom-left | top-right | top-left | bottom-center
    "orb_position": "bottom-right",
    "orb_margin": 28,
    # Take colours from a desktop-generated Material You palette when one
    # exists. Off by default: the app ships its own dark theme and should look
    # the same everywhere. ~/.config/nabria/palette.json overrides either way.
    "follow_desktop_palette": False,
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
