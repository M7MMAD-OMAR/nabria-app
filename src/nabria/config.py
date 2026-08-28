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

# A dialect prompt, offered when Arabic is chosen at setup.
#
# Not a glossary -- these are ordinary Levantine function words, and their job
# is to stop the model pulling spoken dialect toward formal Arabic. Measured
# over one utterance, three runs: a prompt of formal Arabic came out *worse*
# than no prompt at all (هلأ became هلا), and adding these recovered هلأ twice,
# شغال, and a word the formal prompt had split in half. Keep it short; a long
# prompt starts leaking into the transcript.
LEVANTINE_PROMPT = "هلأ، بحكي، شوف، هيك، كتير، منيح، شغال، لهيك، مشان، عنجد، طيب"

# What the wizard offers. Deliberately three, not ninety: whisper knows a great
# many languages, and a list of them is a worse first experience than a choice
# between "the one I speak" and "work it out".
LANGUAGE_PRESETS: dict[str, dict[str, str]] = {
    "ar": {
        "label": "العربية",
        "summary": "Arabic, including spoken dialect. Ships a Levantine prompt.",
        "vocabulary": LEVANTINE_PROMPT,
    },
    "en": {
        "label": "English",
        "summary": "",
        "vocabulary": "",
    },
    "auto": {
        "label": "Work it out",
        "summary": "Detected per phrase. Least accurate, and it can turn room "
                   "noise into confident nonsense in another language.",
        "vocabulary": "",
    },
}

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
    # Set by the setup wizard when it finishes. The wizard used to open only
    # when no model was installed, which meant it never opened at all on the
    # documented path: install.sh downloads a model *before* the daemon first
    # starts, so there was always one there. The language step never ran, and
    # nobody choosing Arabic ever received the dialect prompt that is the whole
    # reason Arabic works well here.
    "setup_done": False,
    "threads": 8,
    # Which device the engine computes on.
    #   auto  a discrete GPU if there is one, otherwise the CPU
    #   cpu   never use a GPU
    #   any   let the engine choose, integrated cards included
    #   10de:2860  a specific `vendor:device` reported by Vulkan
    #
    # "auto" refuses integrated GPUs rather than merely deprioritising them.
    # Measured on 11s of audio with large-v3-turbo: discrete 0.32s, CPU 21.4s,
    # integrated 63.5s followed by a driver crash. An integrated GPU is not a
    # slower accelerator here, it is a worse answer than no accelerator.
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
    # Extra window classes that paste with Ctrl+Shift+V. The shipped list
    # covers the common terminals; anything else -- a terminal nobody here has
    # heard of, or one that reports an unexpected class -- goes here rather
    # than requiring a source edit.
    "terminal_classes": [],
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

    # The default names large-v3-turbo, which is the right model only where
    # there is a discrete GPU -- on a CPU it runs at half realtime. Rather than
    # fail with "model missing", fall back to whatever is installed.
    #
    # Which one is a hardware question and models.py owns it. Asking about the
    # GPU costs a subprocess and this runs on the daemon's startup path, so the
    # answer is left unknown and models.best_installed takes its safe branch --
    # it would rather hand back a smaller model than one this machine might not
    # be able to run faster than speech.
    if not Path(settings["model"]).exists():
        from . import models as model_catalogue

        fallback = model_catalogue.best_installed(MODEL_DIR)
        if fallback is not None:
            settings["model"] = str(fallback)
    return settings


def save(settings: dict[str, Any]) -> None:
    """Persist the whole settings dict, Arabic kept readable rather than escaped."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def models() -> list[Path]:
    """Every model sitting in the model directory.

    Scanned rather than hardcoded: dropping a `.bin` in there is how a new
    model is offered, and a half-finished download leaves an `.aria2` or
    `.part` beside it, which is what keeps it out of the list.
    """
    from . import models as model_catalogue

    return model_catalogue.models_in(MODEL_DIR)


def needs_setup(settings: dict[str, Any]) -> bool:
    """Whether the setup wizard should open.

    Two conditions, and both are needed.

    `setup_done` is the first-run one. Keying only off the model -- which this
    did at first -- meant the wizard never ran on the documented path at all,
    because install.sh downloads a model before the daemon ever starts. The
    language step, and with it the Arabic dialect prompt, went with it.

    The missing-model condition stays as the repair path: a first-run flag on
    its own can get stuck marked done while the app is unusable, which is
    exactly what keying off the model was avoiding.
    """
    return not settings.get("setup_done") or not models()


def write_default_config() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
    return CONFIG_PATH
