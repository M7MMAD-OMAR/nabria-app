# Windows — the plan

An earlier draft of this file recommended *not now*. That recommendation was
overruled, so this is the plan to ship it. The analysis behind the original
answer is kept below, because two of its findings are constraints on the plan
rather than arguments against it, and one is a risk that has to be accepted out
loud rather than discovered later.

## The decision everything else follows from

**Keep GTK 4. Do not write a second, native interface.**

The choice is between two costs and only one of them is recoverable:

| | keep GTK 4 | native Windows UI |
|---|---|---|
| the 1,730 lines of wizard, settings and indicator | run unchanged | rewritten |
| the cost | delivering the GTK runtime, once | maintaining two of everything, forever |
| when a string changes | one file | two, and one of them is forgotten |

This project's history is a list of things that drifted the moment they were
written twice — a model list restated in two places, a test-file list kept in
two copies, a version in the spec and a version in the source. A second user
interface is that failure at the largest possible scale, and it would land on
the half of the product a new user meets first.

The delivery cost, by contrast, is paid once and then automated. It is a build
pipeline, and a build pipeline is a thing this project already has.

The overlay does not change the answer either way: GDK on Windows hands out the
real `HWND` (`gdk_win32_surface_get_handle`), so `WS_EX_LAYERED |
WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOPMOST` gives exactly what the
layer surface gives — click-through, always on top, never focused — on a window
whose contents are still the same Cairo drawing. It was the strongest argument
for dropping GTK and it does not survive contact with the API.

## What the port is, module by module

`src/nabria/` is about 4,000 lines. Sorted by what the port costs.

### Free — already portable

| | why |
|---|---|
| `whisper.py` | whisper.cpp publishes official Windows binaries, Vulkan included. It is an HTTP server on localhost either way |
| `gpu.py` | it enumerates through `libvulkan` with ctypes. On Windows the library is `vulkan-1.dll` and every structure is identical — one name changes |
| `i18n.py` | a dict. Arabic and right-to-left are a Pango question, and Pango comes with GTK |
| `history.py`, `models.py` | JSON, HTTP and file paths |
| `config.py` | the XDG lookups become `%APPDATA%` / `%LOCALAPPDATA%`. Contained, because every path in the project already comes from this one module |

### Cheaper than Linux — genuinely better there

| | Linux | Windows |
|---|---|---|
| **the global hotkey** | no cross-desktop way to claim one at all: `portal.py` is 214 lines, `shortcut.py` prints per-compositor instructions, and `PLAN.md` calls this "the most likely reason a non-technical user gives up" | `RegisterHotKey`, and it is done |
| **typing the text** | `wtype`, `ydotool`, and a per-compositor focus probe asked of Hyprland, sway and niri separately | `SendInput` for Ctrl+V; `GetForegroundWindow` answers what has focus and which process owns it |

The two hardest problems on Linux are the two easiest on Windows. That is worth
saying plainly, because it means the Windows build can be *better* than the
Linux one at the thing users actually judge: pressing a key and having it work
with no configuration at all.

### Real work

| | |
|---|---|
| `recorder.py` (211) | no `pw-record`. WASAPI through `sounddevice`, or ffmpeg's dshow. The recording is the easy half; the live meter, `LEVEL_WARMUP_FRAMES` and the silence gate carry thresholds measured against one specific device-open artefact, and there is no reason a WASAPI capture has the same one. **These have to be re-measured, not copied** |
| `orb.py` (344) | the extended window styles above, reached through the `HWND`. The drawing itself ports untouched |
| control socket | a Unix socket in `$XDG_RUNTIME_DIR` becomes a named pipe. No stdlib server for it, so ctypes on `CreateNamedPipe` |
| `notify.py` | `notify-send` becomes a toast, which needs a registered AppUserModelID — the installer's job, and the same shape as the Linux desktop entry |
| autostart | a `--user` systemd unit becomes a shortcut in `shell:startup` |

## Delivery — the actual project

Three stages, each of which has a finished artefact.

