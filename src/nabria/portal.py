"""Global hotkeys through org.freedesktop.portal.GlobalShortcuts.

The alternative is what the installer prints: open your compositor's config
file and paste a line. That is the single most likely place for someone who
does not use a terminal to give up, so it is worth some effort to avoid.

The portal is the standard answer and is implemented here by the Hyprland, KDE
and GNOME backends. It is *not* universal, so this is strictly an addition:
when it is missing, or refuses, or is simply not enabled, the hand-bound key
keeps working exactly as before. Nothing in the daemon depends on this
succeeding.

The awkward part is the portal's request pattern. A method call does not return
its result -- it returns an object path for a Request, and the answer arrives
later as a Response signal on that path. The subscription has to be in place
*before* the call, because a fast backend can reply before the call returns.
"""

from __future__ import annotations

import os
import re
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import shortcut

BUS = "org.freedesktop.portal.Desktop"
OBJECT = "/org/freedesktop/portal/desktop"
SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"
REQUEST = "org.freedesktop.portal.Request"

# id -> (description shown in the desktop's shortcut editor, preferred trigger)
# The triggers are suggestions. Every backend is free to ignore them, and KDE
# and GNOME let the user rebind afterwards, which is the point of going through
# the portal rather than owning the key ourselves.
SHORTCUT_DEFINITIONS = (
    (shortcut.TOGGLE, "Start or stop dictating", "CTRL+ALT+q"),
    (shortcut.CANCEL, "Throw away the current take", "CTRL+ALT+SHIFT+q"),
)


class GlobalShortcuts:
    """Binds the dictation keys, if the desktop will let us.

    Every failure path is the same: log why and leave the caller to the
    manually bound key. A missing hotkey is a lesser problem than a daemon
    that will not start because a portal was unhappy.
    """

    def __init__(self, on_activated: Callable[[str], None], log: Callable[[str], None]):
        self.on_activated = on_activated
        self.log = log
        self.bus: Gio.DBusConnection | None = None
        self.session = ""
        self._counter = 0
        self._subscriptions: set[int] = set()

    # -- plumbing ----------------------------------------------------------

    def _sender(self) -> str:
        """Our bus name as the portal spells it inside request object paths."""
        unique = self.bus.get_unique_name() if self.bus else ""
        return re.sub(r"\.", "_", unique.lstrip(":"))

    def _request_path(self, token: str) -> str:
        return f"{OBJECT}/request/{self._sender()}/{token}"

    def _next_token(self, prefix: str) -> str:
        self._counter += 1
        return f"nabria_{prefix}_{self._counter}"

    def _call(self, method: str, signature: str, args: tuple, options: dict, on_response) -> None:
        """Invoke a portal method and route its Response to `on_response`.

        The options dict is built here rather than by the caller, because the
        handle_token has to go into it and it must hold GLib.Variant values
        throughout. Taking a finished Variant and reaching into it does not
        work: `unpack()` converts the values to plain Python, and handing those
        back to a a{sv} constructor fails with "Expected GLib.Variant, but got
        str".

        Subscribing before the call rather than after is not defensive: the
        reply can genuinely arrive first, and then the signal is delivered to
        nobody and the whole thing hangs with no error at all.
        """
        token = self._next_token(method.lower())
        path = self._request_path(token)

        assert self.bus
        subscription = 0

        def handler(_conn, _sender, _path, _iface, _signal, payload):
            self.bus.signal_unsubscribe(subscription)
            self._subscriptions.discard(subscription)
            code, results = payload.unpack()
            if code != 0:
                # 1 is the user cancelling, 2 is anything else. Neither is
                # worth a notification -- they still have the manual key.
                self.log(f"portal {method} declined (code {code})")
                return
            on_response(results)

        subscription = self.bus.signal_subscribe(
            BUS, REQUEST, "Response", path, None, Gio.DBusSignalFlags.NONE, handler
        )
        self._subscriptions.add(subscription)

        parameters = GLib.Variant(
            signature, (*args, {**options, "handle_token": GLib.Variant("s", token)})
        )
        self.bus.call(
            BUS, OBJECT, SHORTCUTS, method, parameters, None,
            Gio.DBusCallFlags.NONE, 10_000, None, self._called,
        )

    def _called(self, source, result) -> None:
        try:
            source.call_finish(result)
        except GLib.Error as error:
            self.log(f"portal call failed: {error.message}")

    # -- lifecycle ---------------------------------------------------------

    def available(self) -> bool:
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as error:
            self.log(f"no session bus: {error.message}")
            return False
        try:
            reply = self.bus.call_sync(
                BUS, OBJECT, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (SHORTCUTS, "version")),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 3_000, None,
            )
        except GLib.Error:
            # No portal, or no GlobalShortcuts interface in it. Ordinary on
            # plenty of desktops; not worth more than a log line.
            return False
        return reply.unpack()[0] >= 1

    def start(self) -> None:
        if not self.available():
            self.log("shortcuts portal unavailable, using manually bound keys")
            return
        self._call(
            "CreateSession", "(a{sv})", (),
            {"session_handle_token": GLib.Variant("s", self._next_token("session"))},
            self._session_created,
        )

    def _session_created(self, results: dict) -> None:
        self.session = results.get("session_handle", "")
        if not self.session:
            self.log("portal returned no session handle")
            return
        assert self.bus
        # Subscribed before binding: on a backend that restores previously
        # bound shortcuts, a key can fire the moment the bind lands.
        self._subscriptions.add(
            self.bus.signal_subscribe(
                BUS, SHORTCUTS, "Activated", OBJECT, None,
                Gio.DBusSignalFlags.NONE, self._on_activated,
            )
        )
        shortcuts = [
            (name, {
                "description": GLib.Variant("s", description),
                "preferred_trigger": GLib.Variant("s", trigger),
            })
            for name, description, trigger in SHORTCUT_DEFINITIONS
        ]
        self._call(
            "BindShortcuts", "(oa(sa{sv})sa{sv})",
            (self.session, shortcuts, ""), {}, self._bound,
        )

    def _bound(self, results: dict) -> None:
        names = [entry[0] for entry in results.get("shortcuts", [])]
        self.log(f"shortcuts bound through the portal: {', '.join(names) or 'none'}")

    def _on_activated(self, _conn, _sender, _path, _iface, _signal, payload) -> None:
        session, shortcut_id, *_ = payload.unpack()
        if session != self.session:
            return
        self.on_activated(shortcut_id)

    def stop(self) -> None:
        if not self.bus:
            return
        for subscription in self._subscriptions:
            self.bus.signal_unsubscribe(subscription)
        self._subscriptions.clear()
        if self.session:
            # Closing the session drops the bindings with it, which is what
            # keeps a restarted daemon from accumulating duplicates.
            try:
                self.bus.call_sync(
                    BUS, self.session, "org.freedesktop.portal.Session", "Close",
                    None, None, Gio.DBusCallFlags.NONE, 2_000, None,
                )
            except GLib.Error:
                pass
            self.session = ""


def enabled() -> bool:
    """Opt-out for anyone whose desktop makes a mess of it."""
    return os.environ.get("NABRIA_NO_PORTAL_SHORTCUTS", "") == ""
