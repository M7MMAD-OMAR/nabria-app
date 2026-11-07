"""Native streaming stays provisional; only full WAV recognition is delivered."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from nabria import gpu, models, nemotron
from nabria.nemotron import LiveText, NemotronServer
from nabria.whisper import WhisperServer


def test_model_path_selects_the_engine_without_changing_whisper(tmp_path):
    settings = {"model": str(tmp_path / "ggml-small.bin")}
    assert type(nemotron.create_server(settings, lambda line: None)) is WhisperServer
    settings["model"] = str(tmp_path / nemotron.MODEL_NAME)
    assert isinstance(nemotron.create_server(settings, lambda line: None), NemotronServer)
    assert settings.get("server_binary") is None


def test_completed_utterance_replaces_deltas_and_next_sentence_appends():
    text = LiveText()
    assert text.update({"type": "session.updated"}) is None
    assert text.update({"type": "conversation.item.input_audio_transcription.delta", "delta": "كلو"}) == "كلو"
    assert text.update({"type": "conversation.item.input_audio_transcription.delta", "delta": "د"}) == "كلود"
    assert text.update({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Claude"}) == "Claude"
    assert text.update({"type": "conversation.item.input_audio_transcription.delta", "delta": "ديسكتوب"}) == "Claude ديسكتوب"
    assert text.update({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Desktop"}) == "Claude Desktop"


def test_installed_streaming_model_is_visible_without_offering_uninstalled_engine(tmp_path):
    target = tmp_path / nemotron.MODEL_NAME
    target.write_bytes(b"GGUF")
    assert models.models_in(tmp_path) == [target]
    assert models.NEMOTRON.key not in models.CATALOG
    assert models.NEMOTRON.url == nemotron.MODEL_URL
    assert models.NEMOTRON.size == nemotron.MODEL_SIZE
    assert models.NEMOTRON.sha256 == nemotron.MODEL_SHA256
    target.rename(target.with_suffix(target.suffix + ".part"))
    assert models.models_in(tmp_path) == []


def test_complete_file_recognition_uses_local_http_and_original_audio(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    requests = []
    launches = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/ready"
            body = b'{"ready":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"text": "نص عربي مع Claude Desktop"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    binary, model, wav = [tmp_path / name for name in ("engine", nemotron.MODEL_NAME, "take.wav")]
    for path in (binary, model, wav):
        path.write_bytes(b"original private recording")
    for key in ("http_proxy", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    for key in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(key, "")
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as http:
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        running = [True]
        process = SimpleNamespace(
            stderr=None, poll=lambda: None if running[0] else 0,
            terminate=lambda: running.__setitem__(0, False), wait=lambda **kwargs: 0,
        )
        def spawn(command, **kwargs):
            launches.append(command)
            return process
        monkeypatch.setattr(nemotron.subprocess, "Popen", spawn)
        monkeypatch.setattr(gpu, "plan", lambda select: gpu.Plan(False, None, "test CPU"))
        engine = NemotronServer({
            "nemo_binary": str(binary), "model": str(model), "language": "ar",
            "nemo_vocabulary": "Claude Desktop، Claude Code, Claude Desktop",
            "server_port": http.server_port, "idle_unload_seconds": 0,
        }, lambda line: None)
        try:
            assert engine.transcribe(wav) == "نص عربي مع Claude Desktop"
            assert len(launches) == 1
            assert launches[0][1] == "serve"
            assert launches[0][-2:] == ["--asr.backend.gpu", "-1"]
            assert requests[0][0] == "/v1/audio/transcriptions"
            assert b"original private recording" in requests[0][1]
            assert b"ar-AR" in requests[0][1]
            assert b' name="verbatim"\r\n\r\ntrue' in requests[0][1]
            assert b'"phrases": ["Claude Desktop", "Claude Code"]' in requests[0][1]
        finally:
            engine.stop()
            http.shutdown()
            worker.join(timeout=2)


def test_live_socket_streams_pcm_and_close_cancels_without_commit(monkeypatch):
    pytest.importorskip("websockets.sync.server")
    from websockets.sync.server import serve
    from websockets.exceptions import ConnectionClosed

    updates, audio, endings, seen = [], [], [], []
    received = threading.Event()
    ended = threading.Event()
    def handler(ws):
        ws.send(json.dumps({"type": "session.created"}))
        updates.append(json.loads(ws.recv()))
        ws.send(json.dumps({"type": "session.updated"}))
        audio.append(ws.recv())
        ws.send(json.dumps({"type": "conversation.item.input_audio_transcription.delta", "delta": "هلا"}))
        ws.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed", "transcript": "هلأ"}))
        try:
            endings.append(json.loads(ws.recv()))
        except ConnectionClosed:
            endings.append("closed")
        finally:
            ended.set()

    for key in ("http_proxy", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with serve(handler, "127.0.0.1", 0) as http:
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        def on_text(text):
            seen.append(text)
            if text == "هلأ":
                received.set()
        server = SimpleNamespace(port=http.socket.getsockname()[1], settings={"language": "ar"}, last_used=0)
        session = nemotron.LiveSession(server, on_text)
        try:
            with pytest.raises(ValueError):
                session.feed(b"x")
            session.feed(b"\x01\x00" * 4096)
            assert received.wait(3)
        finally:
            session.close()
            session.close()
            assert ended.wait(3), "server did not observe the cancelled connection"
            http.shutdown()
            worker.join(timeout=2)
    assert updates[0]["session"]["sample_rate"] == 16000
    assert updates[0]["session"]["language"] == "ar-AR"
    assert audio == [b"\x01\x00" * 4096]
    assert seen == ["هلا", "هلأ"]
    assert endings == ["closed"]


@pytest.mark.parametrize("cancel", [True, False])
def test_an_engine_that_stops_reading_cannot_keep_a_live_sender(cancel):
    pytest.importorskip("websockets.sync.server")
    import os
    import socket
    from websockets.sync.server import serve

    release = threading.Event()
    finished = threading.Event()
    errors = []
    def handler(ws):
        ws.send(json.dumps({"type": "session.created"}))
        ws.recv()
        ws.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        ws.send(json.dumps({"type": "session.updated"}))
        release.wait(8)

    with serve(handler, "127.0.0.1", 0, max_queue=1, compression=None, close_timeout=0.2) as http:
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        backend = SimpleNamespace(port=http.socket.getsockname()[1], settings={"language": "ar"}, last_used=0)
        session = nemotron.LiveSession(backend, lambda text: None)
        session.socket.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        pcm = os.urandom(960000)
        def send():
            try:
                for _ in range(4):
                    session.feed(pcm)
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()
        sender = threading.Thread(target=send, daemon=True)
        sender.start()
        try:
            assert not finished.wait(0.2), "the peer should have filled the send buffer"
            if cancel:
                session.cancel()
            assert finished.wait(5), "live sender survived cancellation or the send deadline"
            assert errors
        finally:
            session.cancel()
            release.set()
            sender.join(timeout=2)
            session.close()
            http.shutdown()
            worker.join(timeout=2)


def test_a_crashed_servers_helpers_cannot_hold_the_stderr_pipe_open(tmp_path):
    import os
    import subprocess
    import sys
    import time

    if not hasattr(os, "fork"):
        pytest.skip("POSIX process-group cleanup")

    process = subprocess.Popen(
        [sys.executable, "-c", "import os,time; child=os.fork(); time.sleep(30) if child==0 else os._exit(1)"],
        stderr=subprocess.PIPE, start_new_session=True,
    )
    engine = NemotronServer({}, lambda line: None)
    engine.process = process
    engine.drain = threading.Thread(target=engine._drain_stderr, args=(process,), daemon=True)
    engine.drain.start()
    try:
        process.wait(timeout=3)
        started = time.monotonic()
        engine.stop()
        assert time.monotonic() - started < 3
        assert not engine.drain.is_alive()
        assert engine.process is None
    finally:
        if engine.process is not None:
            engine.stop()


def test_whispers_dialect_prompt_is_never_reused_as_decoder_boosting():
    assert nemotron._speech_contexts({"vocabulary": "هلأ، بحكي، لهيك"}) == []
    assert nemotron._speech_contexts({"nemo_vocabulary": "Claude Desktop، Claude Desktop, GitHub"}) == [
        {"phrases": ["Claude Desktop", "GitHub"], "boost": 0.5},
    ]
