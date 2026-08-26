"""The on-screen indicator, drawn as a real Wayland layer-shell surface.

This is the whole reason the tool exists rather than a settings change in
OpenWhispr: an Electron overlay on Hyprland is an XWayland toplevel, so the
compositor stacks it against ordinary windows and it vanishes behind anything
fullscreen. A layer surface on the OVERLAY layer sits above every window by
protocol, and with keyboard interactivity set to NONE it can never steal focus
from the window being dictated into.

It is deliberately tiny -- one small disc that says listening, thinking, or
failed. Anything with words in it belongs in a desktop notification, not
floating over the user's work.

gtk4-layer-shell must be loaded before libwayland-client. Python cannot control
link order, so scripts/run-fedora.sh sets LD_PRELOAD; without it
Gtk4LayerShell.is_supported() returns False and the window silently degrades
into an ordinary toplevel.
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

from . import theme

WINDOW_SIZE = 60
DISC_RADIUS = 19

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

        self.window = Gtk.ApplicationWindow(application=application)
        self.window.add_css_class("dictate")
        self.window.set_default_size(WINDOW_SIZE, WINDOW_SIZE)

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
        self.area.set_content_width(WINDOW_SIZE)
        self.area.set_content_height(WINDOW_SIZE)
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
        x, y = width / 2, height / 2
        accent = self.colours[ACCENTS.get(self.state, "primary")]

        if self.state == "recording" and self.level > 0.02:
            # A halo that breathes with the microphone level: the one piece of
            # feedback that separates "listening" from "the mic is dead".
            context.set_source_rgba(*accent, 0.10 + 0.20 * self.level)
            context.arc(x, y, DISC_RADIUS + 2 + 7 * self.level, 0, 2 * math.pi)
            context.fill()

        context.set_source_rgba(*self.colours["surface_container"], 0.94)
        context.arc(x, y, DISC_RADIUS, 0, 2 * math.pi)
        context.fill()

        if self.state in {"working", "loading"}:
            context.set_line_width(2.0)
            context.set_source_rgba(*accent, 0.9)
            context.arc(x, y, DISC_RADIUS - 1, self.phase, self.phase + 1.6)
            context.stroke()
        else:
            context.set_line_width(1.0)
            context.set_source_rgba(*self.colours["outline_variant"], 0.9)
            context.arc(x, y, DISC_RADIUS - 0.5, 0, 2 * math.pi)
            context.stroke()

        context.set_source_rgba(*accent, 0.95)
        if self.state == "done":
            self._draw_check(context, x, y)
        elif self.state == "error":
            self._draw_bang(context, x, y)
        else:
            self._draw_microphone(context, x, y)

    def _draw_microphone(self, context, x: float, y: float) -> None:
        context.set_line_width(1.8)
        context.arc(x, y - 4.5, 4.0, math.pi, 2 * math.pi)
        context.line_to(x + 4.0, y - 0.5)
        context.arc(x, y - 0.5, 4.0, 0, math.pi)
        context.close_path()
        context.fill()
        context.arc(x, y - 2.0, 7.5, 0.22 * math.pi, 0.78 * math.pi)
        context.stroke()
        context.move_to(x, y + 5.5)
        context.line_to(x, y + 8.5)
        context.stroke()

    def _draw_check(self, context, x: float, y: float) -> None:
        context.set_line_width(2.2)
        context.set_line_cap(1)  # cairo.LINE_CAP_ROUND
        context.move_to(x - 6, y)
        context.line_to(x - 2, y + 4.5)
        context.line_to(x + 6.5, y - 5)
        context.stroke()

    def _draw_bang(self, context, x: float, y: float) -> None:
        context.set_line_width(2.2)
        context.set_line_cap(1)
        context.move_to(x, y - 6.5)
        context.line_to(x, y + 1.5)
        context.stroke()
        context.arc(x, y + 6, 1.3, 0, 2 * math.pi)
        context.fill()

    def _tick(self, _widget, _clock) -> bool:
        if self.visible and self.state in {"working", "loading"}:
            self.phase = (self.phase + 0.11) % (2 * math.pi)
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

    def _cancel_hide(self) -> None:
        if self._hide_source:
            GLib.source_remove(self._hide_source)
            self._hide_source = 0
