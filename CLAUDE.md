# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Nabria (نَبْرة, "tone of voice") is local voice dictation for Linux. Press a
key, speak, press again, the text is pasted into the focused window. Nothing
leaves the machine. No account, no cloud.

The product concept is **"just talk"** and it is load-bearing: live partial
transcription, note-taking, summarisation, translation, an assistant, a tray
menu, cloud sync and a plugin system are all explicit non-goals. A feature that
needs explaining does not go in. See `PLAN.md`.

## Commands

```sh
scripts/install.sh                      # launchers, systemd unit, engine, model
systemctl --user enable --now nabria    # autostart (Hyprland ignores XDG autostart)
systemctl --user restart nabria         # REQUIRED after any config.json edit
journalctl --user -u nabria -f          # daemon stderr
```

```sh
scripts/run.sh daemon                   # run in the foreground instead of the unit
scripts/run.sh status                   # idle | recording | working
scripts/run.sh toggle | cancel | settings | last | quit
```

There is no test suite, no linter config and no build step yet — the Python is
run from source via `PYTHONPATH=src`. `PLAN.md` Phase 4 tracks adding CI.

Read `~/.local/state/nabria/nabria.log` first when anything misbehaves. It
records every take with its measured level, so it distinguishes "the hotkey did
nothing" from "the microphone was silent" — which is the single most common
misdiagnosis in this project's history.

## Architecture

**One long-lived daemon owns everything.** The hotkey does not launch a
process; it writes one line to a Unix socket at
`$XDG_RUNTIME_DIR/nabria.sock`. That is why a keypress feels instant and why
two presses can never race a second copy into existence. `Gtk.Application` with
a unique `application_id` is the second guard.

```
__main__.py   daemon | control command — control commands never import GTK
app.py        Daemon: socket, state machine, GTK main loop, transcribe worker
recorder.py   pw-record -> WAV, live level + RMS statistics
whisper.py    supervises whisper.cpp's whisper-server, POSTs /inference
inject.py     paste -> wtype -> ydotool -> clipboard
orb.py        the layer-shell indicator (custom Cairo drawing)
settings_window.py  model / microphone / history, built inside the daemon
audio.py      input devices and level measurement, via wpctl
config.py     every path, and the one JSON config file
theme.py      the shipped dark palette, optional desktop-palette override
gpu.py        sysfs GPU detection for MESA_VK_DEVICE_SELECT
history.py    transcript log (JSONL)
notify.py     desktop notifications
```

**Recording and transcription are independent.** A new take starts immediately
even while the previous one is still transcribing; finished takes pass through
a single worker thread, so they are typed in the order they were spoken. A
keypress is never dropped for being busy.

**Nothing is lost, by design.** The transcript is appended to
`history.jsonl` *before* it is typed. Injection failure falls back to the
clipboard plus a notification. A take that fails to transcribe keeps its WAV in
`failed/` rather than being deleted — the audio is the one thing that cannot be
produced again.

**The settings window lives inside the daemon on purpose.** A separate process
could write `config.json` but could not make the daemon re-read it or tear down
the loaded whisper server. `_apply_setting` goes memory → disk → engine reload,
and `model`/`language`/`vocabulary` trigger a bounded-wait reload because all
three are baked into the server's command line at startup.

## Non-obvious invariants

**Config is read once, in `Daemon.__init__`.** Editing `config.json` does
nothing until `systemctl --user restart nabria`. Verify what the engine
actually got with `ps -o args= -C whisper-server`.

**Always drop the first 0.6 s of a recording from the level statistics**
(`recorder.py`, `LEVEL_WARMUP_FRAMES`). The ALSA device-open pop measures about
-32 dBFS and dominates a short take's RMS — it defeated the silence gate before
this existed. The *live* meter must never be gated on the warm-up, or the
indicator is dead for its first half-second.

