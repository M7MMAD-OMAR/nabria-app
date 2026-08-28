# Nabria

*نَبْرة — the tone of a voice.*

Local voice dictation for Linux. Press the key, speak, press it again, the
text is typed into whatever window has focus. Nothing leaves the machine.

```
CTRL+ALT+Q          toggle
CTRL+ALT+SHIFT+Q    cancel the current take
CTRL+ALT+W          settings: model, microphone, history
dictate status      idle | recording | working
dictate last        the last transcript, even if typing it failed
```

## The indicator

A 76x30 pill under the centre of the screen holding five marks. Five marks
carry every state, so it reads as one object changing rather than a series of
different pictures.

Recording rises and falls with the live level, tallest in the middle, eased at
frame rate between the 50 ms samples so it tracks the voice instead of stepping.
A dead input therefore sits as a flat row of dots rather than animating
regardless -- the difference between an indicator and a decoration.
Transcribing lifts each mark in turn: movement, not brightness, because at this
size a brightness cycle read as the resting dots. Done and failed are still and
differ by shape -- a whole line against one broken in the middle -- since a
Material You palette can hand you an error and a primary that are the same
salmon.

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

`install.sh` hard-links a whisper.cpp server binary and a ggml model into
`~/.local/libexec/nabria` and `~/.local/share/nabria/models`. It originally
took them from an OpenWhispr install; that is gone now, so on a fresh machine
point it at your own copies:

```sh
SERVER_SOURCE=/path/to/whisper-server \
MODEL_SOURCE=/path/to/ggml-large-v3-turbo.bin \
scripts/install.sh
```

Hard links, so a shared 1.6 GB model is not duplicated and removing whatever it
came from cannot break dictation -- the file survives as long as this link does.
Any `.bin` dropped into the model directory afterwards shows up in the settings
window's model picker.

## Configuration

`~/.config/nabria/config.json`. The knobs that matter:

| key | default | |
|---|---|---|
| `language` | `ar` | `auto` re-detects per 30s window and turns room noise into confident English |
| `vocabulary` | Levantine + Latin terms | initial prompt; see *Prompt* below |
| `keep_audio` | `false` | keep every take's WAV, not just the failed ones |
| `silent_notice_after` | `3` | consecutive silent takes before saying the mic is not being heard; `0` never |
| `silence_threshold_dbfs` | `-42` | RMS below this = nothing was said |
| `max_seconds` | `0` | no limit; long takes are not cut |
| `idle_unload_seconds` | `900` | release the model's VRAM when unused |
| `prewarm` | `false` | load the model at login instead of on first use |
| `always_copy` | `false` | clipboard on every take, not just failures |
| `orb_position` | `bottom-center` | `orb_margin` sets the gap |
| `inject` | `auto` | `paste`, then `wtype`, then `ydotool`, then clipboard |

`gpu_select` is applied to the whisper subprocess only. Vulkan takes physical
device 0, which here is the Intel iGPU -- 2.5x slower than realtime on
large-v3-turbo. `10de:2860` puts the RTX 4070 first; 45s of audio goes from
114s to under 4s. Exporting it process-wide would drag the GTK UI onto the
discrete card too, so it is passed to that one subprocess.

## Nothing is lost

Every transcript is appended to `~/.local/share/nabria/history.jsonl` *before*
it is typed. If injection fails the text goes to the clipboard and a
notification says so. `dictate last` prints the most recent one.

If transcription itself fails, the recording is kept in
`~/.local/share/nabria/failed/` rather than deleted -- the audio is the one
thing that cannot be produced again.

A take being transcribed never blocks a new one. Press the key again while the
previous one is still working and recording starts immediately; finished takes
go through a single worker, so they are typed in the order they were spoken.

## Layout

```
fedora/nabria/
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
  link order, so `run.sh` sets `LD_PRELOAD` for the daemon. Without it
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

## Delivery

`wtype` and `ydotool` type the transcript one keystroke at a time. Measured on
585 characters of mixed Arabic and English:

| backend | |
|---|---|
| `wtype` | 2.59s |
| `paste` | 0.02s |

So `auto` pastes first: the text goes on the clipboard and one keystroke sends
it, in constant time whatever the length. Ctrl+V, or Ctrl+Shift+V when the
focused window's class is a terminal.

The clipboard is borrowed, not taken. Its previous contents are captured *with
their MIME type* and put back 1.5s later -- typed, because reading a copied
image back as text and writing that back would replace it with mojibake. The
restore stands down if anything was copied in the meantime, so a newer copy is
never destroyed to return an older one.

## Judging a transcript

You cannot, without the audio. `keep_audio` files every take under
`~/.local/share/nabria/takes/` and puts a play button on its history row, which
is the only way to tell a misheard word from a badly spoken one -- and the only
way to run two models over the *same* utterance. Nothing prunes that directory.

A clean take on this hardware measures around -31 dBFS RMS, -10 dBFS peak, no
clipped samples. Errors on audio like that belong to the model, not the mic.

## Not done yet

- Live partial transcription while speaking.
