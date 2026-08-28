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
DEFAULT_OUT = PROJECT_DIR / "docs" / "screenshots"

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

# The models a fresh profile has not downloaded. Created empty, so the settings
# window's picker has something in it: a picture of an empty picker is accurate
# for a profile with nothing fetched and misleading as a picture of the
# application. Nothing reads their contents on this path.
PLACEHOLDER_MODELS = ("ggml-base.bin", "ggml-small.bin", "ggml-large-v3-turbo.bin")

CAPTURE = '''\
"""Run inside a throwaway profile: build each window, photograph it, move on."""

import json
import os
import subprocess
import sys
import time
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

from nabria import config, i18n, settings_window, wizard  # noqa: E402

language, out_dir, shots = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
if i18n.use(language) in i18n.RTL:
    Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)

settings = config.load()
failures = []

# Breathing room under the last line of a page, in the window's own pixels.
PADDING = 24


def geometry_of(title):
    """`x,y WxH` for grim, asked of the compositor rather than guessed.

    What is on screen has a scale factor and whatever the tiling rules did to
    it, so a rectangle derived from the size the window asked for is merely
    close -- and a screenshot that is merely close has a slice of somebody's
    desktop down one edge.
    """
    try:
        clients = json.loads(subprocess.run(
            ["hyprctl", "-j", "clients"], capture_output=True, text=True, check=True
        ).stdout)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None
    for client in clients:
        if client.get("title") == title and client.get("mapped"):
            (x, y), (w, h) = client["at"], client["size"]
            return f"{x},{y} {w}x{h}"
    return None


def crop_below(path, wanted, window_height):
    """Cut the capture off `wanted` window-pixels down, throwing the rest away.

    A tiling compositor gives the window the whole column it is in, so a
    capture of the whole thing is a few lines of text above a screenful of
    empty background. Asking the compositor to float and resize it instead
    means a dispatch syntax that changes between Hyprland releases, which is
    not something a screenshot script should carry.

    `wanted` is in the window's own pixels and `path` is in the screen's, so
    the scale factor is recovered by dividing one height by the other rather
    than being read from anywhere. A fraction of the capture would not do: the
    window's height depends on what else happens to be tiled beside it, so the
    same fraction is a different amount of page on every run.

    GdkPixbuf rather than an image library: it arrives with GTK, which is
    already required to open the window being photographed.
    """
    image = GdkPixbuf.Pixbuf.new_from_file(path)
    scale = image.get_height() / max(window_height, 1)
    height = min(max(round(wanted * scale), 1), image.get_height())
    if height == image.get_height():
        return  # the page fills the window; there is nothing to trim
    image.new_subpixbuf(0, 0, image.get_width(), height).savev(path, "png", [], [])


def page_height(window, kind, page):
    """How tall the visible page wants to be, in the window's own pixels.

    Asked of GTK rather than written down per shot. The pages are not equally
    full, and Arabic wraps at different places than English -- so a number
    chosen by eye is a number that is wrong for one of the two languages, and
    wrong again the next time a sentence is edited.
    """
    if kind == "wizard":
        content = window.stack.get_visible_child()
        chrome = window.stack.get_margin_top() + window.stack.get_margin_bottom()
    else:
        notebook = window.get_child()
        content = notebook.get_nth_page(int(page))
        # The tab strip, which is inside the notebook and above the page.
        chrome = notebook.get_height() - content.get_height()
    natural = content.measure(Gtk.Orientation.VERTICAL, window.get_width())[1]
    return natural + chrome + PADDING


def hide_pointer():
    """Move the pointer to the bottom of the screen, out of every crop.

    Hyprland composites the cursor into the frame grim copies, so without this
    a stray arrow sits in the middle of each picture. Relative rather than
    absolute: a relative move clamps at the screen edge, which is the wanted
    result on any monitor, at any scale, without doing the arithmetic. Best
    effort -- a missing ydotool costs a cursor in the screenshots, not a run.
    """
    subprocess.run(["ydotool", "mousemove", "-x", "0", "-y", "4000"],
                   capture_output=True)


def settle():
    """Let the window actually reach the screen.

    present() only asks. Turning the main loop alone is not enough -- the
    compositor has its own frame to draw -- so this alternates between the two
    rather than sleeping once and hoping.
    """
    for _ in range(3):
        while GLib.MainContext.default().iteration(False):
            pass
        time.sleep(0.25)


def shoot(app):
    """Every shot in one activation. Wrapped, because an exception raised in a
    GTK signal handler is printed and swallowed -- the main loop keeps running
    with no windows left to make, so the process hangs instead of failing."""
    try:
        capture_all(app)
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    app.quit()


def capture_all(app):
    hide_pointer()
    for stem, kind, page in shots:
        if kind == "wizard":
            window = wizard.Wizard(app, dict(settings), lambda: None)
            window.present()
            window.stack.set_visible_child_name(page)
        else:
            window = settings_window.SettingsWindow(app, dict(settings), lambda *a: None)
            window.present()
            window.get_child().set_current_page(int(page))

        # Unique, and set after the window is built so it overrides whatever
        # the window titled itself. Every wizard page is one window class with
        # one title, so looking the window up by its own title found whichever
        # matching window the compositor still had -- usually the previous
        # shot's, which had been destroyed a moment earlier and not yet
        # unmapped. Four of the six pictures were of the wrong page.
        window.set_title("Nabria capture: " + stem)
        settle()
        geometry = geometry_of(window.get_title())
        if geometry is None:
            print("  ! " + stem + ": the window never appeared", file=sys.stderr)
            failures.append(stem)
            window.destroy()
            continue

        path = os.path.join(out_dir, stem + ".png")
        subprocess.run(["grim", "-g", geometry, path], check=True)
        crop_below(path, page_height(window, kind, page), window.get_height())
        print("  " + stem + ".png")
        window.destroy()


# Not config.APP_ID: that id belongs to the running daemon, and a second
# application claiming it hands its activation over and exits without ever
# opening a window of its own.
app = Gtk.Application(application_id="com.sbarah.NabriaShots")
app.connect("activate", shoot)
app.run(sys.argv[:1])
sys.exit(1 if failures else 0)
'''


