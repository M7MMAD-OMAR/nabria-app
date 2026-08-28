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
| [x] | ~~README is written as "why not OpenWhispr"~~ — rewritten; internals in `docs/DESIGN.md` | |
| [x] | ~~Pick a licence~~ — **MIT**, matching whisper.cpp and the weights | `LICENSE` |

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
- [x] **Tag pinned in one place** — `engine/VERSION`, read by the build script,
      CI and the installer, so they cannot drift.
- [x] **Model** downloads on first run with progress, resume and a checksum.
      The checksums are the upstream Git-LFS oids, verified against local copies.
- [x] **Setup wizard** on first launch: model → download → microphone test →
      shortcut.
- [x] **`scripts/install.sh` rewritten.** Checks dependencies in the running
      distro's own package names, builds the engine, fetches the right model,
      writes the unit and a desktop entry. Verified in clean Fedora, Debian
      trixie and Ubuntu 24.04 containers.
- [x] **Prebuilt engine.** `scripts/release-engine.sh` builds it in an Ubuntu
      22.04 container (old glibc, so it runs everywhere newer) and records the
      sha256 in `engine/CHECKSUMS`; the installer fetches and verifies against
      that in-repo checksum, runs `--help` on the result, and falls back to
      building only if any of it fails. Done locally rather than in CI: it
      happens when `engine/VERSION` changes, which is rarely.

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

`config.load()` falls back to the largest model actually present when the
configured one is missing, so skipping the wizard cannot leave a machine
pointed at a model it does not have.

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

1. **`scripts/install.sh`** — what actually ships today, and the only path
   that has been tested. Verified in clean Fedora, Debian trixie and Ubuntu
   24.04 containers, automatically, by `scripts/check.sh --distros`. It fetches
   a prebuilt engine, so a compiler is a fallback rather than a requirement.
2. **AppImage** — *unproven*. Attractive because it is one unsandboxed file,
   but bundling GTK4, PyGObject and the layer-shell typelib is a build-container
   job that has not been attempted. Do not describe it as the plan until
   something has actually been built.
3. **Native packages** — COPR, AUR, `.deb`. Community can own these later.

Do not spend time on a Flatpak. Say why in the README so nobody files it as an
oversight.

## Phase 2 — make it portable

- [x] **GPU**: auto-detected, and refuses integrated cards rather than merely
      deprioritising them. `gpu_select` remains a manual override.
- [x] **Indicator fallback** when layer-shell is unavailable. The typelib
      import is guarded, so a missing package no longer stops the daemon from
      starting at all — which is what it used to do.
- [x] **Audio**: a missing `pw-record` is its own error naming the package per
      distro, not a stack trace.
- [x] **Paste**: Hyprland, sway and niri are each asked what has focus; where
      nothing can say, Ctrl+V, which is right everywhere but a terminal.
- [ ] **Palette**: read the GTK accent colour as an optional source.
- [ ] Test matrix: only Hyprland has been exercised on real hardware. KDE,
      GNOME and Sway are covered by code paths and containers, not by use.
      X11 is out of scope and the README says so.

## Phase 3 — the shortcut problem

The most likely reason a non-technical user gives up. There is no cross-desktop
way to bind a global hotkey on Wayland.

- [x] `org.freedesktop.portal.GlobalShortcuts` implemented and **verified
      working on Hyprland** — `hyprctl globalshortcuts` lists both. The catch,
      which cost real time: the portal refuses a caller it cannot name, and
      names a host application from its **systemd unit**
      (`app-<app-id>-...`). The unit is named accordingly; renaming it breaks
      shortcuts silently. See `docs/DESIGN.md`.
- [x] Where the portal is missing, `shortcut.py` detects the compositor and
      prints the exact line, shared by the installer and the wizard.
- [ ] Verify the portal on KDE and GNOME. Both backends advertise the
      interface; neither has been tried.
- [ ] Offer to *write* the config snippet rather than print it.
- [ ] A window with a record button, for when no key can be bound at all.

## Phase 4 — release

- [x] Tests: 126, including the real engine over real HTTP.
- [x] CI: four jobs on push — units on two Python versions, GTK under xvfb,
      shellcheck, and a full engine build that transcribes for real.
- [x] README rewritten for a newcomer; internals moved to `docs/DESIGN.md`.
- [x] Retagged `v0.1.0`; the private `v1.0.0` is deleted.
- [x] Desktop entry, so it appears in application menus.
- [ ] `CONTRIBUTING.md`, issue templates.
- [ ] Make the repository public. Everything blocking it is done.

## Open decisions

1. ~~**Name.**~~ **Nabria** (نَبْرة). `com.sbarah.Nabria`, `nabria.sbarah.com`.
2. ~~**Git history.**~~ Email stays public, by decision.
3. ~~**Default model.**~~ Decided by hardware — see the table above.
4. ~~**Licence.**~~ MIT, chosen rather than asked about since it was the last
   thing blocking Phase 0. Apache-2.0 remains an easy change.
5. **Arabic-first or language-neutral?** Still open. `language` defaults to
   `auto` in a fresh config, and the Levantine prompt is not shipped — it is
   the real differentiator and nobody gets it without being told. Ship it as an
   Arabic preset detected from the locale?
6. **Who builds the engine?** Right now every user compiles whisper.cpp. That
   is the remaining gap between a developer and an ordinary person.

## Session log

- **2026-08-27** — Plan written. Audit found 7 personal/machine-specific items.
  Confirmed the bundled `whisper-server` came from OpenWhispr.
- **2026-08-28** — Engine now builds from upstream source; the OpenWhispr
  binary is gone. Benchmarked CPU/discrete/integrated (table above) and found
  that an integrated GPU is *worse* than no GPU — rewrote device selection to
  refuse it, via `GGML_VK_VISIBLE_DEVICES` and a ctypes Vulkan enumeration
  rather than the Mesa layer. **Established that Flatpak cannot work**, which
  reordered packaging. Rewrote the installer and verified it in Fedora, Debian
  and Ubuntu containers, which found three bugs that would each have silently
  cost a user their indicator. Added the model downloader, the setup wizard,
  the desktop entry, the layer-shell fallback, and portal shortcuts. 126 tests
  and CI. Licence MIT, retagged `v0.1.0`. CLAUDE.md and docs/DESIGN.md written.

  Left deliberately: publishing a prebuilt engine, KDE/GNOME verification, and
  the Arabic preset.
