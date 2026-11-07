"""Optional local Nemotron recognition, with provisional text kept out of paste."""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import signal
import subprocess
import tempfile
import threading
import time
import urllib.request
from collections import ChainMap
from pathlib import Path

from . import config
from .models import NEMOTRON
from .whisper import POLL_INTERVAL, STARTUP_TIMEOUT, WhisperServer, _free_port, _multipart, clean

MODEL_NAME = NEMOTRON.filename
MODEL_SIZE = NEMOTRON.size
MODEL_SHA256 = NEMOTRON.sha256
MODEL_URL = (
    "https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/"
    "ea30d66debe3740a08b573244286791d423d6b3e/" + MODEL_NAME
)


def binary_path() -> Path:
    return config.LIBEXEC_DIR / "nemo-speech" / "bin" / "nemo-speech"


def is_nemotron(model: str | Path) -> bool:
    return Path(model).name == MODEL_NAME


def create_server(settings, log):
    if is_nemotron(settings.get("model", "")):
        return NemotronServer(settings, log)
    return WhisperServer(settings, log)


def _language(settings) -> str:
    code = str(settings.get("language", "ar"))
    return {"ar": "ar-AR", "en": "en-US", "auto": ""}.get(code, code)


def _speech_contexts(settings) -> list[dict]:
    # Whisper's dialect prompt is not a decoder lexicon: boosting its function
    # words replaced real speech with those particles in a retained recording.
    phrases = [word.strip() for word in re.split(r"[,،\n]", str(settings.get("nemo_vocabulary") or ""))]
    phrases = list(dict.fromkeys(word for word in phrases if word))[:32]
    return [{"phrases": phrases, "boost": 0.5}] if phrases else []


class LiveText:
    """Completed utterances are authoritative; deltas belong to the current one."""

    def __init__(self):
        self.completed: list[str] = []
        self.partial = ""

    def update(self, message: dict) -> str | None:
        kind = message.get("type", "")
        if kind == "conversation.item.input_audio_transcription.delta":
            self.partial += str(message.get("delta", ""))
        elif kind == "conversation.item.input_audio_transcription.completed":
            text = str(message.get("transcript", "")).strip()
            if text:
                self.completed.append(text)
            self.partial = ""
        else:
            return None
        return " ".join(self.completed + ([self.partial] if self.partial else []))


class LiveSession:
    def __init__(self, server, on_text):
        # Optional: the original Whisper path has no WebSocket dependency.
        from websockets.sync.client import connect

        self.server = server
        self.on_text = on_text
        self.closed = threading.Event()
        self.send_condition = threading.Condition()
        self.send_deadline: float | None = None
        self.error: Exception | None = None
        self.final_ready = threading.Event()
        self.committed = False
        self.completed_seen = False
        self.text = LiveText()
        self.socket = connect(
            f"ws://127.0.0.1:{server.port}/v1/audio/transcriptions/realtime",
            proxy=None, open_timeout=5, close_timeout=1, max_size=1 << 20,
        )
        self.watchdog = threading.Thread(
            target=self._watch_send, daemon=True, name="nabria-live-send-deadline",
        )
        self.watchdog.start()
        try:
            created = json.loads(self.socket.recv(timeout=5))
            if created.get("type") != "session.created":
                raise RuntimeError(created.get("message", "live session was not created"))
            self._send(json.dumps({
                "type": "session.update",
                "session": {
                    "sample_rate": 16000, "language": _language(server.settings),
                    "verbatim": True, "automatic_punctuation": True,
                    "speech_contexts": _speech_contexts(server.settings),
                },
            }))
            updated = json.loads(self.socket.recv(timeout=5))
            if updated.get("type") != "session.updated":
                raise RuntimeError(updated.get("message", "live session was not configured"))
        except Exception:
            self.closed.set()
            with self.send_condition:
                self.send_condition.notify_all()
            self.cancel()
            self.socket.close()
            raise
        self.reader = threading.Thread(target=self._read, daemon=True, name="nabria-live-text")
        self.reader.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                try:
                    message = json.loads(self.socket.recv(timeout=0.25))
                except TimeoutError:
                    continue
                if message.get("type") == "error":
                    raise RuntimeError(message.get("message", "live recognition failed"))
                if message.get("type") == "input_audio_buffer.committed":
                    self.committed = True
                    self.final_ready.set()
                    continue
                if message.get("type") == "conversation.item.input_audio_transcription.completed":
                    self.completed_seen = True
                text = self.text.update(message)
                if text is not None:
                    self.on_text(text)
        except Exception as exc:
            if not self.closed.is_set():
                self.error = exc
                self.final_ready.set()

    def feed(self, pcm: bytes):
        if self.error is not None:
            raise RuntimeError(f"live recognition stopped: {self.error}") from self.error
        if self.closed.is_set():
            raise RuntimeError("live session is closed")
        if len(pcm) % 2:
            raise ValueError("PCM16 requires complete samples")
        self._send(pcm)
        self.server.last_used = time.monotonic()

    def _send(self, data):
        with self.send_condition:
            self.send_deadline = time.monotonic() + 3
            self.send_condition.notify_all()
        try:
            self.socket.send(data)
        finally:
            with self.send_condition:
                self.send_deadline = None
                self.send_condition.notify_all()

    def finish(self, timeout: float = 1.0) -> str | None:
        """Return a confirmed final result, never an unfinished partial."""
        if self.closed.is_set() or self.error is not None:
            return None
        # Earlier completed utterances do not confirm the last audio chunk.
        # Require a completion in this commit phase before accepting its ack.
        self.completed_seen = False
        self.committed = False
        self.final_ready.clear()
        self._send(json.dumps({"type": "input_audio_buffer.commit"}))
        if not self.final_ready.wait(timeout):
            return None
        if self.error is not None or not self.committed or not self.completed_seen or self.text.partial:
            return None
        return self.server.corrections.apply(clean(" ".join(self.text.completed)))

    def _watch_send(self):
        # The library's receive timeout does not bound a blocking sendall to
        # an engine that stopped reading. Interrupt the socket at the deadline.
        with self.send_condition:
            while not self.closed.is_set():
                if self.send_deadline is None:
                    self.send_condition.wait()
                    continue
                remaining = self.send_deadline - time.monotonic()
                if remaining > 0:
                    self.send_condition.wait(remaining)
                    continue
                self.error = TimeoutError("live audio send timed out")
                self.cancel()
                return

    def cancel(self):
        """Interrupt in-flight I/O without a network write or joining GTK."""
        with contextlib.suppress(OSError):
            self.socket.socket.shutdown(socket.SHUT_RDWR)

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        with self.send_condition:
            self.send_condition.notify_all()
        # A bounded commit may already have supplied the final result. Closing
        # still cancels immediately when that path failed or timed out.
        self.cancel()
        self.socket.close()
        self.reader.join(timeout=1.5)
        self.watchdog.join(timeout=1.5)


