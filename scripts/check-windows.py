"""Exercise the shipped directory with build tools removed from PATH."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
bundle = Path(sys.argv[1]).resolve()
target = root / "build/Windows runtime test with spaces"
if target.exists():
    shutil.rmtree(target)
shutil.copytree(bundle, target)
environment = dict(os.environ)
environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
environment.pop("PYTHONPATH", None)
environment.pop("PYTHONHOME", None)
environment["NABRIA_TEST_REPORT"] = str(root / "dist/windows-check.json")
environment["NABRIA_TEST_WAV"] = str(root / "build/whisper-windows/samples/jfk.wav")
result = subprocess.run([str(target / "Nabria.exe"), "--self-test"], env=environment, timeout=600)
report = Path(environment["NABRIA_TEST_REPORT"])
if report.exists():
    print(report.read_text(encoding="utf-8"), flush=True)
else:
    print(json.dumps({"error": "Self-test did not write its report"}), flush=True)
if result.returncode or not report.exists():
    raise SystemExit(result.returncode or 1)