**A take shorter than the warm-up has no measured level at all.** It is neither
silence nor evidence about the microphone, and must be its own outcome
(`recorder.measured`), or stray double-presses accuse a healthy input.

**Whisper invents polite sentences out of room tone** — "شكرا للمشاهدة",
"Thank you." — and types them into the document. Hence both the RMS silence
gate and `whisper.HALLUCINATIONS`, matched after Arabic normalisation.

**`notify-send` parses any argument starting with `-` as an option.** A body
beginning with a level ("-66 dBFS…") is silently dropped. Pass `--` first.

### GTK / layer-shell traps

- `gtk4-layer-shell` must load before `libwayland-client`. Python cannot
  control link order, so `run.sh` sets `LD_PRELOAD` for the `daemon` subcommand
  only. Without it the orb silently degrades to an ordinary toplevel — the
  exact failure this tool exists to avoid.
- The desktop theme writes `window { background: @window_bg_color; }` into
  `~/.config/gtk-4.0/gtk.css` at `PRIORITY_USER`. The orb's CSS must register
  *above* that or the indicator paints an opaque rectangle.
- `Gtk.Application.run([])` skips `activate` entirely and exits. Pass
  `sys.argv[:1]`.

### Engine and hardware (measured, 11 s of audio, `large-v3-turbo`)

| backend | time |
|---|---|
| discrete GPU (RTX 4070, Vulkan) | 0.32 s warm |
| CPU, 16 threads | 21.4 s |
| integrated GPU (Intel, Vulkan) | 63.5 s, then `ErrorDeviceLost` |

Two consequences that are easy to get backwards:

- **An integrated GPU is worse than useless** — three times slower than CPU and
  it crashes. GPU selection must *refuse* integrated cards, not merely prefer
  discrete ones.
- **`large-v3-turbo` on CPU is 2× slower than realtime**, i.e. unusable. Any
  machine without a discrete GPU needs a smaller model.

`gpu_select` is applied to the whisper subprocess only. Exporting
`MESA_VK_DEVICE_SELECT` process-wide would drag the GTK UI onto the discrete
card too.

Build the engine from upstream whisper.cpp (MIT). `-DGGML_NATIVE=OFF`
`-DBUILD_SHARED_LIBS=OFF` `-DGGML_VULKAN=ON` produces one portable static
binary that runs everywhere: it falls back to CPU cleanly when no Vulkan driver
is present, and still picks up AVX2/FMA through ggml's runtime dispatch —
`GGML_NATIVE=ON` measured no faster. **Do not redistribute the
`whisper-server` currently installed on the original development machine**; it
was copied out of OpenWhispr.

### Arabic

`language` is `ar`, not `auto`: per-window auto-detect turns room noise into
confident English gibberish.

The `vocabulary` initial prompt (`--prompt` + `--carry-initial-prompt`) is not
just a glossary — **an all-MSA prompt measured worse than no prompt at all**,
because it pulls spoken dialect toward the formal register (هلأ came back as
هلا). If a prompt is shipped for Arabic it must carry dialect function words.
Keep it short; a long prompt leaks into the transcript.

### Delivery

`wtype` and `ydotool` type one keystroke at a time — 2.59 s for 585 characters,
against 0.02 s for a paste. So `auto` pastes first: Ctrl+V, or Ctrl+Shift+V
when the focused window's class is a terminal.

The clipboard is *borrowed*. Previous contents are captured with their MIME
type and restored 1.5 s later, typed — reading a copied image back as text
would replace it with mojibake. The restore stands down if anything was copied
in the meantime, so a newer copy is never destroyed to return an older one.

## Style

Comments explain *why*, and usually cite the measurement or the failure that
forced the code to be that way. Match that: a comment restating what the line
does is noise here, but the reason a threshold is -42 and not -40 is the most
valuable thing on the page. Several of the invariants above exist only because
a comment recorded the bug that produced them.

User-facing strings in the daemon are Arabic; log lines are English.
