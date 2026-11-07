# Local live dictation

The owner requested live text on 2026-11-05. This supersedes the earlier
non-goal for partial transcription. Dictation remains local, with one final
history entry and one paste. The floating surface shows recognition as it
arrives; it does not type provisional words into the focused application.

## Model selection evidence

- [NVIDIA Nemotron 3.5 ASR Streaming 0.6B](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
  documents Arabic and native streaming recognition. The official Q8 GGUF is
  about 742 MB and runs through NVIDIA's
  [NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp/releases/tag/v0.3.0)
  without a PyTorch runtime. Weight size is not total memory consumption.
- [Liquid LFM3 Audio 2B](https://huggingface.co/LiquidAI/LFM3-Audio-2B)
  supports ordinary Arabic ASR, but its LiveASR mode currently supports
  English only. Causal audio encoding and streaming speech output do not
  establish live Arabic transcription support.
- [Cohere Transcribe Arabic](https://huggingface.co/CohereLabs/cohere-transcribe-arabic-07-2026)
  is a dedicated candidate for Arabic dialects and Arabic/English switching.
  Its original weights are larger and require Hugging Face access approval.
  No native live recognition interface was established in this investigation.

These are capability statements from their publishers, not accuracy rankings
on this owner's voice. A trustworthy quality comparison needs human-corrected
references from representative recordings. Existing history is not ground truth.
Whisper remains installed and selectable; do not remove it on the strength of
an unrelated benchmark.

## Preview and final text

The recorder retains at most 30 seconds of PCM for the live reader, independently
of the complete WAV. A reader that falls behind fails visibly in the log rather
than dropping syllables silently. Capture never waits for the network.

Nemotron receives PCM16 over a loopback-only WebSocket. Partial events update
the indicator; completed utterances replace provisional text. The whole text
remains in a wrapping, scrollable label. The text viewport has a fixed height
of approximately four Arabic lines. Every new speech update follows the tail,
including after the user has scrolled back. The translucent surface uses static
shading and a rim, without a per-frame blur pass or compositor configuration.

When recording ends, capture drains through EOF and the live reader sends any
remaining PCM. A bounded commit can then supply the final result. Reuse requires
the exact take path, engine, model/language/vocabulary key and complete byte count,
plus a final completion during the commit phase and its acknowledgement. A
provisional partial is never used. A live connection failure clears stale preview
text and notifies the user while recording and full-file fallback continue. If any condition fails or the deadline expires,
the saved complete WAV is transcribed through the local HTTP API instead.

[Upstream issue 49](https://github.com/NVIDIA/NeMo-Speech.cpp/issues/49) reports
a commit hang, so the fallback remains necessary. The pinned v0.3.0 handler
[emits finish before acknowledging commit](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.3.0/server/http/http_server.cpp#L1293).
Short and long retained recordings matched full-file recognition in local checks
with the selected configuration. This does not establish universal accuracy.
Only the accepted final result enters history and paste, once. The indicator
closes after delivery instead of showing a delayed success card. Cancellation
and stale callbacks cannot change a newer take or prevent final transcription.

## Optional runtime

The streaming engine is an additional local runtime. Its release and model are
pinned by SHA256. It uses `websockets` 15 or newer for the optional live connection;
Whisper continues without that package. Recent Linux distributions package it as
`python3-websockets`; older versions of that package are insufficient.
Model files and the runtime stay under the user's existing Nabria directories.
The full model filename identifies the backend, so selecting an installed
Whisper model returns to the existing engine.

From a checkout, install the optional runtime with:

```sh
python3 scripts/install-streaming.py --activate
```

The optional installer requires a maintained Python with tarfile.data_filter
(Python 3.12 or a security-patched 3.10/3.11). It refuses older unsafe extraction
implementations before downloading. Then restart the daemon while idle. The installer keeps a configuration backup
and preserves all existing Whisper weights. Its Linux x86_64 Vulkan archive and
the model are both checksum-verified before selection. In Settings, selecting a
previously installed Whisper model switches back to that backend.

Local timing results, transcripts and recordings belong outside the repository.
Do not publish timings from the owner's laptop as product specifications.

## Separate vocabulary controls

Nemotron's optional `nemo_vocabulary` is empty by default. It accepts names and
terms for conservative decoder boosting. The settings entry follows the selected
model and keeps Whisper's `vocabulary` separately. Reusing the old dialect prompt
as a boosted lexicon severely damaged a retained recording, so it is never copied
across automatically. This is a measured failure, not a reason to claim either
model is universally more accurate.

The pinned native runtime disables its dynamic ASR batcher and initial warmup.
The default Vulkan warmup aborted with this checkpoint; single-session GPU file
recognition and paced streaming were then exercised successfully. If an engine
aborts, its owned process group is cleaned up before stderr is closed, including
any debugger helpers that inherited that pipe.
