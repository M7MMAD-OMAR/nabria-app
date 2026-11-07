"""A final live result is reusable only for the exact, completely drained take."""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('gi')
from nabria import app


def daemon_for(recorder):
    daemon = app.Daemon.__new__(app.Daemon)
    daemon.settings = {'model': 'model', 'language': 'ar', 'nemo_vocabulary': ''}
    daemon.whisper = SimpleNamespace(transcribe=lambda path: 'file fallback')
    daemon.recording = None
    daemon.live_stop = None
    daemon.live_session = None
    daemon.live_result = None
    daemon.log = lambda text: None
    daemon.jobs = 0
    daemon.jobs_lock = threading.Lock()
    return daemon


def completed_result(daemon, recorder, text='confirmed final'):
    result = app.LiveResult(str(recorder.destination), daemon.whisper, daemon._live_key())
    result.expected_bytes = result.sent_bytes = recorder.live_bytes
    result.finalize = True
    result.text = text
    result.done.set()
    recorder.live_result = result
    return result


def test_confirmed_stream_result_skips_complete_file_decode():
    recorder = SimpleNamespace(destination=Path('take.wav'), live_bytes=4)
    daemon = daemon_for(recorder)
    completed_result(daemon, recorder)
    daemon.whisper.transcribe = lambda path: pytest.fail('duplicate recognition')
    assert daemon._transcribe_audio(recorder) == 'confirmed final'


@pytest.mark.parametrize('fault', ['wrong_take','different_engine','language','vocabulary','missing_pcm','unfinished','failed','cancelled'])
def test_untrusted_or_incomplete_live_results_use_the_saved_wav(fault):
    recorder = SimpleNamespace(destination=Path('take.wav'), live_bytes=4)
    daemon = daemon_for(recorder)
    result = completed_result(daemon, recorder)
    if fault == 'wrong_take': result.take = 'another.wav'
    elif fault == 'different_engine': result.engine = object()
    elif fault == 'language': daemon.settings['language'] = 'en'
    elif fault == 'vocabulary': daemon.settings['nemo_vocabulary'] = 'AI'
    elif fault == 'missing_pcm': result.sent_bytes = 2
    elif fault == 'unfinished': result.done.clear()
    elif fault == 'failed': result.text = None
    elif fault == 'cancelled': result.finalize = False
    if fault == 'unfinished':
        result.done = SimpleNamespace(wait=lambda timeout: False)
    assert daemon._transcribe_audio(recorder) == 'file fallback'


def test_empty_confirmed_transcript_is_not_decoded_twice():
    recorder = SimpleNamespace(destination=Path('take.wav'), live_bytes=4)
    daemon = daemon_for(recorder)
    completed_result(daemon, recorder, '')
    daemon.whisper.transcribe = lambda path: pytest.fail('empty result repeated')
    assert daemon._transcribe_audio(recorder) == ''


def test_last_pcm_is_sent_before_requesting_stream_finalization():
    recorder = SimpleNamespace(destination=Path('take.wav'), live_bytes=4,
                               read_live=lambda offset: (b'abcd'[offset:], 4))
    daemon = daemon_for(recorder)
    stop = threading.Event()
    result = app.LiveResult(str(recorder.destination), daemon.whisper, daemon._live_key())
    result.finalize = True
    result.expected_bytes = 4
    calls = []
    def start_live(callback):
        stop.set()
        return SimpleNamespace(feed=lambda data: calls.append(('feed',data)),
                               finish=lambda timeout: calls.append(('finish',timeout)) or 'final',
                               close=lambda: calls.append(('close',)))
    daemon._preview(recorder, stop, start_live, result)
    assert [call[0] for call in calls] == ['feed','finish','close']
    assert result.sent_bytes == 4
    assert result.text == 'final'
    assert result.done.is_set()


def test_completed_overlay_closes_without_a_success_flash():
    daemon = daemon_for(None)
    calls = []
    daemon.orb = SimpleNamespace(hide=lambda: calls.append('hide'), flash=lambda *args: pytest.fail('lingering overlay'))
    daemon._done('delivered')
    assert calls == ['hide']


def test_queued_work_keeps_its_indicator_until_it_finishes():
    daemon = daemon_for(None)
    daemon.jobs = 1
    calls = []
    daemon.orb = SimpleNamespace(state='working', hide=lambda: calls.append('hide'))
    daemon._done('earlier result')
    assert calls == []
    daemon.jobs = 0
    daemon._settle()
    assert calls == ['hide']
