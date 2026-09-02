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

Left alone, ggml uses every discrete *and integrated* GPU it can see. That is
the wrong default here, and the measurement that settled it ran the same audio
through the largest model on a discrete card, on the CPU, and on an integrated
GPU.

**The times are deliberately not written down.** They describe one laptop, and
a number from one laptop kept in a repository is a number that ends up quoted
as though it described the software. What they established is ordinal, and
that part does generalise:

- the discrete card was far ahead of everything else
- the CPU was usable with a small model and not with the largest
- the integrated GPU came *last* — behind the CPU it would have been standing
  in for — and then died with `vk::Queue::submit: ErrorDeviceLost`

So an integrated GPU is not a lesser accelerator here, it is a worse answer
than no accelerator, and the policy is: a discrete GPU if there is one,
otherwise `-ng` and the CPU. Getting this backwards on the commonest laptop
there is — integrated graphics, no discrete card — means a tool that does not
work. `gpu_select: any` is there for anyone who wants to test the claim on
their own hardware, which is the only place it can honestly be tested.

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
paths through ggml's runtime dispatch — a `-march=native` build measured
no faster, within noise, so the portable build costs nothing.

It is spoken to over HTTP on a loopback port it binds only after the model has
loaded, so a successful connection is a sufficient readiness check. The model
is unloaded after `idle_unload_seconds` to give back its VRAM, and reloaded
while you are still speaking, so the cost is usually invisible.

### When the GPU will not start

A card that cannot be used is not a reason to lose a take. Measured here: the
discrete GPU was busy, `whisper-server` aborted inside `whisper_model_load` on
an allocation that came back null, and the recording went to `failed/` with
`whisper server exited with code -6` as the only account of it anywhere.

So a GPU start that dies is retried once on the CPU, and the decision is then
kept for the rest of the session. Retrying the card per take would pay for the
crash and its startup timeout again to reach the same answer; a daemon restart
tries it afresh, which is right, because the usual cause is something else on
the machine holding the memory and that clears.

The fallback is announced, once. Keeping the take is the point, but every take
afterwards is slower than the user has any reason to expect, and a tool that
quietly halves its own speed is the same misdiagnosis this log exists to
prevent, one layer up.

The engine's stderr is **kept and drained**, not sent to `/dev/null`. `-6` is
the same answer for a busy GPU, a missing driver and an unreadable model; the
engine says which, and discarding that left the log recording a failure it
could not explain. Drained on a thread for the same reason `pw-record`'s is:
the pipe holds 64 KiB and the engine writes to it for as long as it runs.

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

### Saying so in time to matter

The gate discards a silent take, and after three in a row the daemon says why.
That is the right shape for a habit going wrong: a microphone pinned to a dead
input is silent every time, while pressing the key and then not speaking is
ordinary and must stay quiet.

It is the wrong shape for the case that actually costs something. Somebody who
mutes their input, talks for a minute into a meeting and then stops has already
lost the minute by the time any of this can be said, and on the count of one,
not three, so the notice does not even fire.

So the same judgement is made **while the take is still running**.
`recorder.unheard` reads the take's length and its RMS under one lock, and
returns both alongside its verdict, so the caller never re-reads them at a
later instant: one chunk of speech arriving in that gap is enough for the
notification to claim nothing rose above the gate while the log line beside it
reports a level that did. After `silence_warning_seconds` (12 s) with nothing
above the gate, the daemon says so once. Twelve, because the cost of being
wrong is an interruption while somebody is talking: long enough that gathering
a thought or waiting for someone else to finish never reaches it, short enough
that the sentence is still worth repeating. It cannot fire inside the warm-up
whatever it is set to, for the reason above: an unmeasured take is not
evidence.

The two never both speak about one recording. A take that carried the live
warning is flagged, and the finished-take notice stands down rather than
repeating it. That take also does not advance the consecutive-silence count:
the count triggers on equality, so spending it on a suppressed notice walks
the run past the threshold and the notice can never fire again. Measured, a
mid-take warning on exactly the third take silenced all eight that followed.

