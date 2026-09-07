# Windows

The Windows x64 release is a prerelease. The installer includes the
runtime and the local engine; no Python or developer tools are required.
Windows 10 version 1903 or later is required for UTF-8 engine arguments.

## Product

Nabria keeps the same interaction on both systems: press a shortcut, speak,
press it again, and the transcript is pasted into the focused application.
Transcription runs locally. Model download is a first-run operation.
Windows has its own WPF desktop with Fluent controls, a three-step setup,
settings, history and help. Linux keeps GTK. Both share the string catalogue,
configuration, model management and transcription worker.

Open Nabria from Start or a desktop shortcut. It is a graphical WinExe and
launches its backend without a console. Minimize it to keep dictating in other
applications; closing the window exits Nabria. The floating stop and cancel
controls do not take focus away from the application receiving the text.

## Layout

| Location | Responsibility |
|---|---|
| `src/nabria/windows/audio.py` | WASAPI through sounddevice, shared PCM statistics |
| `src/nabria/windows/control.py` | Authenticated named-pipe commands |
| `src/nabria/windows/desktop.py` | Single instance, hotkeys, non-activating indicator |
| `src/nabria/windows/inject.py` | Unicode paste and owned clipboard snapshots |
| `src/nabria/windows/notify.py` | Windows toast notifications |
| `src/nabria/windows/selftest.py` | Checks run inside the assembled runtime |
| `windows/Nabria.Desktop/` | Native WPF windows, single instance and child process lifetime |
| `src/nabria/windows/backend.py` | JSON pipe bridge to the shared dictation state machine |
| `packaging/windows/nabria.iss` | Per-user Inno Setup installer |

Settings live in `%APPDATA%\Nabria`. Models, history and retained audio live in
`%LOCALAPPDATA%\Nabria`. The application installs under
`%LOCALAPPDATA%\Programs\Nabria`. Uninstalling removes application files and
shortcuts, preserving settings, models and transcripts.

## Build

Use the UCRT64 environment from [MSYS2](https://www.msys2.org/). The complete
package list is in `.github/workflows/windows.yml`.

```sh
scripts/build-windows.sh
```

The script builds the whisper.cpp tag defined by `engine/VERSION`, then
assembles the Python runtime and engine. Install .NET SDK 10, then publish the
self-contained `windows/Nabria.Desktop` project into `dist/Nabria` using the
command in the Windows workflow. No .NET installation is needed by the user.
Run `python scripts/check-windows.py dist/Nabria` after publishing. Run Inno Setup with `AppVersion` taken from `src/nabria/__init__.py`; the workflow
shows the exact invocation. `dist/` contains the executable installer.

This replaces the earlier gvsbuild and PyInstaller proposal. MSYS2 is an
[officially documented GTK distribution route](https://www.gtk.org/docs/installations/windows/).
Its Python and native extensions stay together in the bundle; they are not mixed
with wheels built for a different Python runtime. No MSYS shell is needed by the
installed application. Runtime package versions and licenses accompany it.

## Shortcuts

Nabria attempts Win+Shift+9 to record, Win+Shift+0 to cancel, and Win+Shift+8
for settings. If Windows or another program owns a binding, it tries
Ctrl+Alt+F9, Ctrl+Alt+F10 and Ctrl+Alt+F11 respectively. The wizard displays
the bindings that actually registered. If both choices are unavailable, the
application reports the conflict and the main recording button remains
available. Existing bindings are never replaced.

Windows can prevent a non-elevated program from pasting into an elevated one.
In that case the transcript remains in history and on the clipboard for manual
paste. Running the dictation app as administrator is not required or recommended.

## Release checks

Validation combines the Windows workflow and an interactive Windows 11 Pro
Arabic VM. The CI runner denies foreground activation, so it explicitly skips
the native paste check; the interactive VM runs it without that exemption.
Together they exercise:

- Shared unit tests and WASAPI adapter tests.
- Runtime imports and engine startup with build tools removed from PATH.
- Launch from a relocated directory containing spaces and Arabic characters.
- Arabic engine arguments and inference from an Arabic model directory.
- Native desktop pages and all three setup steps in both languages, plus shared
  GTK compatibility checks. The desktop self-test also checks that no console
  window is attached.
- Native single-instance lock, named-pipe request and hotkey registration.
- Arabic and English clipboard contents, actual paste into a Windows EDIT
  control, and restoration of previous clipboard contents.
- A real speech sample transcribed through the bundled engine using a model
  verified against the catalogue checksum.
- Silent install, installed runtime checks and silent uninstall.
- The Linux `scripts/check.sh` distribution matrix.
- Native recording button and floating stop control, microphone testing, history
  copy, language changes, and cancelling deletion without modifying history.
- Interactive installation, setup, adopting a checksum-verified existing model,
  and independent English dictation with an Arabic interface.
- English and Arabic speech WAVs played into a dedicated virtual input, captured
  through real WASAPI, transcribed locally, saved in history and pasted into a Windows editor.
- Recording and cancel hotkeys with English and Arabic keyboard layouts.
- Silence rejection, image clipboard preservation and a newer clipboard copy
  taking precedence over restoration.

Windows 10 was not exercised; the interactive guest runs Windows 11.
A CI runner is not evidence about a physical microphone, device unplugging,
hardware GPU performance, multiple monitors, or every target application.
WASAPI warm-up still uses the conservative shared threshold and needs hardware
calibration. These limits remain explicit in the Windows release.

The installer is unsigned. A public release must say so and provide its SHA-256
checksum; signing requires a certificate that this project does not currently
have. A Windows prerelease must not replace the latest stable Linux release.
