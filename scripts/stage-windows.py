"""Assemble the UCRT runtime without depending on the build machine's PATH."""

import shutil
import json
import ctypes as C
import subprocess
import ssl
import sys
from pathlib import Path


def embed_manifest(executable: Path) -> None:
    from ctypes import wintypes as W
    kernel = C.WinDLL("kernel32", use_last_error=True)
    begin = kernel.BeginUpdateResourceW
    begin.argtypes = [W.LPCWSTR, W.BOOL]
    begin.restype = W.HANDLE
    update = kernel.UpdateResourceW
    update.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, W.WORD, C.c_void_p, W.DWORD]
    update.restype = W.BOOL
    end = kernel.EndUpdateResourceW
    end.argtypes = [W.HANDLE, W.BOOL]
    end.restype = W.BOOL
    data = (ROOT / "packaging/windows/utf8.manifest").read_bytes()
    buffer = C.create_string_buffer(data)
    handle = begin(str(executable), False)
    if not handle:
        raise C.WinError(C.get_last_error())
    # MinGW already embeds a neutral manifest. Adding an English resource
    # leaves that one active on Arabic Windows, silently keeping the ANSI
    # code page. Replace the neutral resource instead of adding a language.
    if not update(handle, C.c_void_p(24), C.c_void_p(1), 0, buffer, len(data)):
        error = C.WinError(C.get_last_error())
        end(handle, True)
        raise error
    if not end(handle, False):
        raise C.WinError(C.get_last_error())


ROOT = Path(__file__).resolve().parent.parent
PREFIX = Path(sys.prefix)
DEST = ROOT / "dist/Nabria"
if sys.platform != "win32":
    raise SystemExit("Stage on Windows using the MSYS2 UCRT Python")
if DEST.exists():
    shutil.rmtree(DEST)
(DEST / "runtime/bin").mkdir(parents=True)
(DEST / "engine").mkdir()
shutil.copytree(ROOT / "src/nabria", DEST / "app/nabria", ignore=shutil.ignore_patterns("__pycache__"))
for path in (PREFIX / "bin").glob("*.dll"):
    shutil.copy2(path, DEST / "runtime/bin" / path.name)
for name in ("python.exe", "pythonw.exe"):
    shutil.copy2(PREFIX / "bin" / name, DEST / "runtime/bin" / name)
for name in (f"lib/python{sys.version_info.major}.{sys.version_info.minor}",
             "lib/girepository-1.0", "share/glib-2.0", "share/icons",
             "share/fontconfig", "etc/fonts", "share/licenses"):
    source = PREFIX / name
    if source.exists():
        shutil.copytree(source, DEST / "runtime" / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.a", "*.pyc"))
shutil.copy2(ROOT / "build/whisper-windows/build/bin/whisper-server.exe", DEST / "engine")
embed_manifest(DEST / "engine/whisper-server.exe")
shutil.copy2(ROOT / "build/whisper-windows/LICENSE", DEST / "engine/LICENSE")
shutil.copy2(ROOT / "LICENSE", DEST / "LICENSE")
# Exact runtime package versions accompany the build for reproducibility.
(DEST / "runtime-packages.txt").write_bytes(subprocess.check_output(["pacman", "-Q"]))

certificate = ssl.get_default_verify_paths().cafile
if certificate:
    shutil.copy2(certificate, DEST / "runtime/cert.pem")

# One string catalogue serves GTK and the native Windows desktop.
sys.path.insert(0, str(ROOT / "src"))
from nabria import __version__, i18n
(DEST / "strings.json").write_text(json.dumps(i18n.STRINGS, ensure_ascii=False), encoding="utf-8")
(DEST / "version.txt").write_text(__version__, encoding="utf-8")

messages = ["[CustomMessages]"]
for language in ("en", "ar"):
    for key in ("startup", "desktop", "open"):
        messages.append(f"{language}.{key}=" + i18n.STRINGS[f"installer.{key}"][language])
(ROOT / "dist/windows-messages.iss").write_text("\n".join(messages), encoding="utf-8-sig")
