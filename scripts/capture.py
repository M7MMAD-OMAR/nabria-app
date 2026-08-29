#!/usr/bin/env python3
"""One language's screenshots, taken inside a throwaway profile.

Run by `screenshots.py`, once per language, and not useful on its own -- it
expects `XDG_CONFIG_HOME` and friends to point at a profile that was created
for it. It is a separate process because `config.py` resolves every path at
import time, so a single process cannot be pointed at a second profile
afterwards; and a separate *file* because 175 lines held in a string constant
are invisible to `compileall`, to the editor and to every other checker.
"""

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

from nabria import config, i18n, models, settings_window, wizard  # noqa: E402

# Nothing of this machine's reaches a published picture.
#
# The throwaway profile covers the config, the models and the history, because
# those are addressed through XDG variables. The model page's search is not: it
# looks in the *real* home directory for models installed by anything else, and
# the machine that runs this script has several -- so the README would carry a
# photograph of the author's own directory names. A new install is what is
# being photographed, and on one of those this list is empty.
models.search_roots = list

language, out_dir, shots = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
i18n.apply(language)

settings = config.load()

# Breathing room under the last line of a page, in the window's own pixels.
PADDING = 24
failed = False


def hyprctl(*arguments):
    return json.loads(subprocess.run(
        ["hyprctl", "-j", *arguments], capture_output=True, text=True, check=True
    ).stdout)


def visible_workspaces():
    """The workspace shown on each monitor right now."""
    return {monitor["activeWorkspace"]["id"] for monitor in hyprctl("monitors")}


def geometry_of(title):
    """`x,y WxH` for grim, asked of the compositor rather than guessed.

    What is on screen has a scale factor and whatever the tiling rules did to
    it, so a rectangle derived from the size the window asked for is merely
    close -- and a screenshot that is merely close has a slice of somebody's
    desktop down one edge.

    The window also has to be on a workspace that is actually being displayed.
    grim copies the *output*, so a window sitting on a hidden workspace has a
    perfectly good geometry pointing at whatever is on screen instead: one run
    of this wrote six screenshots of a wallpaper, with no error anywhere. A
    missing geometry is reported and fails the run; a picture of the wrong
    thing is not noticed until it is published.
    """
    try:
        clients = hyprctl("clients")
        shown = visible_workspaces()
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None
    for client in clients:
        if client.get("title") != title or not client.get("mapped"):
            continue
        if client.get("workspace", {}).get("id") not in shown:
            print("  ! " + title + ": on a workspace that is not being shown",
                  file=sys.stderr)
            return None
        (x, y), (w, h) = client["at"], client["size"]
        return f"{x},{y} {w}x{h}"
    return None


def settled_geometry(title, tries=25):
    """The same rectangle twice running, which is not the same as a rectangle.

    A floating window is animated into place, and `hyprctl clients` answers
    with wherever it is at the instant it is asked -- so a rectangle read
    mid-flight is a real answer to the wrong question. grim then copies that
    part of the output, and what lands in the file is the window with a strip
    of whatever it was sliding across down one edge.

    This is not hypothetical. It is what published the Arabic welcome shot with
    a terminal showing through the right-hand quarter of it: right size, right
    window, right workspace, wrong place -- and nothing in the run said so.
    Only the window asking not to be resizable made it float, and only floating
    made it move.
    """
    previous = None
    for _ in range(tries):
        current = geometry_of(title)
        # Not there *yet* is not the same as not there. GTK considers the
        # window presented before the compositor has a client for it, so the
        # first look can come back empty for a window that appears a frame
        # later -- which showed up as the settings shots failing in the second
        # language and not the first, the run order being the only difference
        # between them.
        if current is not None:
            if current == previous:
                return current
            previous = current
        time.sleep(0.15)
    return previous


def looks_like_a_window(path):
    """Whether the capture is the window, or whatever was covering it.

    grim copies the output, not the window, so anything drawn on top is what
    lands in the file -- and the geometry, the size and `hyprctl clients` all
    stay correct while it happens. A session lock produced twelve photographs
    of a lock screen this way, with no error anywhere; a full-screen window or
    a break reminder would do the same. Silence is the whole problem: these
    files are published.

    The test is for hard edges. Text, borders and widget outlines put adjacent
    pixels tens of levels apart; a wallpaper, blurred or not, is smooth almost
    everywhere. An earlier version asked whether the corners were flat instead,
    and a dark gradient wallpaper passed it -- flatness is what the two have in
    common, not what separates them.

    Deliberately not "is it dark" or "is it the theme colour": the wizard is
    dark, the settings window follows the desktop, and the lock screen is
    whichever wallpaper is up. Only the edges tell them apart.
    """
    image = GdkPixbuf.Pixbuf.new_from_file(path)
    width, height = image.get_width(), image.get_height()
    stride, channels = image.get_rowstride(), image.get_n_channels()
    pixels = image.get_pixels()

    sampled = edges = 0
    for y in range(0, height, 3):
        row = y * stride
        for x in range(0, width - 2, 3):
            here, right = row + x * channels, row + (x + 2) * channels
            sampled += 1
            if abs(pixels[here] - pixels[right]) > 60:
                edges += 1
    # 0.2%: measured at 1.5-6% across the twelve screenshots this produces,
    # and at 0.00% on the lock screen that prompted the check. Two orders of
    # magnitude of margin either way, so the threshold is not delicate.
    return sampled > 0 and edges / sampled > 0.002


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
    content = (
        window.stack.get_visible_child() if kind == "wizard"
        else window.notebook.get_nth_page(int(page))
    )
    natural = content.measure(Gtk.Orientation.VERTICAL, window.get_width())[1]
    # Where the page starts, asked of GTK rather than added up from the pieces
    # above it. Adding them up meant naming them -- the stack's margins, the
    # notebook's tab strip -- and the sum went stale the moment the settings
    # window grew a row above its notebook: the picture came out with the new
    # row cropped away and nothing said so.
    found, bounds = content.compute_bounds(window)
    return (bounds.origin.y if found else 0) + natural + PADDING


