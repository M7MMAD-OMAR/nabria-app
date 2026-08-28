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
| [x] | ~~`gpu_select` hardcoded to one card's PCI id~~ — detected now | `gpu.py` |
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
      measured no faster, within noise, so portability is free.
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

Measured locally across the three models on a discrete GPU, on the CPU, and on
an integrated GPU. **The figures are not recorded here or anywhere else in the
repository**, because a time from one machine published as a property of the
software is a benchmark claim, and it is wrong for every reader whose hardware
differs — which is all of them. What the measurement settled:

- the largest model is only usable where there is a discrete GPU; on a CPU it
  ran slower than speech, which is unusable rather than slow
- the smallest model is comfortable on a CPU
- an integrated GPU was worse than the CPU it would replace, and unstable

So the wizard picks by hardware, not by taste. The README states requirements
instead: what is needed at least, and what the largest model wants.

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

1. **Native packages** — `.rpm`, `.deb`, AUR. Built by `scripts/package.sh` in
   a container of the distribution each targets, and installed by
   `scripts/check.sh --packages` in clean Fedora, Debian and Ubuntu. This is
   the path that matters, because it is the only one where **updates and
   update notifications are free**: `dnf` and `apt` already check, and the
   desktop already tells the user. See `docs/PACKAGING.md`.
2. **`scripts/install.sh`**, reached by `curl … | sh` — for everything with no
   package. Verified in the same three containers by `--distros`.
3. **Copr** for Fedora and possibly OBS for Debian — the hosted repositories
   that turn (1) from a downloaded file into a subscription. Copr needs a
   Fedora account; steps are written down in `docs/PACKAGING.md`.
4. **AppImage** — *decided against*, not merely unproven. Bundling
   `gtk4-layer-shell` means controlling library load order inside the bundle,
   and the reason `run.sh` exists at all is that Python cannot control link
   order. An AppImage that silently loses the layer shell is one whose
   indicator can be covered by a fullscreen window.

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
- [x] **Installable without git.** `scripts/bootstrap.sh` is the one-line
      install; `scripts/release.sh` publishes it beside a source tarball built
      from the tag. Verified end to end on a clean Debian trixie with nothing
      but the dependencies: fetched the installer from the published URL,
      verified the engine, downloaded the model and transcribed.
- [x] **Arabic and English throughout.** `i18n.py` holds every user-facing
      string in both, with `ui_language` (`auto|en|ar`) separate from the
      spoken `language`. Right-to-left is not alignment alone: Latin runs are
      isolated with FSI/PDI, bare digits deliberately are not, and `xalign`
      comes from `i18n.start_align()` because GTK's is absolute.
- [x] **Screenshots**, both languages, generated by `scripts/screenshots.py`
      from a profile created seconds earlier -- so no personal vocabulary or
      transcript can reach the repository, and they cannot go stale silently.
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
- [x] Repository public.
- [x] **A release that can actually be downloaded.** The `v0.1.0` tag had no
      release attached and was eleven commits behind — no prebuilt engine, no
      portal shortcuts, the `pycairo` bug still in it — so it was left as the
      historical marker it is and `v0.2.x` published instead.
- [ ] `CONTRIBUTING.md`, issue templates.

## Phase 4b — one command, and updates

- [x] **`.rpm`, `.deb` and an AUR `PKGBUILD`**, all from one layout definition
      (`packaging/layout.sh`), all bundling the engine so nothing is left to
      compile or fetch but the model. Installed by the distribution's own
      package manager in clean containers by `scripts/check.sh --packages` —
      Ubuntu 24.04 among them, because it is the machine that proves
      `gtk4-layer-shell` has to be a recommendation rather than a dependency.
- [x] **Stable download names.** `releases/latest/download/nabria.rpm` and
      `.deb`, so the install lines in the README never need editing.
- [ ] **Copr**, which is what turns the `.rpm` into automatic updates. Needs a
      Fedora account; the spec is already suitable and the steps are in
      `docs/PACKAGING.md`.
- [ ] **AUR submission** — likewise a person with an account, not a script.
- [ ] An apt repository. Deliberately last: it needs a GPG signing key
      wherever it is built, and putting one in CI is the dependency this
      project is without. OBS is the candidate.

No self-update code, and none wanted. A packaged install is updated by the
same machinery that updates everything else on the machine, and that is a
smaller and better answer than anything in-app.

- [x] **`install.sh --uninstall`**, and a warning when a user install would
      shadow a packaged one. It shadows at every point — the launcher on PATH,
      the unit, the desktop entry — so `dnf install nabria` after `install.sh`
      did nothing observable, and there was no way out of it because the repo
      had no uninstall path at all.

## Phase 5 — the landing page

Planned in `docs/SITE.md`; nothing built. All drawn, no screenshots, almost no
words, and the drawing is the orb's own geometry rather than an invented
visual language. GitHub Pages from `main` `/docs`, so there is no build step
and nothing that can fail. Four questions are open at the bottom of that file.

## Phase 6 — other platforms

**Windows is planned, not deferred** — `docs/WINDOWS.md`. The earlier "not now"
was overruled, so that file is now a shipping plan with the order of work in
it. The decision it turns on: keep GTK 4 and pay the delivery cost once, rather
than write a second interface and maintain two of everything. Delivery is
gvsbuild → PyInstaller `--onedir` → Inno Setup, and `winget` is the Copr/AUR
equivalent, updated from CI by wingetcreate.

The one thing that did *not* change is the risk: there is no Windows equivalent
of `check.sh --distros`, and `windows-latest` has no audio device and no
interactive session — so the microphone, the hotkey, the overlay and pasting
into a real window ship on the strength of a person trying them. That is
accepted out loud, and the release notes must say so while it is true.

**macOS is blocked on a purchase**, not on code — `docs/MACOS.md`. Input
Monitoring and Accessibility are recorded against the app's code identity, so
without a Developer ID ($99/year) every rebuild is a new app and the user
re-grants both permissions on every update. Homebrew solves finding and
updating; it does not solve the signature.

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
6. ~~**Who builds the engine?**~~ Nobody: `scripts/release-engine.sh` publishes
   it and the installer fetches it. Compiling is the fallback.

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

- **2026-08-28, later** — Published a release anyone can install from:
  `bootstrap.sh` for the one-line path, `release.sh` to publish it. Proved end
  to end on a clean Debian trixie, from the real URL through to a
  transcription. Planned the landing page and analysed a Windows port; both
  documents, no code.

  A cleanup review found three things worth recording. The container matrix was
  tarring the *working tree* and calling it the release archive — the one
  archive that cannot catch a file missing from the release, since it contains
  everything uncommitted too; both it and `release.sh` now go through
  `release_tarball` in `common.sh`, which also made that step ~200× cheaper.
  `release-engine.sh` did not pass `--prerelease`, so the next engine build
  would have taken `releases/latest` away from the install command in the
  README and 404ed it. And `__version__` said 0.1.0 with `v0.2.0` tagged, with
  nothing reading either — `release.sh` now refuses a tag that disagrees with
  the source, which is how the mismatch was found.