Both name the device, and both say **muted** only when `wpctl` reports it
muted. That is the one cause this tool can name outright rather than describe,
and naming it turns a symptom into an instruction, but only when it is known.
Where `wpctl` is absent or PipeWire is wedged the mute state stays unknown and
the weaker, true sentence is sent instead, because "your microphone is muted"
sends the user to fix something that may be fine.

### Why the gate has to exist

Two seconds of digital silence — actual zeroes — came back from `base` as
«نقف بعضك». The hallucination list holds the stock phrases Whisper repeats
("Thanks for watching", "شكرا للمشاهدة") and could not reasonably be extended
to cover that. There is no filter that reliably separates invented words from
real ones after the fact, so the protection must be upstream: audio that quiet
is never sent at all.

## Getting the text into the window

Three mechanisms, tried in order. `wtype` and `ydotool` type one character at a
time, each keystroke a round trip through the compositor, so a long transcript
crawls onto the screen character by character. So paste comes first: the text
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

### Sending the paste key, and why the order is inverted

The paste keystroke goes through **ydotool first and wtype second**, the
reverse of the order used for typing. Measured into a real focused text entry
on Hyprland 0.56.2: `wtype -M ctrl -k v -m ctrl` landed **0 times out of 15**
while exiting 0 every time, and ydotool landed 12 out of 12. The daemon was
therefore logging `typed via paste` for transcripts that never arrived
anywhere, which is precisely the misdiagnosis the log exists to prevent, one
layer up. A sender that reports success for work it did not do goes last.

wtype stays as the fallback because it needs no daemon: on a machine with no
`ydotoold` it is the only thing that can send the key at all.

A 120 ms settle precedes the key. The clipboard offer and the keystroke are
two separate trips through the compositor, and firing immediately after
`wl-copy` measured 11 in 12 against 12 in 12 with the pause: the target has to
have processed the offer before the key arrives, or it pastes what was there
before.

### XWayland is served by typing, not by pasting

An XWayland client reads the **X11** selection, which the compositor bridges
from Wayland. Where that bridge is broken the paste cannot work by any amount
of retrying: measured here, `wl-copy` followed by `xclip -o` returned nothing
forty times out of forty while `wl-paste` read the same value back fine, so a
paste into such a window inserts whatever X11 happened to hold before.

So `_paste` asks the compositor whether the focused window is XWayland and
refuses if it is, which drops the take through to `wtype`/`ydotool`. Those
type the characters and never touch the clipboard, so they are unaffected by
the bridge. Only Hyprland is asked, and anything that cannot answer is treated
as native: a wrong True would send every dictation down the slow typing path
on every other desktop.

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

## Testing

`scripts/check.sh` is the check; CI calls the same script. That ordering is
deliberate — a project whose correctness is only knowable by pushing to a
service is a project you cannot work on offline, and this one was written
largely through a tunnel that makes GitHub slow.

The part that earns its keep is the distribution matrix. Every packaging bug
found so far was found by running the installer in a clean container and none
by reading it: `LD_PRELOAD` missing the multiarch path Debian uses, a typelib
packaged separately from its library, `pw-record` living in a different package
on Arch. Reasoning about package names does not work; running them does.

The engine is published as a prebuilt binary built in an **Ubuntu 22.04**
container. glibc is forward compatible and not backward compatible, so a binary
linked against a new one refuses to start on an older distribution — and the
people most likely to want a prebuilt engine are the least likely to be on the
newest release. `scripts/release-engine.sh` does that build locally rather than
in CI: it happens when `engine/VERSION` changes, which is rarely, and it is
better done where the result can be looked at.

The installer verifies the download against `engine/CHECKSUMS`, which is
committed here. A hash published beside the file it describes only proves the
bytes arrived intact; one in the repository proves they are the bytes we built.
It then runs `--help` on it, because a binary built against a newer glibc
downloads and verifies perfectly and then refuses to start.

## Judging a transcript

You cannot, without the audio. `keep_audio` files every take and puts a play
button on its history row, which is the only way to tell a misheard word from a
badly spoken one — and the only way to run two models over the *same*
utterance. Nothing prunes that directory.

A clean take measures around −31 dBFS RMS, −10 dBFS peak, no clipped samples.
Errors on audio like that belong to the model, not the microphone.
