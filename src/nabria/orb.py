"""The on-screen indicator, drawn as a real Wayland layer-shell surface.

This is the whole reason the tool exists rather than a settings change in
OpenWhispr: an Electron overlay on Hyprland is an XWayland toplevel, so the
compositor stacks it against ordinary windows and it vanishes behind anything
fullscreen. A layer surface on the OVERLAY layer sits above every window by
protocol, and with keyboard interactivity set to NONE it can never steal focus
from the window being dictated into.

It is deliberately wordless and small -- five marks in a pill, centred under the
screen, saying listening, thinking, or failed and nothing else. Anything with
words in it belongs in a desktop notification, not floating over the user's
work.

Five marks carry every state, so it always reads as one object changing rather
than as a series of different pictures:

    recording     the marks rise and fall with the live level, tallest in the
                  middle. A microphone that has gone dead therefore sits as a
                  flat row of dots instead of animating regardless, which is
                  the difference between an indicator and a decoration.
    transcribing  a wave lifts each mark in turn. Movement, not brightness:
                  at this size a brightness cycle read as the resting row of
                  dots, the one state it must never be confused with.
    done, failed  still, and told apart by shape -- a whole line against one
                  broken in the middle. Colour cannot carry it, because a
                  Material You palette hands out an error and a primary that
                  are the same salmon.

gtk4-layer-shell must be loaded before libwayland-client. Python cannot control
link order, so scripts/run.sh sets LD_PRELOAD; without it
Gtk4LayerShell.is_supported() returns False and the window silently degrades
into an ordinary toplevel.
"""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

# Layer shell is what makes the indicator sit above everything, but it is not
# universally available: GNOME does not implement the protocol at all, and the
# typelib is a separate package everywhere. Importing it unconditionally meant
# a missing package stopped the whole application from starting -- turning a
# degraded indicator into no dictation at all. See `_init_surface`.
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
except (ImportError, ValueError):
    LayerShell = None

from . import config, theme  # noqa: E402

WINDOW_W = 76
WINDOW_H = 30
PILL_INSET = 3  # keeps the pill's shadow-free edge off the surface boundary
PILL_RADIUS = 13

# Five bars, not a scrolling history of thirty-four. At this size a history
# would be a texture rather than a reading, and the one thing it has to say --
# is anything being heard -- is said just as well by five bars that move.
BAR_COUNT = 5
BAR_WIDTH = 3.0
BAR_MIN = 3.0  # a row of dots: the resting shape, and the shape of silence
BAR_MAX = 16.0
BAR_GAP = 8.0
# Tallest in the middle, so the shape reads as a voice rather than a bar chart.
BAR_ENVELOPE = (0.5, 0.82, 1.0, 0.82, 0.5)

# Attack fast, release slow. A level arrives only every LEVEL_POLL_MS (50 ms);
# easing between them at frame rate is what makes it feel attached to the
# voice instead of stepping. Faster rise than fall because speech onsets are
# what the eye is looking for, and a slow fall reads as a tail rather than a
# flicker.
LEVEL_ATTACK = 0.35
LEVEL_RELEASE = 0.12

# How far a dot rises at the crest of the transcribing wave.
SWEEP_LIFT = 5.0

# Clearing background-color alone is not enough: the GTK theme paints the
# window through background-image as well, which draws straight over a
# transparent colour and leaves an opaque rectangle around the orb.
STYLE = """
window.nabria {
  background-color: transparent;
  background-image: none;
  box-shadow: none;
  border: none;
}
"""

# Named rather than holding LayerShell.Edge values, because those cannot be
# looked up when the typelib is absent -- and this table is read at import.
ANCHORS = {
    "bottom-right": ("BOTTOM", "RIGHT"),
    "bottom-left": ("BOTTOM", "LEFT"),
    "top-right": ("TOP", "RIGHT"),
    "top-left": ("TOP", "LEFT"),
    "bottom-center": ("BOTTOM", None),
}


def layer_shell_available() -> bool:
    """Whether the indicator can be put on the overlay layer.

    Two ways to fail and they look identical from the outside. The typelib may
    not be installed, and `is_supported()` returns False when the compositor
    does not implement the protocol -- or when the library was loaded after
    libwayland-client, which is what LD_PRELOAD in run.sh exists to prevent.
    """
    return LayerShell is not None and LayerShell.is_supported()