**1. The GTK stack.** [gvsbuild](https://github.com/wingtk/gvsbuild) builds GTK
4, Cairo, PyGObject and pycairo against Visual Studio:

```
gvsbuild build --enable-gi --py-wheel gtk4 pygobject
```

Its own README says the prebuilt zips are untested and should not be
redistributed — so this project builds them, exactly as it already builds the
whisper engine rather than trusting a stranger's binary. Same principle, same
reason.

**2. One directory that runs.** PyInstaller, with the gvsbuild wheels installed
rather than the ones from PyPI. This is the specific trap on this path: the
PyPI wheels give `ValueError: Namespace Gtk not Available` once frozen, and it
appears at run time on the user's machine rather than at build time on yours.
`--onedir`, not `--onefile`: a single executable unpacks the whole GTK runtime
to a temporary directory on every launch, and this is a daemon that starts with
the session.

**3. An installer.** Inno Setup. Per-user by default, matching every other
install path in this project — `install.sh` refuses to run as root for the same
reason. It registers the AppUserModelID for toasts, writes the startup
shortcut, and carries the whisper engine. Expect ~200 MB before the model.

Signing is not part of the plan, because there is no certificate. An unsigned
installer meets SmartScreen, and for a tool that asks for the microphone that
is not a small thing — so the download page has to say what SmartScreen will
say, rather than leaving the user to decide alone whether to trust it.

## One command, and updates — winget

This is the same requirement as Copr and the AUR, not a new one, and Windows
answers it the same way.

```
winget install Nabria
winget upgrade --all
```

Submission is a pull request of a YAML manifest to
[microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs), where an
automated check validates it. Updates are then a
[wingetcreate](https://github.com/microsoft/winget-create) call, which has an
autonomous mode built for exactly this — it opens the update pull request from
CI when a release is published. So the Windows release script ends the same way
`release.sh` does, and the user's update notification is Windows' own.

Like Copr and the AUR, the first submission needs a person with an account.
Unlike them, it is a GitHub account, which already exists.

## The risk that is being accepted

The original analysis put this first, and it is still true:

> **There is no Windows equivalent of `scripts/check.sh --distros`.**

Every packaging bug this project has ever had was found by running the
installer inside a clean container — a typelib in a separate package, a library
path Debian uses and Fedora does not, `pw-record` living somewhere else, an
engine linking `libgomp`. Not one was found by reading the code.

| | |
|---|---|
| clean-machine test | Windows containers are multi-gigabyte, need a Windows host, and have no GUI |
| the CI runner | `windows-latest` is free on a public repository — and has **no audio device and no interactive desktop session** |

What CI *can* cover, and should, because it is more than nothing:

- every module imports under the frozen build
- the unit suite, minus the ones that need audio or a display
- the engine starts and answers over HTTP
- the installer installs silently (`/VERYSILENT`), the files land, the
  uninstaller removes them

What no automated test reaches: the microphone, the hotkey, the always-on-top
overlay, pasting into a real window. Those are exactly what is new about the
port, and they will ship on the strength of a person trying them. **The release
notes should say the Windows build is newer and less tested than the Linux
one**, for as long as that is true. Saying so is cheap; being found out is not.

## Dead ends, recorded so they are not re-proposed

- **WSLg.** It gives a Wayland session, so the app would run. It could not
  paste into a Windows application, which is the entire function.
- **A Linux engine serving a Windows client.** Breaks "nothing leaves the
  machine", which is the product.
- **A browser version.** Same objection, plus no way to type into other
  applications.
- **`--onefile` PyInstaller.** Unpacks ~200 MB per launch, for a process that
  starts with the session.
- **Redistributing gvsbuild's prebuilt zip.** Its maintainers say not to.

## Order of work

1. `config.py` paths and the named-pipe control socket — no UI, testable in CI.
2. `recorder.py` on WASAPI, and **re-measure** the warm-up and the silence gate.
3. `RegisterHotKey` and `SendInput`. At this point it is the reduced shape
   below, and it already works.
4. gvsbuild → PyInstaller → Inno Setup. The longest stage and the one that
   ends with something a person can double-click.
5. The `HWND` styles for the orb.
6. winget manifest, and wingetcreate in the release pipeline.

Steps 1–3 are a usable product on their own:

```
nabria toggle / cancel / last   +  RegisterHotKey  +  toast notifications
```

No overlay, no wizard, no settings window — a config file and a background
process, with the notification carrying the state the pill would have shown.
It is worth treating as a milestone with a release on it rather than as
scaffolding, because if step 4 turns out to be worse than it looks, that is the
thing that shipped.

macOS is analysed separately in [MACOS.md](MACOS.md). Read its first paragraph
before planning anything there: the blocker is a bill, not a build.
