"""The transcript log.

This file is the reason a dictation can never be lost. It is written before
the text is typed, so every failure downstream -- wrong window, a toolkit that
drops the paste, a crash -- still leaves the words recoverable.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def history(fresh_config):
    # history binds DATA_DIR at import time, so it has to be reloaded after
    # the config module has been pointed at the temporary tree.
    from nabria import history as module

    return importlib.reload(module)


def test_append_and_read_back(history):
    history.append("hello there", 2.0, 0.4)
    assert history.last() == "hello there"
    assert len(history.recent()) == 1


def test_empty_transcripts_are_not_recorded(history):
    history.append("", 2.0, 0.4)
    assert history.recent() == []


def test_newest_first(history):
    for word in ("first", "second", "third"):
        history.append(word, 1.0, 0.1)
    assert [record["text"] for record in history.recent()] == ["third", "second", "first"]


def test_missing_file_is_not_an_error(history):
    assert history.last() == ""
    assert history.recent() == []


def test_a_corrupt_line_does_not_hide_the_rest(history):
    history.append("good one", 1.0, 0.1)
    with history.HISTORY_PATH.open("a", encoding="utf-8") as sink:
        sink.write("{ truncated write from a crash\n")
    history.append("newer one", 1.0, 0.1)

    assert history.last() == "newer one"
    assert [r["text"] for r in history.recent()] == ["newer one", "good one"]


def test_arabic_is_stored_readable(history):
    history.append("مرحبا كيف حالك", 1.0, 0.1)
    raw = history.HISTORY_PATH.read_text(encoding="utf-8")
    assert "مرحبا كيف حالك" in raw


def test_audio_path_is_recorded_only_when_there_is_one(history):
    history.append("with audio", 1.0, 0.1, "/takes/a.wav")
    history.append("without", 1.0, 0.1)
    newest, older = history.recent()
    assert "audio" not in newest
    assert older["audio"] == "/takes/a.wav"


def test_the_log_is_trimmed(history):
    for index in range(history.KEEP_LINES + 50):
        history.append(f"line {index}", 1.0, 0.1)
    lines = history.HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) == history.KEEP_LINES
    assert history.last() == f"line {history.KEEP_LINES + 49}"  # newest survives
