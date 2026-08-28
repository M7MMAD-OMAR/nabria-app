# Plan: Nabria — a public "just talk" app

Working document. Spans sessions. Update the checkboxes and the log at the
bottom as things land.

## The idea

**Just talk.** Hold a key, speak, the words are in your document. Nothing else.
No account, no cloud, no meeting recorder, no notes, no assistant, no settings
you have to understand before it works.

Everything below is judged against that. A feature that needs explaining does
not go in.

Non-goals, written down so they stay non-goals: live partial transcription,
note-taking, summarisation, translation, an assistant, a tray menu, cloud sync,
a plugin system.

## Verdict on open-sourcing

Worth doing. The tool is small, does one thing, and the one thing works. There
is no comparable local-first dictation app with first-class Arabic on Linux.

The gap between "published" and "usable" is installation, not code. Order of
work: make it installable → make it portable → then publicise.

---

## Phase 0 — before the repo goes public

| | what | where |
|---|---|---|
| [x] | ~~Author email in every commit~~ — accepted, staying public | |
| [x] | ~~`هرميز` and a personal tool list in the default `vocabulary`~~ | `config.py` |
| [x] | ~~`gpu_select` hardcoded to this RTX 4070's PCI id~~ — detected now | `gpu.py` |
| [x] | ~~`APP_ID` is a personal handle~~ — `com.sbarah.Nabria` | `config.py` |
| [x] | ~~Theme palette path hardcoded to one desktop~~ — ships its own | `theme.py` |
| [ ] | README is written as "why not OpenWhispr" and quotes this machine's hardware | `README.md` |
| [ ] | Pick a licence. whisper.cpp is MIT, the models are MIT — **MIT unless told otherwise** | `LICENSE` |

**Licensing trap, resolved:** the engine that was bundled came out of
OpenWhispr and must not be redistributed. whisper.cpp v1.9.3 now builds from
source (MIT) and that is what is installed.

## Phase 1 — make it installable

- [x] **Engine builds from upstream source.** whisper.cpp v1.9.3,
      `-DWHISPER_BUILD_SERVER=ON -DGGML_VULKAN=ON -DGGML_NATIVE=OFF
      -DBUILD_SHARED_LIBS=OFF`. One 57 MB static binary covers every machine:
      it falls back to CPU cleanly where there is no Vulkan driver, and still
      picks up AVX2/FMA through ggml's runtime dispatch. `GGML_NATIVE=ON`
      measured no faster (21.4s vs 21.9s), so portability is free.
- [ ] **Publish that binary from CI** and have the installer fetch it by
      checksum. Pin the whisper.cpp tag in one file that CI, the installer and
      any packaging all read, or they drift and a checksum mismatch reads as a
      corrupt download. The checksum must be generated from our own build at
      release time — trusting whatever the release page says defeats the point.
- [ ] **Model**: cannot be shipped. Download on first run with visible
      progress, resume, and a checksum.
- [ ] **Setup wizard** on first launch: language → model → download → test the
      microphone → shortcut. The mic test already exists (`audio.measure`).
- [ ] `scripts/install.sh` still names OpenWhispr paths, so **the install path
      the README documents is broken for everyone**. Biggest single gap.

### Which model to default to — settled by measurement

11 s of audio, `large-v3-turbo` unless stated:

| | |
|---|---|
| discrete GPU (RTX 4070, Vulkan) | 0.32 s |
| CPU, 16 threads | 21.4 s |
| integrated GPU (Intel, Vulkan) | 63.5 s, then `ErrorDeviceLost` |
| **`base` on CPU, 8 threads** | **0.9 s** |
| `base` on CPU, 4 threads | 1.4 s |

So the wizard picks by hardware, not by taste: `large-v3-turbo` only where
there is a discrete GPU, `base` otherwise. Turbo on a CPU is half realtime,
which is not a slower experience but an unusable one.

`config.DEFAULTS["model"]` still names turbo, which is the wrong default on
most machines for anyone who skips the wizard. Resolve it at load time from
what is actually in `MODEL_DIR`.

## Phase 1b — packaging: **Flatpak is out**

