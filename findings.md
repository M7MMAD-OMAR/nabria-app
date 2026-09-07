# Findings

## Initial state

- The initial review had no diff: main and origin/main were identical.
- Windows had a design document, no implementation or packaging.
- config imports os.getuid unconditionally; the application cannot import on Windows.
- The daemon uses Unix sockets, GLib Unix signals and portal shortcuts.
- Recording uses PipeWire; input selection uses wpctl; delivery uses Wayland tools.
- GTK, model catalogue, history and inference should remain shared.
- GitHub authentication is available. The current app release is v0.4.6.
- QEMU is installed; Wine was not found on PATH. A separate Windows 11 Pro Arabic VM was subsequently created.

## Implementation decisions
- Use official MSYS2 UCRT GTK packages and a relocatable bundled Python runtime.
  GTK documents this distribution route; it avoids compiling the entire GUI stack.
- A native launcher and Inno Setup provide one executable installer.
- Keep all Win32 APIs under nabria/windows and retain the shared GTK UI.
- WASAPI initially uses the existing conservative warm-up. Hardware calibration
  is still outstanding and must not be described as completed.
- Clipboard restoration duplicates the actual OLE formats before replacing data;
  a live OleGetClipboard wrapper does not survive replacement.
- The Windows build/test workflow is separate from Linux checks.

## Native packaging checks
- Windows compilation found a missing SPIRV-Headers package; the dependency
  is now explicit rather than assuming shaderc brings development headers.
- Launcher uses a Windows Job Object so abrupt exit cannot orphan the engine.
- Capture filenames are unique across restarts, protecting unfinished WAVs.
- Windows shortcut text now describes Windows behavior in both UI languages.

- The Windows launcher removes inherited PATH entries. PowerShell is in
  System32/WindowsPowerShell/v1.0, so bare powershell.exe breaks notifications
  in the installed app. Resolve its system path explicitly and include a native
  toast host check in the runtime suite.

- Native engine argument validation found Arabic vocabulary becoming question
  marks under the legacy Windows ANSI code page. Embed a per-process UTF-8
  manifest and require Windows 10 1903 or later. Runtime checks now include
  Arabic engine arguments, installation paths and model paths.