class NemotronServer(WhisperServer):
    def __init__(self, settings, log):
        super().__init__(ChainMap({
            "server_binary": str(settings.get("nemo_binary") or binary_path()),
        }, settings), log)
        self.configuration = None

    def _spawn(self, binary: Path, model: Path, on_gpu: bool):
        if self.process is not None:
            self._stop_locked()
        self.port = int(self.settings.get("server_port") or 0) or _free_port()
        env = dict(os.environ)
        if on_gpu and self.device is not None and self.device.visible is not None:
            env["GGML_VK_VISIBLE_DEVICES"] = str(self.device.visible)
        # v0.3.0's batched Vulkan warmup aborts in ggml's CPU graph operation
        # with this multilingual checkpoint. Per-session inference was verified
        # on the same GPU and WAV. Keep the model, disable that server batcher.
        self.configuration = tempfile.TemporaryDirectory(prefix="nabria-nemotron-")
        configuration = Path(self.configuration.name) / "asr.yaml"
        configuration.write_text("asr:\n  batching:\n    enabled: false\n")
        command = [
            str(binary), "serve", "--asr-model", str(model),
            "--host", "127.0.0.1", "--port", str(self.port), "--no-ui",
            "--no-warmup", "--config", str(configuration),
            "--asr.backend.gpu", "0" if on_gpu else "-1",
        ]
        self.log(f"starting Nemotron on 127.0.0.1:{self.port} on {'GPU' if on_gpu else 'CPU'}")
        self.stderr_tail.clear()
        self.process = subprocess.Popen(
            command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            start_new_session=True,
            creationflags=subprocess.CREATE_NO_WINDOW if config.WINDOWS else 0,
        )
        self.drain = threading.Thread(
            target=self._drain_stderr, args=(self.process,), daemon=True,
            name="nabria-nemotron-stderr",
        )
        self.drain.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Nemotron exited with code {self.process.returncode}{self._stderr_summary()}")
            with contextlib.suppress(OSError, ValueError):
                with opener.open(f"http://127.0.0.1:{self.port}/ready", timeout=1) as response:
                    if json.load(response).get("ready") is True:
                        self.last_used = time.monotonic()
                        self.log("Nemotron ready")
                        self._start_reaper()
                        return
            time.sleep(POLL_INTERVAL)
        self.stop()
        raise TimeoutError("Nemotron did not become ready")

    def _stop_locked(self):
        process = self.process
        if not config.WINDOWS and process is not None and getattr(process, "pid", None):
            # ggml's abort handler can leave debugger helpers holding stderr
            # open after the server dies. Kill only this owned process group
            # before closing the pipe, or CPU fallback can block on close().
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
            if self.drain is not None:
                self.drain.join(timeout=1)
        super()._stop_locked()
        if self.configuration is not None:
            self.configuration.cleanup()
            self.configuration = None

    def start_live(self, on_text):
        self.ensure()
        return LiveSession(self, on_text)

    def _infer(self, wav_path: Path) -> str:
        self.ensure()
        fields = {"response_format": "json", "verbatim": "true", "automatic_punctuation": "true"}
        language = _language(self.settings)
        if language:
            fields["language"] = language
        contexts = _speech_contexts(self.settings)
        if contexts:
            fields["speech_contexts"] = json.dumps(contexts, ensure_ascii=False)
        body, content_type = _multipart(fields, wav_path)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/audio/transcriptions", data=body,
            headers={"Content-Type": content_type}, method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=300) as response:
            result = json.load(response)
        return self.corrections.apply(clean(str(result.get("text", ""))))
