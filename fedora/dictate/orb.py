"""The on-screen indicator, drawn as a real Wayland layer-shell surface.

This is the whole reason the tool exists rather than a settings change in
OpenWhispr: an Electron overlay on Hyprland is an XWayland toplevel, so the
compositor stacks it against ordinary windows and it vanishes behind anything
fullscreen. A layer surface on the OVERLAY layer sits above every window by
protocol, and with keyboard interactivity set to NONE it can never steal focus
from the window being dictated into.

It is deliberately wordless -- a thin line, centred under the screen, that says
listening, thinking, or failed and nothing else. Anything with words in it
belongs in a desktop notification, not floating over the user's work.

The line is the state. While recording it is a waveform scrolling right to
left, drawn from the levels actually measured off the microphone, so a mic that
has gone dead reads as a flat line rather than as an indicator that looks
identical either way. While transcribing, a bright segment sweeps along a
static line -- a different motion, not a different colour, because the two
states have to be distinguishable at a glance and out of the corner of an eye.

gtk4-layer-shell must be loaded before libwayland-client. Python cannot control
link order, so scripts/run-fedora.sh sets LD_PRELOAD; without it
Gtk4LayerShell.is_supported() returns False and the window silently degrades
into an ordinary toplevel.
"""

from __future__ import annotations

import math
from collections import deque

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

from . import theme

WINDOW_W = 264
WINDOW_H = 44
PILL_INSET = 4  # keeps the pill's shadow-free edge off the surface boundary
PILL_RADIUS = 16

BAR_COUNT = 34
BAR_WIDTH = 3.0
BAR_MIN = 3.0  # the flat line shown when nothing is being heard
BAR_MAX = 22.0

# One level arrives every LEVEL_POLL_MS (50 ms), so the visible waveform spans
# roughly BAR_COUNT * 50 ms -- a little under two seconds of speech. Long
# enough to read as a wave, short enough that it reacts immediately.
LEVELS_KEPT = BAR_COUNT

# Clearing background-color alone is not enough: the GTK theme paints the
# window through background-image as well, which draws straight over a
# transparent colour and leaves an opaque rectangle around the orb.
STYLE = """
window.dictate {
  background-color: transparent;
  background-image: none;
  box-shadow: none;
  border: none;
}
"""

ANCHORS = {
    "bottom-right": (LayerShell.Edge.BOTTOM, LayerShell.Edge.RIGHT),
    "bottom-left": (LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT),
    "top-right": (LayerShell.Edge.TOP, LayerShell.Edge.RIGHT),
    "top-left": (LayerShell.Edge.TOP, LayerShell.Edge.LEFT),
    "bottom-center": (LayerShell.Edge.BOTTOM, None),
}

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
        self.colours = theme.load()
        self.state = "recording"
        self.level = 0.0
        self.phase = 0.0
        self.visible = False
        self._hide_source = 0
        # Pre-filled so the first frame is a flat line rather than a bar
        # growing in from nothing at the left edge.
        self.levels: deque[float] = deque([0.0] * LEVELS_KEPT, maxlen=LEVELS_KEPT)

        self.window = Gtk.ApplicationWindow(application=application)
        self.window.add_css_class("dictate")
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

        self._init_layer_shell()
        self.window.add_tick_callback(self._tick)

    def _init_layer_shell(self) -> None:
        window = self.window
        LayerShell.init_for_window(window)
        LayerShell.set_namespace(window, "dictate")
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
        LayerShell.set_anchor(window, vertical, True)
        LayerShell.set_margin(window, vertical, margin)
        if horizontal is not None:
            LayerShell.set_anchor(window, horizontal, True)
            LayerShell.set_margin(window, horizontal, margin)

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

        span = width - 2 * (PILL_INSET + PILL_RADIUS - 4)
        left = (width - span) / 2
        context.set_line_width(BAR_WIDTH)
        context.set_line_cap(cairo.LINE_CAP_ROUND)

        if self.state == "recording":
            self._draw_waveform(context, left, span, centre_y, accent)
        elif self.state in {"working", "loading"}:
            self._draw_sweep(context, left, span, centre_y, accent)
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

    def _draw_waveform(self, context, left, span, centre_y, accent) -> None:
        step = span / (BAR_COUNT - 1)
        for index, level in enumerate(self.levels):
            height = BAR_MIN + (BAR_MAX - BAR_MIN) * level
            # Newest sample is at the right; the older it is the fainter it
            # gets, which is what makes the wave read as travelling rather
            # than as a row of bars flickering in place.
            age = index / (BAR_COUNT - 1)
            context.set_source_rgba(*accent, 0.30 + 0.65 * age)
            x = left + index * step
            context.move_to(x, centre_y - height / 2)
            context.line_to(x, centre_y + height / 2)
            context.stroke()

    def _draw_sweep(self, context, left, span, centre_y, accent) -> None:
        context.set_source_rgba(*self.colours["outline_variant"], 0.75)
        context.move_to(left, centre_y)
        context.line_to(left + span, centre_y)
        context.stroke()

        # A bright segment travelling the length of the line. Its position is
        # a raised cosine rather than a sawtooth, so it eases at both ends
        # instead of snapping back to the start.
        width = span * 0.28
        travel = (1 - math.cos(self.phase)) / 2
        start = left + (span - width) * travel
        gradient = cairo.LinearGradient(start, centre_y, start + width, centre_y)
        gradient.add_color_stop_rgba(0.0, *accent, 0.0)
        gradient.add_color_stop_rgba(0.5, *accent, 0.95)
        gradient.add_color_stop_rgba(1.0, *accent, 0.0)
        context.set_source(gradient)
        context.move_to(start, centre_y)
        context.line_to(start + width, centre_y)
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
        if self.visible and self.state in {"working", "loading"}:
            self.phase = (self.phase + 0.055) % (2 * math.pi)
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
        if self.state == "recording":
            self.levels.append(self.level)
            self.area.queue_draw()

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
        self.level = 0.0
        # Cleared, not left to decay: the next dictation must start from a flat
        # line rather than replaying the tail of the previous one.
        self.levels = deque([0.0] * LEVELS_KEPT, maxlen=LEVELS_KEPT)

    def _cancel_hide(self) -> None:
        if self._hide_source:
            GLib.source_remove(self._hide_source)
            self._hide_source = 0
