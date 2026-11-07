# Dictation quality

Separate capture faults from recognition faults before changing models. Read
`nabria.log`: each finished take reports its RMS, peak and capture warnings.
Digital silence cannot be repaired by a larger speech model.

## Preserve the input and output

The Linux recorder must drain the capture pipe through EOF after stopping the
child. A stop flag can discard audio already buffered in the pipe, including
the end of a word. `test_stop_drains_audio_already_written_by_the_child`
reproduces a reader that was not scheduled until the child had exited.

whisper.cpp v1.9.3 inserts a newline after every segment in its flattened
`text` response. A segment can end inside a word. Use `verbose_json`, concatenate
the original segment texts without inserting separators, then normalise
whitespace. Send `no_language_probabilities=true` to avoid an extra language
detection pass. The segment spacing tests cover Arabic fragments
and genuine English word boundaries.

## Tune decoding before adding a model

For recurring known spelling errors, `corrections` in `config.json` accepts
an object of exact source words or phrases and their replacements. For example,
`{"git hub": "GitHub"}`. Restart the daemon after changing these rules.
They run locally before history and paste, with no additional model call.
Matching is case-sensitive, respects word boundaries and does not cascade.
The longest matching phrase wins. Empty or invalid entries are ignored.
These rules do not infer grammar or reorder unfamiliar sentences. Only add
corrections whose intended spelling is known; broad letter substitutions can
damage valid words. Rules are compiled once when the engine wrapper is created.

`beam_size` in `config.json` controls how many decoding alternatives the engine
examines. The default is 1; requests are bounded to 1 through 8. A value of 5
recovered both languages in a local concatenated English/Arabic test where
greedy decoding omitted speech and rendered English in Arabic. This is a
regression probe, not a representative accuracy benchmark or a guarantee.
Wider search costs computation. Compare it on the intended machine before
changing a deployment, and restart the daemon after editing its configuration.

Keep a small local evaluation set with the exact intended transcript for each
recording: Arabic dialect, English, natural switching between them, short takes
and long takes. Include names and technical terms. Compare the same recordings
before and after each change, checking omissions, substitutions, unwanted
translation and latency. Synthetic concatenation alone is insufficient.

Transcripts are not ground truth, and saving history does not train Whisper.
Corrections can supply evaluation references and a short vocabulary prompt.
Do not automatically feed uncorrected history back into the prompt or train on
it: this would reinforce recognition errors. Keep personal recordings and
reference transcripts outside the repository.

The current Turbo model trades some accuracy for speed. A larger model, VAD or
a text correction model must earn its additional cost on these same references.
VAD needs separate checks for clipped syllables; a text model must not rewrite
the speaker's meaning. Neither is necessary to fix segment separators.
Silero VAD is a lightweight candidate already supported by whisper.cpp, but
detects speech boundaries rather than correcting recognition. Evaluate padding
and overlap on quiet speech before enabling it. It is not enabled by this fix.

Sources: [whisper.cpp server](https://github.com/ggml-org/whisper.cpp/blob/v1.9.3/examples/server/server.cpp)
and [Turbo model card](https://huggingface.co/openai/whisper-large-v3-turbo),
plus [Silero VAD](https://github.com/snakers4/silero-vad).