def die(message: str) -> None:
    print(f"screenshots: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", action="append", choices=["en", "ar"],
                        help="repeatable; both by default")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not os.environ.get("WAYLAND_DISPLAY"):
        die("no Wayland session — there is nothing on screen to capture")
    for tool in ("grim", "hyprctl"):
        if not shutil.which(tool):
            die(f"{tool} is not installed")

    languages = args.language or ["en", "ar"]

    with tempfile.TemporaryDirectory(prefix="nabria-shots.") as workspace:
        work = Path(workspace)
        # Written out rather than passed with `python -c`, so a traceback in it
        # names real line numbers in a file that still exists to be read.
        script = work / "capture.py"
        script.write_text(CAPTURE, encoding="utf-8")

        for language in languages:
            profile = work / language
            models_dir = profile / "data" / "nabria" / "models"
            models_dir.mkdir(parents=True)
            for model in PLACEHOLDER_MODELS:
                (models_dir / model).touch()

            out = args.out / language
            out.mkdir(parents=True, exist_ok=True)
            print(f"\n{language}:")
            result = subprocess.run(
                [sys.executable, str(script), language, str(out), json.dumps(SHOTS)],
                env={
                    **os.environ,
                    "XDG_CONFIG_HOME": str(profile / "config"),
                    "XDG_DATA_HOME": str(profile / "data"),
                    "XDG_STATE_HOME": str(profile / "state"),
                    "PYTHONPATH": str(PROJECT_DIR / "src"),
                    # The wizard preselects the spoken language from the
                    # interface language, which follows this when the setting
                    # is `auto`. A shot of the Arabic build with English
                    # preselected would be a picture of a bug.
                    "LANG": "ar_SY.UTF-8" if language == "ar" else "en_GB.UTF-8",
                },
            )
            if result.returncode != 0:
                die(f"capture failed for {language}")

    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
