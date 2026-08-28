# Design notes

Why the parts are the way they are. Most of this is measurement, and most of
the measurements cost something to get — the numbers are here so nobody has to
pay for them twice.

## The shape of it

One long-lived daemon owns everything: the indicator, the recorder, the
transcription engine. The hotkey does not launch a process, it writes one line
to a Unix socket. That is why a keypress feels instant, and why two presses can
never race a second copy of the application into existence.

Recording and transcription are independent. A new take starts immediately even
while the previous one is still being transcribed; finished takes pass through
a single worker thread, so they are typed in the order they were spoken. A
keypress is never dropped for being busy.

The settings window lives *inside* the daemon. A separate process could write
`config.json`, but it could not make the daemon re-read it or tear down the
loaded engine.

## The indicator

A layer-shell surface on the OVERLAY layer, which sits above every window by
protocol, with keyboard interactivity set to NONE so it can never steal focus
from what you are dictating into.

This is the whole reason the project exists. The tool it replaced put its
overlay on XWayland, so the compositor stacked it against ordinary windows: it
fell behind anything fullscreen and often never appeared. No window rule
reaches that.

Five marks carry every state, so it reads as one object changing rather than a
series of different pictures:

- **recording** — the marks follow the live level, tallest in the middle. A
  dead input therefore sits as a flat row of dots instead of animating
  regardless, which is the difference between an indicator and a decoration.
- **transcribing** — a wave lifts each mark in turn. Movement, not brightness:
  at this size a brightness cycle read as the resting row of dots, the one
  state it must never be confused with.
- **done / failed** — still, told apart by shape, a whole line against one
  broken in the middle. Colour cannot carry it, because a generated palette can
  hand you an error and a primary that are the same salmon.

### Three traps

- `gtk4-layer-shell` must load before `libwayland-client`. Python cannot
  control link order, so `run.sh` sets `LD_PRELOAD`. Without it
  `is_supported()` returns False and the window silently degrades.
- The desktop theme writes `window { background: @window_bg_color; }` into
  `~/.config/gtk-4.0/gtk.css` at `PRIORITY_USER`. Our CSS must register above
  that or the indicator paints an opaque rectangle.
- `Gtk.Application.run([])` skips `activate` entirely. Pass `sys.argv[:1]`.

## Choosing a device

Left alone, ggml uses every discrete *and integrated* GPU it can see. Measured
on 11 s of audio with `large-v3-turbo`:

| | |
|---|---|
| discrete (RTX 4070, Vulkan) | 0.32 s |
| CPU, 16 threads | 21.4 s |
| integrated (Intel, Vulkan) | 63.5 s, then `vk::Queue::submit: ErrorDeviceLost` |
| `base` on CPU, 8 threads | 0.9 s |
| `base` on CPU, 4 threads | 1.4 s |

An integrated GPU is not a lesser accelerator here — it is three times worse
than the CPU it would replace, and it crashes. So the policy is: a discrete GPU
if there is one, otherwise `-ng` and the CPU. Getting this backwards on the
commonest laptop there is — Intel integrated, no discrete card — means a tool
that does not work.

Selection uses `GGML_VK_VISIBLE_DEVICES`, not `MESA_VK_DEVICE_SELECT`. The Mesa
variable is a loader layer that silently does nothing when the layer is not
installed, which is a poor thing to depend on for correctness. The ggml
variable is read by the engine itself and takes a raw Vulkan device index, so
naming one device leaves ggml with a list of one.

That index can only come from Vulkan, so devices are enumerated through
`libvulkan` with ctypes rather than guessed from sysfs PCI ids. It runs in a
subprocess: creating an instance loads the graphics drivers into whichever
process asks, and neither a driver that aborts nor a handle to the discrete
card belongs in the GTK daemon.

## The engine

whisper.cpp, built from a pinned tag in `engine/VERSION`, static, with
`GGML_NATIVE=OFF` and Vulkan on. One binary covers every machine: it falls back
to the CPU by itself where no Vulkan driver exists, and still selects AVX2/FMA
paths through ggml's runtime dispatch — a `-march=native` build measured no
faster (21.4 s against 21.9 s).

It is spoken to over HTTP on a loopback port it binds only after the model has
loaded, so a successful connection is a sufficient readiness check. The model
is unloaded after `idle_unload_seconds` to give back its VRAM, and reloaded
while you are still speaking, so the cost is usually invisible.

## Levels, and the 0.6-second warm-up

Opening the ALSA capture device pops. The first fraction of a second comes back
tens of dB above the room — about −32 dBFS — which is loud enough on its own to
carry a take of pure silence past the RMS gate and hand Whisper noise to
hallucinate over.

So exactly 0.6 s is excluded from the level *statistics*, trimmed inside the
chunk that crosses the boundary rather than by dropping whole chunks, which
would round up to 0.768 s.

The **live meter is never gated on the warm-up**. The indicator reads it, and
holding it at silence through the opening of a take would draw a healthy
microphone exactly like a dead one for the moment the user is watching to see
whether it heard them.

A take shorter than the warm-up has no measured level at all. It is neither
silence nor evidence about the microphone, and must be its own outcome, or
stray double-presses accuse a healthy input.

