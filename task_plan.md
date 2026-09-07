# Native Windows desktop redesign

## Goal
Replace the Linux-looking Windows interface with a familiar standalone Windows
application. Preserve local dictation, tested audio and delivery, Arabic/English
support, and Sbarah attribution. Publish and validate the finished installer.

## Phases
1. Native UI architecture and console audit: complete.
2. Shared backend bridge and Windows WPF frontend: in_progress.
3. Native setup, recording, history, settings and help flows: in_progress.
4. Build, interactive Windows QA, console and regression checks: pending.
5. Publish the validated redesigned release: pending.

## Decisions
- The user's new direction supersedes the earlier shared GTK interface decision.
- Linux retains GTK. Windows gets a WPF/Fluent desktop shell with normal window
  controls and taskbar identity. Python remains the local dictation backend.
- Keep interface strings in i18n.py and export them to the desktop shell.
- Communicate with the child backend over redirected standard pipes, not HTTP.
- Never publish the intermediate 0.5.1 branding-only build as the redesign.
- Verify ordinary Start menu launch without a terminal or developer tools.

## Previous delivery
v0.5.0 was published and tested on Windows 11 Arabic using a dedicated virtual
microphone. Physical microphone hardware, Windows 10 and GPU hardware were not
validated. The logo addition is committed at 4cd9c3e but not yet released.