# state -> palette key for the glyph and any ring
ACCENTS = {
    "loading": "tertiary",
    "recording": "primary",
    "working": "tertiary",
    "done": "primary",
    "error": "error",
}


class Orb:
    def __init__(self, application: Gtk.Application, settings: dict):
        self.settings = settings
        self.colours = theme.load(
            config.CONFIG_DIR, bool(settings.get("follow_desktop_palette"))
        )
        self.state = "recording"
        self.level = 0.0  # target, straight off the microphone
        self.shown = 0.0  # what is drawn, easing toward the target
        self.phase = 0.0
        self.visible = False
        self._hide_source = 0

        self.window = Gtk.ApplicationWindow(application=application)
        self.window.add_css_class("nabria")
        self.window.set_default_size(WINDOW_W, WINDOW_H)

        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(STYLE)
        else:
            provider.load_from_data(STYLE.encode("utf-8"))
        # Two things matter here. The default display, not window.get_display():
        # an unrealised window has no display yet and the provider would attach
        # to nothing. And a priority above USER (800): the desktop theme writes
        # `window { background: @window_bg_color; }` into ~/.config/gtk-4.0/gtk.css,
        # which loads at USER and would otherwise paint an opaque rectangle
        # around the orb no matter what this rule says.
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 100
        )

        self.area = Gtk.DrawingArea()
        self.area.set_content_width(WINDOW_W)
        self.area.set_content_height(WINDOW_H)
        self.area.set_draw_func(self._draw)
        self.window.set_child(self.area)

        self.layered = layer_shell_available()
        self._init_surface()
        self.window.add_tick_callback(self._tick)

    def _init_surface(self) -> None:
        if self.layered:
            self._init_layer_shell()
        else:
            self._init_plain_window()

    def _init_plain_window(self) -> None:
        """The indicator as an ordinary window, where layer shell is missing.

        Worse, and honestly so. Wayland gives a client no way to place its own
        window or to ask to stay on top, so this lands wherever the compositor
        decides and anything fullscreen will cover it -- exactly the failure
        that made this tool worth writing. It is still much better than the
        alternative, which is that dictation does not run at all: the words
        still land in the document, and the transcript is still in the history.

        Undecorated and unresizable, so at least it reads as an indicator
        rather than as an application someone forgot to close.
        """
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_default_size(WINDOW_W, WINDOW_H)

    def _init_layer_shell(self) -> None:
        window = self.window
        LayerShell.init_for_window(window)
        LayerShell.set_namespace(window, "nabria")
        LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
        # NONE means the compositor never routes keyboard input here, so the
        # window being dictated into keeps focus the whole time.
        LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.NONE)
        # Zero exclusive zone: the orb floats over other windows instead of
        # reserving space and reflowing the layout.
        LayerShell.set_exclusive_zone(window, 0)

        margin = int(self.settings.get("orb_margin", 28))
        vertical, horizontal = ANCHORS.get(
            self.settings.get("orb_position", "bottom-right"), ANCHORS["bottom-right"]
        )
        LayerShell.set_anchor(window, getattr(LayerShell.Edge, vertical), True)
        LayerShell.set_margin(window, getattr(LayerShell.Edge, vertical), margin)
        if horizontal is not None:
            LayerShell.set_anchor(window, getattr(LayerShell.Edge, horizontal), True)
            LayerShell.set_margin(window, getattr(LayerShell.Edge, horizontal), margin)

    # -- drawing -----------------------------------------------------------

    def _draw(self, _area, context, width, height) -> None:
        accent = self.colours[ACCENTS.get(self.state, "primary")]
        centre_y = height / 2

        self._rounded_rect(
            context,
            PILL_INSET,
            PILL_INSET,
            width - 2 * PILL_INSET,
            height - 2 * PILL_INSET,
            PILL_RADIUS,
        )
        context.set_source_rgba(*self.colours["surface_container"], 0.92)
        context.fill_preserve()
        context.set_line_width(1.0)
        context.set_source_rgba(*self.colours["outline_variant"], 0.55)
        context.stroke()

        centre_x = width / 2
        span = (BAR_COUNT - 1) * BAR_GAP
        left = centre_x - span / 2
        context.set_line_width(BAR_WIDTH)
        context.set_line_cap(cairo.LINE_CAP_ROUND)

        if self.state == "recording":
            self._draw_waveform(context, centre_x, centre_y, accent)
        elif self.state in {"working", "loading"}:
            self._draw_sweep(context, centre_x, centre_y, accent)
        else:
            # done and error are momentary and still: motion here would read as
            # "still busy". They differ by shape, not colour -- this palette's
            # error (#ffb4ab) and primary (#ffb5a0) are all but the same salmon,
            # so a failed dictation drawn only in its own colour would look
            # exactly like a successful one. Whole line means it worked; a line
            # broken in the middle means it did not.
            context.set_source_rgba(*accent, 0.95)
            if self.state == "error":
                gap = span * 0.12
                context.move_to(left, centre_y)
                context.line_to(left + (span - gap) / 2, centre_y)
                context.stroke()
                context.move_to(left + (span + gap) / 2, centre_y)
                context.line_to(left + span, centre_y)
            else:
                context.move_to(left, centre_y)
                context.line_to(left + span, centre_y)
            context.stroke()

    def _draw_waveform(self, context, centre_x, centre_y, accent) -> None:
        # Every bar is driven by the one current level through a fixed
        # envelope. Nothing is remembered, so there is no history to read and
        # nothing to get out of step with the voice.
        context.set_source_rgba(*accent, 0.95)
        offset = (BAR_COUNT - 1) / 2
        for index, weight in enumerate(BAR_ENVELOPE):
            height = BAR_MIN + (BAR_MAX - BAR_MIN) * self.shown * weight
            x = centre_x + (index - offset) * BAR_GAP
            context.move_to(x, centre_y - height / 2)
            context.line_to(x, centre_y + height / 2)
            context.stroke()

    def _draw_sweep(self, context, centre_x, centre_y, accent) -> None:
        # The same five positions, now lifting in turn: a wave travelling
        # along them. Brightness alone was not enough -- at this size it read
        # as the resting row of dots, which is the one state it must never be
        # confused with. Movement carries it instead, and reusing the same
        # five marks keeps it the same object doing something else.
        context.set_source_rgba(*accent, 0.9)
        offset = (BAR_COUNT - 1) / 2
        for index in range(BAR_COUNT):
            lift = max(0.0, math.sin(self.phase - index * 0.8)) ** 2
            x = centre_x + (index - offset) * BAR_GAP
            y = centre_y - lift * SWEEP_LIFT
            context.move_to(x, y - BAR_MIN / 2)
            context.line_to(x, y + BAR_MIN / 2)
            context.stroke()

    @staticmethod
    def _rounded_rect(context, x, y, width, height, radius) -> None:
        context.new_sub_path()
        context.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
        context.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
        context.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
        context.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
        context.close_path()

    def _tick(self, _widget, _clock) -> bool:
        if not self.visible:
            return GLib.SOURCE_CONTINUE
        if self.state in {"working", "loading"}:
            self.phase = (self.phase + 0.055) % (2 * math.pi)
            self.area.queue_draw()
        elif self.state == "recording":
            # Levels land only every 50 ms. Easing toward the newest one at
            # frame rate is the whole difference between bars that step and
            # bars that feel attached to the voice.
            rate = LEVEL_ATTACK if self.level > self.shown else LEVEL_RELEASE
            moved = (self.level - self.shown) * rate
            if abs(moved) > 0.0005:
                self.shown += moved
                self.area.queue_draw()
        return GLib.SOURCE_CONTINUE

    # -- state -------------------------------------------------------------

    def show(self, state: str) -> None:
        self._cancel_hide()
        self.state = state
        if not self.visible:
            self.window.present()
            self.visible = True
        self.area.queue_draw()

    def set_level(self, dbfs: float) -> None:
        # -50 dBFS is a quiet room, -5 is shouting; anything outside clamps.
        self.level = max(0.0, min(1.0, (dbfs + 50.0) / 45.0))

    def flash(self, state: str, seconds: float = 1.1) -> None:
        """Show a terminal state, then hide on its own."""
        self.show(state)
        self._hide_source = GLib.timeout_add(
            int(seconds * 1000), lambda: (self.hide(), GLib.SOURCE_REMOVE)[1]
        )

    def hide(self) -> None:
        self._cancel_hide()
        if self.visible:
            self.window.set_visible(False)
            self.visible = False
        # Both cleared, not left to decay: the next dictation opens at rest
        # rather than replaying the tail of the previous one.
        self.level = 0.0
        self.shown = 0.0

    def _cancel_hide(self) -> None:
        if self._hide_source:
            GLib.source_remove(self._hide_source)
            self._hide_source = 0
