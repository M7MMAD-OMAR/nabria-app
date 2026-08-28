# A Windows version — analysis

Asked for as an option, so this is the analysis, not a decision taken. The
short answer is at the bottom and it is **not yet**, for one reason that is not
the one you would expect.

## The port surface, module by module

`src/nabria/` is 3,942 lines. Sorted by what the port costs, not by what the
file does.

### Free — already portable

| | why |
|---|---|
| `whisper.py` | whisper.cpp publishes official Windows binaries, Vulkan included. It is an HTTP server on localhost either way |
| `gpu.py` | it enumerates through `libvulkan` with ctypes. On Windows the library is `vulkan-1.dll` and every structure is identical — one name changes |
| `history.py`, `models.py` | JSON, HTTP and file paths |
| `config.py` | the XDG lookups become `%APPDATA%` / `%LOCALAPPDATA%`. Contained, because every path in the project already comes from this one module |

### Cheap — a different call for the same idea

| | Linux | Windows |
|---|---|---|
| control socket | Unix socket in `$XDG_RUNTIME_DIR` | a named pipe. No stdlib server, so ctypes on `CreateNamedPipe` |
| autostart | a `--user` systemd unit | a shortcut in `shell:startup` |
| `notify.py` | `notify-send` | toast, which needs a registered AppUserModelID |
| `audio.py` | `wpctl` | WASAPI enumeration |

### Cheaper than Linux — genuinely better there

| | |
|---|---|
| **the global hotkey** | `RegisterHotKey` and it is done. On Wayland there is no cross-desktop way to claim a shortcut at all: `portal.py` is 214 lines, `shortcut.py` prints per-compositor instructions, and PLAN.md calls this "the most likely reason a non-technical user gives up". On Windows the entire problem evaporates |
| **typing the text** | `SendInput` for Ctrl+V, `OpenClipboard`/`SetClipboardData` to borrow the clipboard. No `wtype`, no `ydotool`, no per-compositor focus probe — `GetForegroundWindow` answers directly what has focus and which process owns it, which is what `inject.py` currently asks Hyprland, sway and niri separately |

### Expensive

| | |
|---|---|
| `recorder.py` (211) | there is no `pw-record` on Windows. WASAPI through PortAudio, or ffmpeg's dshow. The recording is the easy half; the live level meter and the RMS statistics have to be re-derived, and those carry `LEVEL_WARMUP_FRAMES` and the silence gate — the thresholds in them were measured against one specific class of device-open artefact, and there is no reason a WASAPI capture has the same one |
| `orb.py` (344) | no layer shell. The equivalent exists — `WS_EX_TOPMOST` with `WS_EX_TRANSPARENT`, `WS_EX_NOACTIVATE` and `WS_EX_LAYERED` gives a click-through window that stays above everything and never takes focus, which is exactly what the layer surface buys. Reaching it means getting the `HWND` out of GDK and calling `SetWindowLong`. Feasible; fiddly; and the Cairo drawing itself ports untouched |

### The real one

**GTK 4 and PyGObject on Windows.** Not the code — `app.py`, `orb.py`,
`wizard.py` and `settings_window.py` are about 1,730 lines of GTK that would
run unchanged. The problem is delivering it.

MSYS2 packages GTK 4 and PyGObject and they work. Shipping that to somebody who
does not have MSYS2 means bundling the runtime: GTK, GLib, Pango, HarfBuzz,
Cairo, gdk-pixbuf, fontconfig and a Python. Precedent exists — several GTK
applications ship Windows installers — but it is a build-system project in its
own right, it lands somewhere around 200 MB before the model, and an unsigned
installer meets SmartScreen, which for a dictation tool that asks for the
microphone is not a small thing.

The alternatives are worse, not better:

- **A second, native UI.** Then there are two of everything — two wizards, two
  settings windows, two indicators — and they drift. This project's own
  history is a list of things that drifted the moment they were written twice.
- **Move the UI to Qt** so one toolkit covers both. Cleanest in theory, and it
  rewrites the working Linux UI to get there.

## The finding that actually decides it

Everything above is work, and work can be scheduled. This cannot:

> **There is no Windows equivalent of `scripts/check.sh --distros`.**

Every packaging bug this project has ever had was found by running the
installer inside a clean container — a typelib in a separate package, a library
path Debian uses and Fedora does not, `pw-record` living somewhere else, a
missing `pycairo`, an engine linking `libgomp`. Not one was found by reading
the code. `CLAUDE.md` says it plainly: package names cannot be reasoned about,
only run.

A Windows port has none of that:

| | |
|---|---|
| clean-machine test | Windows containers are multi-gigabyte, need a Windows host, and have no GUI |
| the CI runner | `windows-latest` is free on a public repository — and has **no audio device and no interactive desktop session** |
| so what can be tested | imports, unit tests, the HTTP call to the engine |
| what cannot | the microphone, the hotkey, the always-on-top overlay, pasting into a real window |

Which is to say: everything that is genuinely new about the port is exactly
what no automated test could reach. It would ship on the strength of one
person trying it on one machine — against a standing requirement that every
platform be really tested. That is the argument, and it is not one more
engineering week can answer.

## Dead ends, recorded so they are not re-proposed

- **WSLg.** It gives a Wayland session, so the app would run. It could not
  paste into a Windows application, which is the entire function.
- **A Linux engine serving a Windows client.** Breaks "nothing leaves the
  machine", which is the product.
- **A browser version.** Same objection, plus no way to type into other
  applications.

## If it is wanted anyway — the cheap shape

Do not port the desktop UI. Ship the part that has no UI:

```
nabria toggle / cancel / last      +  RegisterHotKey  +  toast notifications
```

No overlay, no wizard, no settings window — a config file and a tray-less
background process. Roughly the modules in the "free" and "cheap" tables plus
`recorder.py`, so about a third of the work and none of the GTK-on-Windows
problem, and the notification carries the state the pill would have shown.

That is a real product for someone who wants local dictation on Windows. It is
a different product from this one, and it should be honest about being one.

## Recommendation

**Not now**, in this order:

1. The port cannot be verified the way the rest of this project is verified.
2. The Linux side has a `[ ]` in `PLAN.md` against KDE and GNOME — the two
   desktops most Linux users actually run. Finishing the platform already
   claimed beats adding one that is not.
3. The GTK-on-Windows packaging is a project, not a task.

What would change the answer: Windows users asking for it, or a decision to
ship the reduced shape above, which is small enough to be worth doing on its
own merits and which sidesteps reasons 1 and 3 together.

macOS is not analysed here. It has the same UI-delivery problem, the same lack
of a container story, and unlike Windows it also requires notarisation and
accessibility permissions before anything can type into another application.