def hide_pointer():
    """Move the pointer to the bottom of the screen, out of every crop.

    Hyprland composites the cursor into the frame grim copies, so without this
    a stray arrow sits in the middle of each picture. Relative rather than
    absolute: the move clamps at the screen edge on any monitor at any scale
    without doing the arithmetic. Best effort -- a missing ydotool costs a
    cursor in the pictures, not a run.
    """
    subprocess.run(["ydotool", "mousemove", "-x", "4000", "-y", "4000"],
                   capture_output=True)


def settle(ready=None, tries=40):
    """Let the window actually reach the screen, and the page reach the window.

    present() only asks. Turning the main loop alone is not enough -- the
    compositor has its own frame to draw -- so this alternates between the two
    rather than sleeping once and hoping.

    `ready` is the thing being waited *for*, rather than a length of time. A
    fixed wait was enough on an idle machine and not on a busy one: with a
    container build and a large download running, the stack's slide animation
    had not finished when grim fired and the shortcut page was photographed
    still showing the welcome page. Correct size, correct title, wrong
    picture, no error.
    """
    for _ in range(tries):
        while GLib.MainContext.default().iteration(False):
            pass
        time.sleep(0.1)
        if ready is None or ready():
            # One more turn after the condition holds: it goes true when GTK
            # has decided, which is before the frame carrying it is drawn.
            time.sleep(0.2)
            while GLib.MainContext.default().iteration(False):
                pass
            return True
    return False


def shoot(app):
    """Every shot in one activation. Wrapped, because an exception raised in a
    GTK signal handler is printed and swallowed -- the main loop keeps running
    with no windows left to make, so the process hangs instead of failing."""
    global failed
    try:
        capture_all(app)
    except Exception:
        traceback.print_exc()
        failed = True
    app.quit()


def capture_all(app):
    global failed
    hide_pointer()
    for stem, kind, page in shots:
        if kind == "wizard":
            window = wizard.Wizard(app, dict(settings), lambda: None)
            window.present()
            window.stack.set_visible_child_name(page)
            stack = window.stack
            def showing(stack=stack, page=page):
                return (stack.get_visible_child_name() == page
                        and not stack.get_transition_running())
        else:
            window = settings_window.SettingsWindow(
                app, dict(settings), lambda *a: None,
                # With the dictation button, because it is part of the window
                # a user opens. Photographing the version built without a
                # daemon would publish a picture of a window nobody has.
                on_toggle=lambda: None, state=lambda: "idle",
            )
            window.present()
            notebook = window.notebook
            notebook.set_current_page(int(page))
            def showing(notebook=notebook, page=page):
                return notebook.get_current_page() == int(page)

        # Unique, and set after the window is built so it overrides whatever
        # the window titled itself. Every wizard page is one window class with
        # one title, so looking the window up by its own title found whichever
        # matching window the compositor still had -- usually the previous
        # shot's, which had been destroyed a moment earlier and not yet
        # unmapped. Four of the six pictures were of the wrong page.
        title = "Nabria capture: " + stem
        window.set_title(title)
        if not settle(showing):
            print("  ! " + stem + ": the page never came up", file=sys.stderr)
            failed = True
            window.destroy()
            continue
        geometry = settled_geometry(title)
        if geometry is None:
            print("  ! " + stem + ": the window never appeared", file=sys.stderr)
            failed = True
            window.destroy()
            continue

        path = os.path.join(out_dir, stem + ".png")
        subprocess.run(["grim", "-g", geometry, path], check=True)
        if not looks_like_a_window(path):
            print("  ! " + stem + ": something is covering the screen — a lock "
                  "screen, a full-screen window or a break reminder. Unlock or "
                  "dismiss it and run this again.", file=sys.stderr)
            os.unlink(path)
            failed = True
            window.destroy()
            continue
        # Only a window that is bigger than its content needs cutting down.
        #
        # `resizable` is exactly that question. The setup window asks not to be
        # resizable, so the compositor floats it at its natural height and the
        # frame already *is* the crop -- running the trim over it anyway shaved
        # a few pixels off the bottom, which took the rounded corners with them
        # and left a different bottom edge on every page. The settings window
        # is resizable and gets tiled into whatever column it lands in, which
        # is the case this was written for.
        if window.get_resizable():
            crop_below(path, page_height(window, kind, page), window.get_height())
        print("  " + stem + ".png")
        window.destroy()


# Not config.APP_ID: that id belongs to the running daemon, and a second
# application claiming it hands its activation over and exits without ever
# opening a window of its own.
app = Gtk.Application(application_id="com.sbarah.NabriaShots")
app.connect("activate", shoot)
app.run(sys.argv[:1])
sys.exit(1 if failed else 0)