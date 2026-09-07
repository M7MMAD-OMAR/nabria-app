# Windows delivery

## Goal
Review Nabria as a whole, preserve local dictation and one shared GTK UI,
and deliver a tested Windows EXE through GitHub.

## Phases
1. Architecture and baseline review: complete.
2. Windows platform adapters and shared integration: complete.
3. Windows runtime, installer and automated checks: complete.
4. Execute Linux and Windows checks, repair failures: complete.
5. Publish a tested Windows release with exact validation limits: complete.

## Delivery
Published v0.5.0 as a prerelease, with the tested installer, SHA256SUMS and
validation scope. The latest stable release remains v0.4.6.

## Decisions
- Scope is this Nabria repository, following the concrete Windows objective.
- Preserve one GTK interface and one transcription state machine.
- Do not claim physical microphone or desktop tests without execution evidence.

## Errors
Observed failures and their repairs are recorded below and in progress.md.

## Build failures
- Windows run 34109182876: treated engine/VERSION as a bare tag, but it is a
  shell configuration file. Parse WHISPER_CPP_VERSION and normalize CRLF.
- Windows run 34109470772: Vulkan CMake requires SPIRV-Headers as a separate
  development package. Add the UCRT package to the build environment.
- Windows run 34109858177: engine and runtime assembly succeeded. Imports,
  engine startup, IPC, GTK in both languages, hotkey registration and Unicode
  clipboard passed. Native EDIT remained empty after SendInput; add foreground
  diagnostics and investigate actual delivery before claiming input works.
- Windows run 34111090255: 68 native unit tests passed. Foreground diagnostics
  confirmed SetForegroundWindow was denied by the CI desktop. CI explicitly
  reports native paste as not tested; an interactive VM pass remains required.
- Windows run 34112340248: inference timed out after model startup. Add engine
  diagnostics and use two test threads. Also eliminate system proxy handling
  from loopback audio requests, backed by a real local HTTP regression test.
