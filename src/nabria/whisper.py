"""Supervises the local whisper.cpp HTTP server.

The server is started on demand and kept warm, then stopped again once it has
been idle long enough that holding ~2.5 GB of VRAM is no longer worth it. It
binds its port only after the model has finished loading, so a successful TCP
connection is a sufficient readiness check.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from . import gpu

STARTUP_TIMEOUT = 90.0
POLL_INTERVAL = 0.2

# Whisper produces these out of near-silence or room tone -- they are training
# data bleeding through, never something the user said, and typing them into a
# document is worse than typing nothing at all. Matched after normalisation,
# so "شكراً للمشاهدة." and "شكرا للمشاهدة" collapse to the same entry.
HALLUCINATIONS = {
    "thank you", "thanks for watching", "thanks for watching!",
    "please subscribe", "you", "bye", "bye.", "the end",
    "شكرا للمشاهدة", "شكرا لمشاهدتكم", "شكرا لكم على المشاهدة",
    "اشتركوا في القناة", "اشترك في القناة", "لا تنسى الاشتراك",
    "ترجمة نانسي قنقر", "الحمد لله", "بس",
}

# Arabic diacritics and tatweel, plus the alef variants, so that the same
# sentence typed with or without them normalises identically.
_STRIP = dict.fromkeys(range(0x064B, 0x0653)) | {0x0640: None}
_FOLD = str.maketrans("أإآىة", "اااية")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    marker = f"--{boundary}".encode()
    parts: list[bytes] = []
    for name, value in fields.items():
        parts += [
            marker,
            f'Content-Disposition: form-data; name="{name}"'.encode(),
            b"",
            str(value).encode("utf-8"),
        ]
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts += [
        marker,
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode(),
        f"Content-Type: {content_type}".encode(),
        b"",
        file_path.read_bytes(),
        f"--{boundary}--".encode(),
        b"",
    ]
    return b"\r\n".join(parts), f"multipart/form-data; boundary={boundary}"


class WhisperServer:
    def __init__(self, settings: dict[str, Any], log):
        self.settings = settings
        self.log = log
        self.process: subprocess.Popen[bytes] | None = None
        self.port = 0
        self.lock = threading.RLock()
        self.last_used = 0.0
        self._reaper: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def ensure(self) -> None:
        """Start the server if needed and block until it accepts connections."""
        with self.lock:
            if self.is_running():
                self.last_used = time.monotonic()
                return
            self._start_locked()

    def _start_locked(self) -> None:
        binary = Path(self.settings["server_binary"])
        model = Path(self.settings["model"])
        if not binary.exists():
            raise FileNotFoundError(f"whisper server binary missing: {binary}")
        if not model.exists():
            raise FileNotFoundError(f"model missing: {model}")

        self.port = int(self.settings.get("server_port") or 0) or _free_port()
        # The GPU override belongs to this subprocess only. Exporting it into
        # the daemon would drag the GTK UI onto the discrete card as well.
        env = dict(os.environ)
        gpu_select = str(self.settings.get("gpu_select") or "")
        if gpu_select == "auto":
            gpu_select = gpu.preferred()
            if gpu_select:
                self.log(f"selecting GPU {gpu_select}")
        if gpu_select:
            env["MESA_VK_DEVICE_SELECT"] = gpu_select

        command = [
            str(binary),
            "-m", str(model),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "-t", str(self.settings.get("threads", 8)),
            "-l", str(self.settings.get("language", "auto")),
        ]
        vocabulary = str(self.settings.get("vocabulary") or "").strip()
        if vocabulary:
            # carry-initial-prompt re-applies the bias to every 30s window, so
            # a long dictation keeps spelling the same terms consistently
            # instead of drifting after the first window.
            command += ["--prompt", vocabulary, "--carry-initial-prompt"]
        self.log(f"starting whisper server on 127.0.0.1:{self.port}")
        self.process = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"whisper server exited with code {self.process.returncode}"
                )
            with contextlib.suppress(OSError):
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    self.last_used = time.monotonic()
                    self.log("whisper server ready")
                    self._start_reaper()
                    return
            time.sleep(POLL_INTERVAL)
        self.stop()
        raise TimeoutError("whisper server did not become ready")

    def _start_reaper(self) -> None:
        idle_seconds = float(self.settings.get("idle_unload_seconds") or 0)
        if idle_seconds <= 0 or (self._reaper and self._reaper.is_alive()):
            return

        def watch() -> None:
            while True:
                time.sleep(30)
                with self.lock:
                    if not self.is_running():
                        return
                    if time.monotonic() - self.last_used < idle_seconds:
                        continue
                    self.log("whisper server idle, releasing VRAM")
                    self._stop_locked()
                    return

        self._reaper = threading.Thread(target=watch, daemon=True, name="nabria-reaper")
        self._reaper.start()

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if not self.process:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.process.wait(timeout=5)
        self.process = None

    # -- inference ---------------------------------------------------------

    def transcribe(self, wav_path: Path) -> str:
        self.ensure()
        fields = {
            "response_format": "json",
            "language": str(self.settings.get("language", "auto")),
            "temperature": "0",
        }
        body, content_type = _multipart(fields, wav_path)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/inference",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = response.read().decode("utf-8", "replace")
        with self.lock:
            self.last_used = time.monotonic()

        try:
            text = json.loads(payload).get("text", "")
        except ValueError:
            text = payload
        return clean(text)


def _normalise(text: str) -> str:
    text = text.translate(_STRIP).translate(_FOLD)
    return " ".join(text.lower().strip(" .,!?؟،…\"'").split())


_NORMALISED = {_normalise(entry) for entry in HALLUCINATIONS}


def clean(text: str) -> str:
    """Trim whisper's leading space and drop its silence artefacts."""
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    if _normalise(text) in _NORMALISED:
        return ""
    return text
