# dictate

Local voice dictation for Hyprland. Press the key, speak, press it again, the
text is typed into whatever window has focus. Nothing leaves the machine.

```
CTRL+ALT+Q          toggle
CTRL+ALT+SHIFT+Q    cancel the current take
CTRL+ALT+W          settings: model, microphone, history
dictate status      idle | recording | working
dictate last        the last transcript, even if typing it failed
```

## The indicator

A thin line under the centre of the screen. Recording draws a waveform scrolling
right to left, built from the levels actually measured off the microphone -- so
a dead input reads as a flat line instead of an indicator that looks the same
either way. Transcribing sweeps a bright segment along a static line: different
motion, not just a different colour. Done is a whole line, failed is a line
broken in the middle -- shape, because a Material You palette can hand you an
error colour and a primary colour that are the same salmon.

## Why not OpenWhispr

Its launcher forces `--ozone-platform=x11` on Wayland because its overlay
positioning needs X11. The floating mic is therefore an XWayland toplevel:
Hyprland stacks it against ordinary windows, so it drops behind anything
fullscreen and frequently never shows. No window rule reaches that.

Here the indicator is a `zwlr_layer_shell_v1` surface on the overlay layer --
above every window by protocol, `keyboard-interactivity: none` so it cannot take
focus. It also drops the meeting recorder, note-taking, calendar sync and the
qdrant vector database that OpenWhispr now starts on every launch.

## Install

```sh
scripts/install.sh
systemctl --user enable --now dictate
```

`install.sh` hard-links the whisper server and the model out of OpenWhispr's
directories into `~/.local/libexec/dictate` and `~/.local/share/dictate/models`.
Hard links, so the 1.6 GB is not duplicated and removing OpenWhispr cannot break
dictation. Override the sources with `SERVER_SOURCE=` / `MODEL_SOURCE=`.

## Configuration

`~/.config/dictate/config.json`. The knobs that matter:

| key | default | |
|---|---|---|
| `language` | `ar` | `auto` re-detects per 30s window and turns room noise into confident English |
| `vocabulary` | Levantine + Latin terms | initial prompt; see *Prompt* below |
| `keep_audio` | `false` | keep every take's WAV, not just the failed ones |
| `silence_threshold_dbfs` | `-42` | RMS below this = nothing was said |
| `max_seconds` | `0` | no limit; long takes are not cut |
| `idle_unload_seconds` | `900` | release the model's VRAM when unused |
| `prewarm` | `false` | load the model at login instead of on first use |
| `always_copy` | `false` | clipboard on every take, not just failures |
| `orb_position` | `bottom-center` | `orb_margin` sets the gap |
| `inject` | `auto` | `wtype`, then `ydotool`, then clipboard |

`gpu_select` is applied to the whisper subprocess only. Vulkan takes physical
device 0, which here is the Intel iGPU -- 2.5x slower than realtime on
large-v3-turbo. `10de:2860` puts the RTX 4070 first; 45s of audio goes from
114s to under 4s. Exporting it process-wide would drag the GTK UI onto the
discrete card too, so it is passed to that one subprocess.

## Nothing is lost

Every transcript is appended to `~/.local/share/dictate/history.jsonl` *before*
it is typed. If injection fails the text goes to the clipboard and a
notification says so. `dictate last` prints the most recent one.

If transcription itself fails, the recording is kept in
`~/.local/share/dictate/failed/` rather than deleted -- the audio is the one
thing that cannot be produced again.

A take being transcribed never blocks a new one. Press the key again while the
previous one is still working and recording starts immediately; finished takes
go through a single worker, so they are typed in the order they were spoken.

## Layout

```
fedora/dictate/
  app.py       daemon: control socket, state machine, GTK main loop
  orb.py       the layer-shell indicator
  settings_window.py  model / microphone / history, inside the daemon
  audio.py     input devices and level measurement, via wpctl
  recorder.py  pw-record -> WAV, with live level and RMS
  whisper.py   whisper.cpp server supervisor + /inference
  inject.py    wtype -> ydotool -> clipboard
  theme.py     live Material You palette
  history.py   transcript log
  notify.py    desktop notifications
```

## Gotchas

- `gtk4-layer-shell` must load before `libwayland-client`. Python cannot control
  link order, so `run-fedora.sh` sets `LD_PRELOAD` for the daemon. Without it
  the orb silently becomes an ordinary window.
- The GTK theme writes `window { background: @window_bg_color; }` into
  `~/.config/gtk-4.0/gtk.css`, which loads at `PRIORITY_USER`. The orb's CSS
  registers above that or the window paints an opaque rectangle.
- `Gtk.Application.run([])` skips `activate` entirely. Pass `sys.argv[:1]`.

## Prompt

The initial prompt is not just a glossary. Measured over one retained take, same
model, `-l ar`, three runs differing only in the prompt:

| prompt | transcript |
|---|---|
| MSA + Latin terms only | طيب **هلا** … أبحكي والتطبيق **شغل فأش وف** يعني هاي النتائج |
| none at all | طيب **هلأ** … أبحكي والتطبيق **شغل، فأشوف** يعني هاي النتائج |
| + Levantine function words | طيب **هلأ** … أبحكي، والتطبيق **شغال، فأشوف** يعني هي نتائج |

An all-MSA prompt was *worse than no prompt*: it pulls dialect toward MSA, and
هلأ came back as هلا. Adding هلأ، بحكي، شوف، هيك and friends recovered it, along
with شغال and the split فأشوف. Keep it short -- a long prompt starts leaking
into the transcript.

Mixed Arabic/Latin comes through as spoken; that is what the Latin half of the
prompt is for.

## Judging a transcript

You cannot, without the audio. `keep_audio` files every take under
`~/.local/share/dictate/takes/` and puts a play button on its history row, which
is the only way to tell a misheard word from a badly spoken one -- and the only
way to run two models over the *same* utterance. Nothing prunes that directory.

A clean take on this hardware measures around -31 dBFS RMS, -10 dBFS peak, no
clipped samples. Errors on audio like that belong to the model, not the mic.

## Not done yet

- Live partial transcription while speaking.
