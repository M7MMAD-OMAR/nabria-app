# Plan: dictate → a public "just talk" app

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

But the honest state of it: **nobody except this machine can install it.**
`scripts/install.sh` sources the engine and model from an OpenWhispr
installation that has been deleted, so a fresh clone gets a daemon with no
transcription engine and no way to obtain one. That is the whole gap between
"published" and "usable", and it is bigger than any code cleanup.

Order of work: make it installable → make it portable → then publicise.

---

## Phase 0 — before the repo goes public

Not optional. The repo is private today; every item here is visible the moment
it is not.

| | what | where |
|---|---|---|
| [ ] | **Author email is in every commit** — `m7mmad.omar0@gmail.com`. Public git history is permanently archived and scraped. Decide: rewrite history to the GitHub noreply address, or squash to a fresh initial commit, or accept it. | `git log --format='%ae'` |
| [ ] | `هرميز` (a private project name) is in the default `vocabulary`, along with a personal tool list | `fedora/dictate/config.py:41` |
| [ ] | `gpu_select` defaults to `10de:2860` — the PCI id of *this* RTX 4070. Meaningless or harmful anywhere else | `fedora/dictate/config.py:48` |
| [ ] | `APP_ID = "dev.sbarah.Dictate"` — personal handle, and it is the Wayland app id users will see | `fedora/dictate/config.py:16` |
| [ ] | Theme palette path is hardcoded to one desktop config (illogical-impulse / quickshell) | `fedora/dictate/theme.py:14` |
| [ ] | README is written as "why not OpenWhispr" and quotes this machine's hardware | `README.md` |
| [ ] | Commit bodies say "this laptop", "the user", name specific hardware. Harmless but reads as a personal journal | `git log` |
| [ ] | Pick a licence. whisper.cpp is MIT, Whisper models are MIT — MIT or Apache-2.0 keeps it all compatible | `LICENSE` |

**Licensing trap:** the bundled `whisper-server` was copied out of OpenWhispr.
Do not redistribute it. Either build whisper.cpp from source (MIT) or have the
installer fetch an official upstream release.

## Phase 1 — make it installable

The blocker. A person who has never opened a terminal must be able to get from
"I want this" to "it typed my words" without help.

- [ ] **Engine**: build `whisper-server` from whisper.cpp, or fetch a pinned
      upstream release by checksum. Vulkan build for GPU, CPU fallback.
- [ ] **Model**: cannot be shipped (1.6 GB). Download on first run, with visible
      progress and a checksum. Offer `base`/`small` as fast defaults and
      `large-v3-turbo` as the good one; let the user pick in the wizard.
- [ ] **Setup wizard** on first launch, in place of `config.json`:
      pick language → pick model → download → test microphone → done. The mic
      test already exists (`audio.measure`, Microphone tab); reuse it.
- [ ] **Packaging**, in priority order:
      1. **Flatpak** — one command on every distro, the only realistic answer
         for a non-technical user. *Risk to verify early:* layer-shell is a
         privileged protocol and may be refused inside the sandbox. If it is,
         the indicator needs a fallback (Phase 2) before Flatpak is viable.
      2. **Install script** (`curl … | sh`) — for people who do use a terminal.
      3. **Native packages** — COPR (Fedora), AUR (Arch), `.deb`. Community can
         own these later; do not block release on them.
- [ ] `scripts/install.sh` currently defaults to OpenWhispr paths. Rewrite
      around the download step above.

## Phase 2 — make it portable

Today it assumes Hyprland, gtk4-layer-shell, PipeWire, and one specific desktop
theme.

- [ ] **Indicator fallback** when layer-shell is unavailable (GNOME, or a
      sandbox that refuses it): an ordinary always-on-top window. Detect with
      `Gtk4LayerShell.is_supported()`, which is already the documented failure
      mode in `orb.py`.
- [ ] **Palette**: read the GTK theme's accent colour; keep the quickshell file
      as an optional override, not the source of truth.
- [ ] **GPU**: auto-detect instead of a hardcoded PCI id. Prefer discrete,
      fall back to CPU. `gpu_select` stays as a manual override.
- [ ] **Audio**: `pw-record` is PipeWire-only. Check for it and say so plainly
      if it is missing, rather than failing as a silent take.
- [ ] **Paste**: `TERMINAL_CLASSES` and `hyprctl activewindow` are
      Hyprland-specific (`inject.py`). Needs a compositor-agnostic path, or
      degrade to `wtype`.
- [ ] Test matrix: Hyprland, KDE, GNOME, Sway. X11 is out of scope — say so.

## Phase 3 — the shortcut problem

The one genuinely hard part, and the most likely reason a non-technical user
gives up.

There is no cross-desktop way to bind a global hotkey on Wayland. Today the
user edits a Hyprland config by hand.

- [ ] `org.freedesktop.portal.GlobalShortcuts` is the standard answer and is
      present on this machine. KDE implements it; GNOME's support is the open
      question. **Verify per desktop before committing to it.**
- [ ] Where the portal is missing: generate the config snippet for the detected
      compositor and offer to write it, rather than printing instructions.
- [ ] Fallback that always works: a visible window with a record button. Not the
      point of the app, but it means it is never *unusable*.

## Phase 4 — release

- [ ] Rewrite the README for someone who has never heard of it: what it does in
      one line, one install command, one screenshot/GIF of the indicator.
- [ ] Move the current README's internals into `docs/DESIGN.md` — the
      measurements and gotchas are good, they are just not a front page.
- [ ] CI: lint + import check on push. The project has no tests; a smoke test
      that the daemon starts and the socket answers is worth more than units.
- [ ] Tag `v0.1.0` on first public release. The existing `v1.0.0` was a private
      milestone and overstates it — retag or reset.
- [ ] `CONTRIBUTING.md`, issue templates.

## Open decisions — need your answer

1. **Name.** `dictate` is taken on PyPI/Flathub-adjacent namespaces and is very
   generic. "Just talk" suggests something like `justtalk` / `Just Talk`.
   Also decides `APP_ID` and the Flatpak id.
2. **Git history.** Rewrite the author email, squash to a fresh start, or leave
   it. Cannot be undone once public.
3. **Licence.** MIT or Apache-2.0.
4. **Default model.** `large-v3-turbo` (1.6 GB, best Arabic) or a smaller
   default with turbo offered as an upgrade. Affects first-run download time,
   which is the moment most people quit.
5. **Arabic-first or language-neutral?** The Levantine prompt work is a real
   differentiator. Ship it as the Arabic preset and detect the system locale?

## Session log

- **2026-08-27** — Plan written. Audit done: 7 personal/machine-specific items
  found (table above), plus the author email in git history. Confirmed
  `org.freedesktop.portal.GlobalShortcuts` exists on this machine and `flatpak`
  is installed (`flatpak-builder` is not). Confirmed the shipped
  `whisper-server` came from OpenWhispr and cannot be redistributed.
