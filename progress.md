# Progress

## 2026-09-07
Read project and workstation instructions, Windows plan, daemon and platform dependencies.
No application files changed yet.

Baseline: scripts/check.sh --quick with system Python: 293 passed, 2 skipped.
Implemented initial Windows adapters, portable paths, launcher and installer.
Added installed-runtime checks for imports, engine startup, GTK, hotkey registration,
single-instance locking, named-pipe control and Unicode clipboard.
Next: execute the Windows workflow and fix its observed failures.

Windows build dispatched: GitHub Actions run 34109182876.
Linux regression check after initial port: 293 passed, 2 skipped.
Found and fixed an existing capture lifecycle bug: stop was deferred behind older
transcriptions. Added a regression asserting capture ends before dequeue.
Added a portable Windows path import test and started full distribution checks.
Second Windows run: 34109470772. First failed at engine tag parsing, fixed.
Added a real Win32 EDIT paste/restore check and a checksum-verified base model
transcription of the upstream speech sample to the installed-runtime suite.
Ubuntu container passed: 246 tests, 8 skips. Debian and Fedora checks continue.
Documented the implemented Windows architecture and explicit release gates.
Latest quick check: 297 passed, 2 skipped.
Moved Win32 hotkey handling onto its own native message queue so GDK cannot
consume WM_HOTKEY first; self-test verifies dispatch, not just registration.
Added Windows UI language detection when Unix locale variables are absent.
Debian container passed: 248 tests, 8 skips. Fedora is running.
A GitHub API status request briefly failed to connect; no build conclusion
was inferred from that network error.
Windows run 34109858177 reached native runtime checks. Everything through
Unicode clipboard passed; actual SendInput paste remained empty.
Latest remote run is 34111090255, with foreground diagnostics and engine cache.
Prepared an isolated local Windows VM test seed and started an official Microsoft
Windows 11 Enterprise evaluation ISO download. No VM has been started yet.
Local DNS is failing; a temporary per-request DoH lookup restores authenticated
GitHub API and Git SSH access without modifying workstation networking.
Full Linux check completed successfully: Ubuntu, Debian and Fedora all passed.
Added Start-menu behavior for an already-configured app that was not running:
open its settings, while explicit daemon/autostart stays in the background.
Stopped the evaluation download after finding an existing Windows 11 Arabic ISO.
Created a separate 4 GiB, 2 vCPU QEMU VM and virtual audio input. The existing
personal VM remains stopped and untouched. Test VM files are under ignored build/.
