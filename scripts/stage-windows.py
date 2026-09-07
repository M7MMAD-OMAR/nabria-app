"""Assemble the UCRT runtime without depending on the build machine's PATH."""

import shutil
import subprocess
import sys
from pathlib import Path

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
shutil.copy2(ROOT / "build/whisper-windows/LICENSE", DEST / "engine/LICENSE")
shutil.copy2(ROOT / "LICENSE", DEST / "LICENSE")
# Exact runtime package versions accompany the build for reproducibility.
(DEST / "runtime-packages.txt").write_bytes(subprocess.check_output(["pacman", "-Q"]))
