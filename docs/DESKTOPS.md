# Desktops, measured

Which desktops this has actually been run on, what each one does, and how the
answer was obtained. Everything here is a measurement rather than a reading of
somebody's documentation, because the two have disagreed more than once.

The method: a stock live image in a virtual machine, driven through the
emulated keyboard, with a directory shared in from the host for the probe
script and its report. Nothing is installed on the host and nothing persists in
the guest — no disk image is attached, so the guest's whole filesystem is a RAM
overlay that disappears at power off.

Two things are asked in this order, and the order matters. First, whether the
desktop's portal backend implements `org.freedesktop.portal.GlobalShortcuts` at
all; then whether Nabria can use it. A backend that does not implement the
interface and an application that fails to bind look identical from inside the
application and have completely different answers.

## GNOME

**Fedora 44 Workstation, GNOME 50, Wayland.
`xdg-desktop-portal` 1.21.1, `xdg-desktop-portal-gnome` 50.0.**

| | |
|---|---|
| `org.freedesktop.portal.GlobalShortcuts` | **advertised, version 2** |
| the bundled engine | starts |
| `gtk4-layer-shell` | present, `libgtk4-layer-shell.so.0` |
| the layer-shell indicator | **not available** |

The interesting result is the last row, and it is not the one the README used
to imply. `gtk4-layer-shell` is installed and loads; the indicator still falls
back to an ordinary window, and says so in the log:

```
layer shell unavailable: the indicator is an ordinary window.
```

So on GNOME this is not a packaging problem and cannot be fixed by installing
anything. Mutter does not implement `zwlr_layer_shell_v1` — the protocol is
absent from the compositor, not from the library — so an overlay that stays
above other windows is not something a GNOME session can be asked for. The
fallback is loud rather than silent, which is the behaviour that was wanted:
the one time this project shipped a quiet degradation here it cost more
debugging than every noisy failure put together.

`GlobalShortcuts` being advertised at version 2 is the better news, and it is
worth stating plainly because the README's "the shortcut is manual" is written
for the general case: on this desktop the portal path exists.

## KDE Plasma

**Fedora 44 KDE, Plasma on Wayland.
`xdg-desktop-portal` 1.21.1, `xdg-desktop-portal-kde` 6.6.4.**

| | |
|---|---|
| `org.freedesktop.portal.GlobalShortcuts` | **advertised, version 2** |
| the bundled engine | starts |
| `gtk4-layer-shell` | present, `libgtk4-layer-shell.so.0` |
| the layer-shell indicator | **works** |

The last row is the contrast with GNOME, and it is why both were run rather
than one: the same package, the same library, the same Fedora — and here the
daemon's log has no fallback line at all, where GNOME's announces one. KWin
implements `zwlr_layer_shell_v1` and Mutter does not, so the indicator is a
real overlay on Plasma and an ordinary window on GNOME. Nothing about the
install differs.

What the application says on this desktop was read from the tree being
released rather than from the published package, because the published one is
older than what is being tested:

```
detected desktop: kde
a config file to write to: None
  System Settings → Keyboard → Shortcuts → Add → Command
  nabria toggle
```

`None` is the right answer and the interesting one. The wizard offers to write
the shortcut into a configuration file on the desktops that have one; Plasma's
shortcuts live in a settings application, so there is nothing a button could
honestly append to, and it correctly declines to offer one rather than
creating a file nobody reads. The Arabic string table resolves from the same
source, so the packaged layout is not the only one that carries it.

## What this does not cover

A live image is a stock session: no extensions, no user configuration, no
third-party portal backend. It answers "does the desktop provide this", not
"does it still work once somebody has configured it".
