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
Windows 11 Pro Arabic installation is running in the isolated VM, reached 77%.
Per-task SOCKS transport restores the regular gh CLI while preserving TLS and
leaving workstation DNS unchanged. Latest Windows workflow run: 34112340248.
The isolated Windows VM reached OOBE. Its virtual microphone is confirmed to
read the dedicated test sink monitor; physical workstation inputs remain suspended.
Prepared a serial command channel for unattended tests inside the interactive guest.
Latest quick check: 298 passed, 2 skipped. A local HTTP regression verifies
that audio requests bypass configured proxies and stay on loopback.
The Windows inference timeout now records engine diagnostics. The installer
will be built before runtime checks, allowing isolated VM diagnosis even if
CI validation fails; failed builds are clearly labeled debug artifacts.
Windows run 34113662382 passed: real base-model speech inference, native unit
checks, silent installation and removal. Native paste remains explicitly skipped
on CI because the runner denies focus. Local Windows 11 Arabic desktop is ready;
a Limited interactive task was verified at medium integrity for realistic tests.
Interactive Windows testing found and fixed clipboard restoration failure:
OleGetClipboard returned a live wrapper invalidated by replacing clipboard data.
The adapter now duplicates each format before borrowing it, including GDI data,
and releases untransferred handles. The native EDIT test now passes actual Arabic
and English paste plus restoration. Added incremental self-test reports and crash
traces so native failures cannot masquerade as an unexplained missing report.
Interactive dictation passed end to end: the dedicated virtual input reached
WASAPI, local inference produced the speech sample, history retained it, and the
focused editor received the text. Arabic input layout 0x0401 was verified before
record/cancel hotkeys; silence produced no transcript. A DIB image and a newer
clipboard copy both survived the native clipboard checks. Corrected Arabic
start alignment against GTK's actual layout behavior, with a rendered-position
regression test. Repositioning the mapped indicator fixed its Windows corner.
Windows run 34118430060 passed, but a subsequent explicit Arabic argument test
exposed ANSI conversion in the engine. Added a UTF-8 engine manifest and expanded
native validation to cover non-ASCII installation and model paths before release.
The UTF-8 probe initially still failed on Arabic Windows because MinGW's neutral
manifest remained beside the newly added English resource. Replacing the neutral
manifest through UpdateResourceW was verified locally: Arabic prompt bytes now
arrive intact. Move that operation into staging, retain a language-neutral
manifest, and force UTF-8 for CI report output as well.
