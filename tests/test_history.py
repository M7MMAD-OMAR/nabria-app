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
    """The module, pointed at the temporary tree.

    The reload is belt to the braces of `history._path()` resolving the
    directory per call. It used to be the only thing standing between this
    suite and the transcripts of whoever ran it -- and it only protected the
    tests that asked for this fixture. One that imported the module directly
    deleted thirty-eight real ones.
    """
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
    with history._path().open("a", encoding="utf-8") as sink:
        sink.write("{ truncated write from a crash\n")
    history.append("newer one", 1.0, 0.1)

    assert history.last() == "newer one"
    assert [r["text"] for r in history.recent()] == ["newer one", "good one"]


def test_arabic_is_stored_readable(history):
    history.append("مرحبا كيف حالك", 1.0, 0.1)
    raw = history._path().read_text(encoding="utf-8")
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
    lines = history._path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == history.KEEP_LINES
    assert history.last() == f"line {history.KEEP_LINES + 49}"  # newest survives


def test_the_transcripts_follow_the_temporary_profile(history, tmp_path):
    """The guard for the worst thing this suite has ever done.

    `HISTORY_PATH` was resolved at import and `fresh_config` reloaded only
    `config`, so a test that wrote or deleted history reached the *real* one.
    A test of `history.clear()` deleted thirty-eight of the author's own
    transcripts, and there was no snapshot to get them back from.

    So the path is resolved per call now, and this asserts the property
    directly rather than trusting the fixture to have reloaded the right list
    of modules.
    """
    assert history._path().is_relative_to(tmp_path)
    history.append("under test", 1.0, 1.0)
    assert history._path().exists()
    assert history._path().is_relative_to(tmp_path)
