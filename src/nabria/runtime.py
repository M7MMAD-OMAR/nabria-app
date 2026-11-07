"""Detect an upgraded Linux Python without interrupting an active take."""

from __future__ import annotations

import encodings
import os
import subprocess
import sys
import threading
from pathlib import Path


def _identity(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size


class RuntimeWatch:
    def __init__(self) -> None:
        self.executable = Path(sys.executable)
        stdlib = Path(encodings.__file__).parent.parent
        # Fixed paths only. Replacing the stdlib or its codec files changes
        # these identities; walking the whole Python installation is needless.
        self.paths = (
            self.executable, stdlib, stdlib / "encodings",
            Path(encodings.__file__), stdlib / "encodings" / "idna.py",
        )
        self.baseline = self._snapshot()
        try:
            self.loaded = _identity(Path("/proc/self/exe"))[:2]
        except OSError:
            self.loaded = None
        self.lock = threading.Lock()
        self.checking = False
        self.verified = None

    def _snapshot(self):
        try:
            return tuple(_identity(path) for path in self.paths)
        except OSError:
            # A package manager can briefly remove files before replacing
            # them. The old process must stay alive until the new one works.
            return None

    def ready(self) -> bool:
        current = self._snapshot()
        if current is None or not os.access(self.executable, os.X_OK):
            return False
        if current == self.baseline and (self.loaded is None or current[0][:2] == self.loaded):
            return False
        with self.lock:
            if current == self.verified:
                return True
            if not self.checking:
                self.checking = True
                threading.Thread(
                    target=self._verify, args=(current,),
                    daemon=True, name="nabria-runtime-check",
                ).start()
        return False

    def _verify(self, fingerprint) -> None:
        try:
            result = subprocess.run(
                [str(self.executable), "-c",
                 "import codecs, socket; codecs.lookup('idna'); "
                 "socket.getaddrinfo('127.0.0.1', 1)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5,
            )
            if result.returncode == 0:
                with self.lock:
                    self.verified = fingerprint
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            with self.lock:
                self.checking = False
