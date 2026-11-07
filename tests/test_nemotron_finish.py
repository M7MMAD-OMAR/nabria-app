"""Only a completed utterance plus commit acknowledgement may skip WAV replay."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from nabria.nemotron import LiveSession


@pytest.mark.parametrize('outcome', ['ok', 'empty', 'timeout', 'partial_only', 'trailing_partial', 'error'])
def test_finalization_requires_an_authoritative_complete_acknowledgement(outcome):
    pytest.importorskip('websockets.sync.server')
    from websockets.sync.server import serve
    from websockets.exceptions import ConnectionClosed

    release = threading.Event()
    def handler(ws):
        ws.send(json.dumps({'type':'session.created'}))
        ws.recv()
        ws.send(json.dumps({'type':'session.updated'}))
        assert isinstance(ws.recv(), bytes)
        assert json.loads(ws.recv())['type'] == 'input_audio_buffer.commit'
        if outcome == 'timeout':
            release.wait(2)
            return
        if outcome == 'error':
            ws.send(json.dumps({'type':'error','message':'recognition failed'}))
        else:
            if outcome not in {'partial_only'}:
                ws.send(json.dumps({'type':'conversation.item.input_audio_transcription.completed',
                                    'transcript':'' if outcome == 'empty' else 'confirmed final'}))
            if outcome in {'partial_only','trailing_partial'}:
                ws.send(json.dumps({'type':'conversation.item.input_audio_transcription.delta','delta':'unfinished'}))
            ws.send(json.dumps({'type':'input_audio_buffer.committed'}))
        try:
            ws.recv()
        except ConnectionClosed:
            pass

    with serve(handler, '127.0.0.1', 0, close_timeout=.2) as http:
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        backend = SimpleNamespace(port=http.socket.getsockname()[1], settings={'language':'ar'},
                                  last_used=0, corrections=SimpleNamespace(apply=lambda text:text))
        session = LiveSession(backend, lambda text:None)
        try:
            session.feed(b'\x00\x00'*100)
            text = session.finish(timeout=.1 if outcome == 'timeout' else 2)
            if outcome == 'ok': assert text == 'confirmed final'
            elif outcome == 'empty': assert text == ''
            else: assert text is None
        finally:
            release.set()
            session.close()
            http.shutdown()
            worker.join(timeout=2)


def test_an_earlier_utterance_cannot_satisfy_the_last_commit():
    from nabria.nemotron import LiveText
    session = LiveSession.__new__(LiveSession)
    session.closed = threading.Event()
    session.error = None
    session.final_ready = threading.Event()
    session.final_ready.set()
    session.committed = True
    session.completed_seen = True
    session.text = LiveText()
    session.text.completed = ['earlier sentence']
    session.server = SimpleNamespace(corrections=SimpleNamespace(apply=lambda text:text))
    def acknowledge_without_completion(message):
        session.committed = True
        session.final_ready.set()
    session._send = acknowledge_without_completion
    assert session.finish(timeout=.1) is None
