# dictate

Local voice dictation for Hyprland. Press the key, speak, press it again, the
text is typed into whatever window has focus. Nothing leaves the machine.

```
CTRL+ALT+Q          toggle
CTRL+ALT+SHIFT+Q    cancel the current take
dictate status      idle | recording | working
dictate last        the last transcript, even if typing it failed
```

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
| `language` | `auto` | per-take detection |
| `vocabulary` | Arabic + Latin terms | initial prompt; biases spelling of technical words |
| `silence_threshold_dbfs` | `-42` | RMS below this = nothing was said |
| `max_seconds` | `0` | no limit; long takes are not cut |
| `idle_unload_seconds` | `900` | release the model's VRAM when unused |
| `prewarm` | `false` | load the model at login instead of on first use |
| `always_copy` | `false` | clipboard on every take, not just failures |
| `orb_position` | `bottom-right` | `orb_margin` sets the gap |
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

## Layout

```
fedora/dictate/
  app.py       daemon: control socket, state machine, GTK main loop
  orb.py       the layer-shell indicator
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

## Not done yet

- Live partial transcription while speaking.
