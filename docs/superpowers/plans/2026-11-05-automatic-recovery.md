# Automatic Recovery Implementation Plan

> **For agentic workers:** Implement this plan task-by-task using available execution tools. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover automatically from the observed Python upgrade failure and transient local inference failures without losing or delivering a take twice.

**Architecture:** Cache the IDNA codec before it can disappear under a running interpreter. Watch runtime identity changes and refresh the daemon only after its work and open windows have finished. Retry local inference once after stopping a broken engine, with the existing audio file and existing post-transcription history/delivery path.

**Tech Stack:** Python, GTK/GLib, whisper.cpp, pytest, systemd on Linux.

**Spec:** The owner's request in this session: make the application repair itself automatically after the observed `LookupError: unknown encoding: idna` following an in-place Python update.

## Global Constraints

- One daemon owns recording and the engine; queued takes retain order.
- Audio never leaves loopback; system proxy settings remain ignored.
- Existing uncommitted changes must be preserved.
- Windows keeps its existing desktop lifecycle.
- No retry after transcript history or injection; persistent failures retain their WAV.
- No universal promise to repair missing models, broken drivers, or all future faults.

### Task 1: Safe runtime refresh

**Files:** Create `src/nabria/runtime.py`, `tests/test_runtime.py`; modify `src/nabria/app.py`.

**Interfaces:** Runtime guard exposes an idle-polled change check and a bounded fresh-interpreter preflight. The daemon owns refresh scheduling, orderly shutdown, and process replacement.

- [x] Add failing tests for executable replacement, temporarily missing runtime files, failed preflight, recording/jobs/open-window deferral, and original process arguments.
- [x] Implement a Linux-only runtime guard using executable and selected standard-library fingerprints. Defer while work or windows remain. Verify a replacement interpreter before orderly cleanup and exec.
- [x] Run `scripts/check.sh --quick`; inspect all failures before expanding verification.

### Task 2: Preserve a take across transient inference failure

**Files:** Modify `src/nabria/whisper.py`, `tests/test_whisper.py`.

**Interfaces:** `WhisperServer.transcribe(wav_path: Path) -> str` remains unchanged. Recovery happens before the caller appends history or delivers text.

- [x] Add regressions using a local HTTP server that disconnects the first request, disconnects every request, and returns a client HTTP error. Assert exactly 2, 2, and 1 requests respectively, identical WAV body on retries, and one engine stop only on recoverable faults.
- [x] Add a fresh-process regression: import the transcriber, hide `encodings.idna` from later imports, and call `socket.getaddrinfo("127.0.0.1", 1)`. It must succeed after early codec registration.
- [x] Hold the engine lock through inference so idle unloading cannot terminate an active request. Restart once on transport/server failure. If the GPU process crashed, use the existing session CPU fallback.
- [x] Run the application checks through the external checkpoint workflow. Attempt the full `scripts/check.sh` matrix separately; unavailable distribution images or dependency downloads are an explicit environment limitation, not a passing result. Obtain independent review against the final source hash, repair findings, and accept only fresh passing evidence.
- [x] Refresh graft, restart the installed daemon, verify service/shortcut readiness, and transcribe a retained real recording without injecting it into the owner's focused window.

## Acceptance and limits

The runtime-refresh tests cover deferred restart, bounded preflight, and pending or executing clipboard restoration. Clipboard timers remain tracked until the callback finishes; a fixed idle delay alone is insufficient when a clipboard command blocks. Local HTTP failures verify retry limits, loopback privacy, and unchanged audio. The existing suite covers retention and delivery behavior. A real saved recording verifies the installed engine path. Physical microphone input and focus-sensitive paste are not synthesized into the owner's current application.

## Evidence

- Independent final specification review: pass, source hash `8f0b6ed764a0806b2aeafa0adfeb0b97fb06c6174aea99eb691407ba84f12d3e`.
- Quick check: 344 passed, 1 skipped.
- A real retained recording was transcribed after a controlled engine process failure. Recovery returned 32 characters, with no history or clipboard writes.
- Fresh-interpreter codec/socket preflight was exercised against a changed runtime fingerprint.
- The running daemon was refreshed and its portal shortcuts rebound successfully.
- Full distribution matrix: attempted, then timed out after 600 seconds while container images and packages were downloading. This is not a passing distribution result. Owned test containers were stopped.
- Final source-bound application gate and independent review accepted. Test result: 344 passed, 1 skipped.