The gate reads the RMS, not the peak: one keyboard click pushes the peak well
above any threshold while the take as a whole is still room tone.

### Why the gate has to exist

Two seconds of digital silence — actual zeroes — came back from `base` as
«نقف بعضك». The hallucination list holds the stock phrases Whisper repeats
("Thanks for watching", "شكرا للمشاهدة") and could not reasonably be extended
to cover that. There is no filter that reliably separates invented words from
real ones after the fact, so the protection must be upstream: audio that quiet
is never sent at all.

## Getting the text into the window

Three mechanisms, tried in order. `wtype` and `ydotool` type one character at a
time — 2.59 s for 585 characters, against 0.02 s for a paste, with every
keystroke a round trip through the compositor. So paste comes first: the text
goes on the clipboard and one keystroke sends it, in constant time whatever the
length.

The clipboard is **borrowed**, not taken. Its previous contents are captured
*with their MIME type* and put back 1.5 s later — typed, because reading a
copied image back as text and writing that back would replace it with mojibake.
The restore stands down if anything was copied in the meantime, so a newer copy
is never destroyed to return an older one.

Terminals need Ctrl+Shift+V. Asking what has focus has no cross-desktop answer,
so Hyprland, sway and niri are each asked in their own language and the list
ends where knowledge does. Where nothing can say, the answer is Ctrl+V — right
everywhere except a terminal, and the safer of the two guesses.

## Arabic

**Tell it the language.** Auto-detection runs per 30-second window, and a
window of near-silence is what it most often gets wrong — turning room tone
into confident gibberish in a language nobody was speaking. The setup wizard
asks, preselecting from the locale; `auto` remains available and remains the
worst of the three.

The `vocabulary` initial prompt is not just a glossary, and choosing Arabic in
the wizard ships one — `config.LEVANTINE_PROMPT`. It is only ever written into
an empty setting, so it cannot overwrite something hand-written. Measured over
one retained take, same model, three runs differing only in the prompt:

| prompt | |
|---|---|
| formal Arabic + Latin terms | طيب **هلا** … أبحكي والتطبيق **شغل فأش وف** |
| none at all | طيب **هلأ** … أبحكي والتطبيق **شغل، فأشوف** |
| + Levantine function words | طيب **هلأ** … أبحكي، والتطبيق **شغال، فأشوف** |

An all-MSA prompt was **worse than no prompt**: it pulls spoken dialect toward
the formal register the prompt is written in. If a prompt ships for Arabic it
must carry dialect function words. Keep it short — a long prompt starts leaking
into the transcript.

## Why there is no Flatpak

Compositors implementing `wp_security_context_v1` withhold privileged Wayland
protocols from sandboxed clients. Measured with `WAYLAND_DEBUG=1` inside the
sandbox against two unrelated Flatpak applications: 123 globals visible where
the host sees 154. Three of the 31 withheld are the ones this is built on:

| protocol | needed for |
|---|---|
| `zwlr_layer_shell_v1` | the indicator |
| `zwp_virtual_keyboard_manager_v1` | `wtype` |
| `zwlr_data_control_manager_v1` | reading the clipboard back |

No Flatseal permission reaches this — it is the compositor refusing, which is
the entire point of the protocol. Nabria is a host application.

## The shortcut, and the app id

`org.freedesktop.portal.GlobalShortcuts` is the standard way to claim a hotkey
on Wayland, and it is implemented here by the Hyprland, KDE and GNOME backends.
Nabria registers `toggle` and `cancel` through it at startup.

Getting that to work turned on something undocumented enough to be worth
recording. The portal refuses to bind anything for a caller it cannot name --
`org.freedesktop.portal.Error.NotAllowed: An app id is required` -- and for a
host (non-sandboxed) application it derives that name from the **systemd unit**,
following the `app-<app-id>-<...>` convention. Measured directly:

| unit | result |
|---|---|
| `app-com.sbarah.Nabria-probe.scope` | both shortcuts bound |
| `nabria-control-probe.scope` | `An app id is required` |

Same binary, same everything else. So the unit is called
`app-com.sbarah.Nabria.service`, with `nabria.service` left as a symlink so the
familiar `systemctl --user restart nabria` still works. **Renaming that file
breaks portal shortcuts**, and it breaks them quietly -- the daemon starts
fine and the key simply never fires.

On Hyprland this registers the shortcut with the compositor but you still bind
a key to it (`bind = CTRL ALT, Q, global, com.sbarah.Nabria:toggle`). On KDE and
GNOME the desktop's own shortcut editor picks it up, which is the case this is
really for.

All of it is additive. Every failure -- no portal, no session bus, a backend
that refuses -- is a log line, and the manually bound key works regardless. A
daemon that would not start because a portal was unhappy would be a much worse
tool than one whose shortcut has to be bound by hand.

## Judging a transcript

You cannot, without the audio. `keep_audio` files every take and puts a play
button on its history row, which is the only way to tell a misheard word from a
badly spoken one — and the only way to run two models over the *same*
utterance. Nothing prunes that directory.

A clean take measures around −31 dBFS RMS, −10 dBFS peak, no clipped samples.
Errors on audio like that belong to the model, not the microphone.
