#!/usr/bin/env python3
"""Capture the README's screenshots, in both languages, from a clean profile.

Run it, do not stage shots by hand: the whole point is that the pictures come
from a profile that has just been created, so what is published is what a new
user actually sees. A screenshot of the running daemon would carry the author's
own microphone names, dictated transcripts and vocabulary prompt into a public
repository, permanently -- `git archive` sweeps the working tree, so it would
be in every release tarball too.

    scripts/screenshots.py                # both languages into docs/screenshots
    scripts/screenshots.py --language ar

Needs a running Wayland compositor and `grim`; there is nothing to capture on a
headless machine, so it says so and stops rather than writing empty files.

Each language runs as its own process. `config.py` resolves every path at
import time, so a profile has to be in the environment before the module is
loaded -- one process cannot be pointed at a second profile afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
# The catalogue and the language list are read from the modules that own them,
# the same way every other list in this project is. A hardcoded copy here does
# not fail when it goes stale -- it quietly writes a misleading picture.
sys.path.insert(0, str(PROJECT_DIR / "src"))
from nabria import i18n, models  # noqa: E402
DEFAULT_OUT = PROJECT_DIR / "docs" / "screenshots"
CAPTURE = PROJECT_DIR / "scripts" / "capture.py"

# Each shot: the file stem, which window to build, and the page to leave
# showing. Wizard pages are named; the settings window's are numbered, because
# a Gtk.Notebook has no names to ask for.
SHOTS = [
    ("welcome", "wizard", "welcome"),
    ("language", "wizard", "language"),
    ("model", "wizard", "model"),
    ("shortcut", "wizard", "shortcut"),
    ("settings-engine", "settings", "0"),
    ("settings-microphone", "settings", "1"),
]

# The locale each language is captured under. The wizard preselects the spoken
# language from the interface language, which follows this when the setting is
# `auto` -- a shot of the Arabic build with English preselected would be a
# picture of a bug.
LOCALES = {"en": "en_GB.UTF-8", "ar": "ar_SY.UTF-8"}


def die(message: str) -> None:
    print(f"screenshots: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", action="append", choices=list(i18n.LANGUAGES),
                        help="repeatable; both by default")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not os.environ.get("WAYLAND_DISPLAY"):
        die("no Wayland session — there is nothing on screen to capture")
    for tool in ("grim", "hyprctl"):
        if not shutil.which(tool):
            die(f"{tool} is not installed")

    languages = args.language or list(i18n.LANGUAGES)

    with tempfile.TemporaryDirectory(prefix="nabria-shots.") as workspace:
        work = Path(workspace)
        for language in languages:
            profile = work / language
            models_dir = profile / "data" / "nabria" / "models"
            models_dir.mkdir(parents=True)
            # Empty placeholders, so the settings window's model picker has
            # something in it: a picture of an empty picker is accurate for a
            # profile with nothing fetched, and misleading as a picture of the
            # application. Nothing reads their contents on this path.
            for model in models.CATALOG.values():
                (models_dir / model.filename).touch()

            out = args.out / language
            out.mkdir(parents=True, exist_ok=True)
            print(f"\n{language}:")
            result = subprocess.run(
                [sys.executable, str(CAPTURE), language, str(out), json.dumps(SHOTS)],
                env={
                    **os.environ,
                    "XDG_CONFIG_HOME": str(profile / "config"),
                    "XDG_DATA_HOME": str(profile / "data"),
                    "XDG_STATE_HOME": str(profile / "state"),
                    "PYTHONPATH": str(PROJECT_DIR / "src"),
                    "LANG": LOCALES[language],
                },
            )
            if result.returncode != 0:
                die(f"capture failed for {language}")

    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
