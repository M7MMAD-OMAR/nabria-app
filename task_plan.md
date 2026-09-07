# Windows delivery

## Goal
Review Nabria as a whole, preserve local dictation and one shared GTK UI,
and deliver a tested Windows EXE through GitHub.

## Phases
1. Architecture and baseline review: complete.
2. Windows platform adapters and shared integration: in_progress.
3. Windows runtime, installer and automated checks: in_progress.
4. Execute Linux and Windows checks, repair failures: in_progress.
5. Publish a tested Windows release with exact validation limits: pending.

## Next Step
Inspect native Windows build results and fix observed failures before publishing.

## Decisions
- Scope is this Nabria repository, following the concrete Windows objective.
- Preserve one GTK interface and one transcription state machine.
- Do not claim physical microphone or desktop tests without execution evidence.

## Errors
None recorded yet.

## Build failures
- Windows run 34109182876: treated engine/VERSION as a bare tag, but it is a
  shell configuration file. Parse WHISPER_CPP_VERSION and normalize CRLF.
- Windows run 34109470772: Vulkan CMake requires SPIRV-Headers as a separate
  development package. Add the UCRT package to the build environment.
- Windows run 34109858177: engine and runtime assembly succeeded. Imports,
  engine startup, IPC, GTK in both languages, hotkey registration and Unicode
  clipboard passed. Native EDIT remained empty after SendInput; add foreground
  diagnostics and investigate actual delivery before claiming input works.