Measured, not assumed. Hyprland implements `wp_security_context_v1`, and
sandboxed clients get 123 Wayland globals where the host gets 154. Among the
31 withheld are the three this application is built on:

| protocol | needed for |
|---|---|
| `zwlr_layer_shell_v1` | the indicator — the whole reason this exists rather than OpenWhispr |
| `zwp_virtual_keyboard_manager_v1` | `wtype`, one of two typing backends |
| `zwlr_data_control_manager_v1` | reading the clipboard back, which is how it is borrowed and returned |

Verified against two unrelated Flatpak apps with `WAYLAND_DEBUG=1` inside the
sandbox. This is not a permission Flatseal can grant — it is the compositor
refusing privileged protocols to a sandboxed client, which is the entire point
of the security-context protocol. Every other wlroots compositor that
implements it will behave the same way.

Nabria is therefore a **host application**. Revised packaging, in priority
order:

1. **AppImage** — one file, download and run, *unsandboxed*, so the protocols
   above are all available. This is the answer for a non-technical user.
2. **Install script** (`curl … | sh`) — for people who use a terminal.
3. **Native packages** — COPR, AUR, `.deb`. Community can own these later.

Do not spend time on a Flatpak. Say why in the README so nobody files it as an
oversight.

## Phase 2 — make it portable

- [x] **GPU**: auto-detected, and refuses integrated cards rather than merely
      deprioritising them. `gpu_select` remains a manual override.
- [ ] **Indicator fallback** when layer-shell is unavailable — GNOME, or a
      compositor that does not implement it. `Gtk4LayerShell.is_supported()`.
      Now also needed for the AppImage running under GNOME.
- [ ] **Palette**: read the GTK accent colour; the quickshell file stays an
      optional override.
- [ ] **Audio**: `pw-record` is PipeWire-only. Check for it and say so plainly
      rather than failing as a silent take.
- [ ] **Paste**: `TERMINAL_CLASSES` and `hyprctl activewindow` are
      Hyprland-specific. Needs a compositor-agnostic path.
- [ ] Test matrix: Hyprland, KDE, GNOME, Sway. X11 is out of scope — say so.

## Phase 3 — the shortcut problem

The most likely reason a non-technical user gives up. There is no cross-desktop
way to bind a global hotkey on Wayland.

- [ ] `org.freedesktop.portal.GlobalShortcuts` — present here, KDE implements
      it, GNOME is the open question. **Verify per desktop.**
- [ ] Where the portal is missing: generate the config snippet for the detected
      compositor and offer to write it, rather than printing instructions.
- [ ] Fallback that always works: a window with a record button.

## Phase 4 — release

- [x] Tests: 68 of them, including the real engine over real HTTP.
- [ ] CI: run them on push, and build the engine artifact.
- [ ] Rewrite the README for someone who has never heard of it.
- [ ] Move the current README's internals into `docs/DESIGN.md`.
- [ ] Retag. `v1.0.0` was a private milestone and overstates it — `v0.1.0`.
- [ ] `CONTRIBUTING.md`, issue templates.

## Open decisions

1. ~~**Name.**~~ **Nabria** (نَبْرة). `com.sbarah.Nabria`, `nabria.sbarah.com`.
2. ~~**Git history.**~~ Email stays public, by decision.
3. ~~**Default model.**~~ Decided by hardware — see the table above.
4. **Licence.** MIT unless told otherwise; proceeding on that assumption.
5. **Arabic-first or language-neutral?** The Levantine prompt work is a real
   differentiator. Ship it as the Arabic preset, detected from the locale?

## Session log

- **2026-08-27** — Plan written. Audit found 7 personal/machine-specific items.
  Confirmed the bundled `whisper-server` came from OpenWhispr.
- **2026-08-28** — Engine now builds from upstream source; the OpenWhispr
  binary is gone. Benchmarked CPU/discrete/integrated (table above) and found
  that an integrated GPU is *worse* than no GPU — rewrote device selection to
  refuse it, via `GGML_VK_VISIBLE_DEVICES` and a ctypes Vulkan enumeration
  rather than the Mesa layer. Added a 68-test suite. **Established that Flatpak
  cannot work**, which reorders all of packaging around AppImage. CLAUDE.md
  written.
