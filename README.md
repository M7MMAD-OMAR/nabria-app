# Nabria

**Just talk.** Press a key, say what you mean, press it again. The words appear
in whatever you were typing into.

*نَبْرة — the tone of a voice.*

Everything happens on your machine. No account, no cloud, nothing uploaded, and
it works with the network off.

Arabic is first-class, including spoken dialect — that is what it was built
for, and it is the thing most dictation tools are worst at. English and the
other 90-odd languages Whisper knows work too.

## Install

Linux, Wayland. You need `git`, `cmake` and a C++ compiler for the one-time
engine build.

```sh
git clone https://github.com/M7MMAD-OMAR/nabria-app.git
cd nabria-app
scripts/install.sh
systemctl --user enable --now nabria
```

The installer tells you what is missing in your own distribution's package
names, builds the transcription engine from source, and downloads the model
that suits your hardware. Re-run it any time; it repairs rather than reinstalls.

Then bind a key. Wayland gives no way for an application to claim a shortcut
for itself, so this part is yours — the installer prints the exact line for
your compositor. On Hyprland:

```
bind = CTRL ALT, Q, exec, nabria toggle
```

## Using it

```
nabria toggle      start and stop dictating
nabria cancel      throw the current take away
nabria settings    model, microphone, transcript history
nabria last        print the last transcript
nabria status      idle | recording | working
```

While you speak, a small pill sits under the middle of the screen with five
marks in it. They rise and fall with your voice, so a microphone that has gone
dead shows a flat row of dots rather than animating regardless. A wave moves
through them while it transcribes.

Nothing is lost. Every transcript is written to disk *before* it is typed. If
typing fails the text is on your clipboard and a notification says so. If
transcription fails the recording is kept, because the audio is the one thing
that cannot be produced again.

## Which model

Chosen for you at setup, from your hardware. It matters more than it sounds:

| | 11 seconds of audio |
|---|---|
| `large-v3-turbo` on a discrete GPU | 0.3 s |
| `large-v3-turbo` on a CPU | 21 s — slower than speaking |
| `base` on a CPU | 0.9 s |

So `large-v3-turbo` if you have a discrete GPU, `base` otherwise. Change it any
time in settings; anything you drop into the model directory shows up there.

An integrated GPU is *not* used even when present. Measured on the same audio
it took 63 s and then crashed the driver — three times worse than the CPU it
would be standing in for.

## What it is not

Live subtitles, note-taking, summarising, translation, an assistant, a tray
menu, cloud sync, a plugin system. There is one key and it types what you said.

## Known limits

- **Wayland only.** X11 is not supported and is not planned.
- **GNOME**, and anywhere `gtk4-layer-shell` is missing, gets an ordinary
  window for the indicator instead of one that floats above everything. It
  still works; it can be covered. Ubuntu 24.04 does not package the GTK4 layer
  shell at all.
- **Flatpak is not possible.** Compositors refuse privileged Wayland protocols
  to sandboxed clients, and the three this depends on — layer shell, virtual
  keyboard, clipboard reading — are all withheld. Measured, not assumed; see
  [docs/DESIGN.md](docs/DESIGN.md).
- **The shortcut is manual.** There is no cross-desktop way to register a
  global hotkey on Wayland yet.

## Contributing

`docs/DESIGN.md` explains why the parts are the way they are, and most of it is
measurements that took a while to get. Read it before changing thresholds.

```sh
python3 -m pytest        # 118 tests
scripts/build-engine.sh  # rebuild the engine from a pinned whisper.cpp tag
```

## Licence

MIT. It builds [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (MIT) and
downloads [Whisper models](https://huggingface.co/ggerganov/whisper.cpp) (MIT).
