# Input evidence and quiet recording recovery

The November 2026 failure combined two decisions: a whole-take RMS threshold was
used as a silence detector, and recordings judged silent were deleted before a
completed live result could be considered. The log demonstrates a false warning
on a take that later produced text. The content of other already deleted quiet
takes cannot be reconstructed or assumed.

The gate now measures sustained input using 20 ms RMS windows. At least five
windows within a rolling ten-window interval must support the level, so a single
click cannot substitute for a syllable. The strongest such level survives later
pauses. It is acoustic activity, not a semantic speech detector. The initial
0.6-second device transient remains excluded, and the live meter is immediate.
The existing configured threshold is retained. Whole-take RMS remains in logs.
Linux and Windows microphone tests use the same activity statistic.

A nonempty finalized live result can rescue below-threshold input only with the
existing exact take, engine, configuration, finalization and full PCM-byte receipt
checks and sustained nonzero input. Digital silence and isolated clicks never
accept model text. Provisional text only suppresses
a contradictory warning for the current take; it never becomes pasted text.

When nonzero quiet input cannot be confirmed, its original WAV is moved into the
failed directory with a quiet- prefix, and a notice gives its path. Only managed
quiet recordings rotate: at most 20 files or 50 MiB, except that the newest complete
recording is always retained even if it alone exceeds the byte budget. Other failed
audio is untouched. Clearing history removes these WAVs through the existing
failed-audio cleanup. Digital silence does not create recovery files.

Input mute probes run off the GTK loop at most once every two seconds, with only
one in flight per take. Earlier speech and preview text cannot hide a later mute.
The probes never unmute or switch devices. Results
for ended or superseded takes are ignored. Notifications describe the measured
condition rather than claiming that every sample was absent. Capture carries odd
s16 bytes across reads, drains EOF on stop and keeps bounded activity/live buffers.

Verification uses the repository check.sh, focused waveform and state regressions,
and an isolated native PipeWire/Nemotron harness. The harness owns a virtual input,
model child and output directory; it checks quiet speech, pauses, silence, noise,
clicks, repeated capture and engine recovery without pasting into applications or
changing the owner's default source, mute state or clipboard. Machine-local evidence
is under ~/.local/state/codex-runs/nabria-20261107. These tests do not certify every
physical microphone, recognition accuracy or a native Windows desktop build.

The minimal Fedora 44 check also reproduced a GTK import failure caused by the
missing cairo-1.0 typelib. Its provider, gobject-introspection, is now explicit in
the installer hint, RPM requirements and distro setup. Python's cairo module alone
does not supply this metadata. The matrix retains disabled weak dependencies so
the required runtime cannot be supplied accidentally by an unrelated desktop stack.

For the opt-in native harness, set NABRIA_TEST_WAV to one retained WAV or
NABRIA_TEST_AUDIO_DIR to a directory of local WAVs. Fixtures are never shipped.
