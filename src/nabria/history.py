"""Every transcript is appended here before anything is typed.

The point is that a dictation can never be lost: if injection fails, if the
wrong window had focus, if the text was typed into something that discarded
it, the words are still on disk and still recoverable with `nabria last`.
"""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from . import config

KEEP_LINES = 2000


def _path() -> Path:
    """Where the transcripts are, asked for now rather than at import.

    A module-level `DATA_DIR / "history.jsonl"` is resolved once, when the
    module is first imported, and every later change to the environment is
    invisible to it. That is how a test pointed at a temporary profile deleted
    the transcripts of the person running the suite -- `config` had been
    reloaded and this had not.

    Cheap enough to ask every time: it is two attribute lookups and a join,
    against a function that is about to touch the disk anyway.
    """
    return config.DATA_DIR / "history.jsonl"


def append(text: str, seconds: float, elapsed: float, audio: str = "") -> None:
    if not text:
        return
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "seconds": round(seconds, 1),
        "elapsed": round(elapsed, 1),
        "text": text,
    }
    if audio:
        record["audio"] = audio
    with _path().open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(record, ensure_ascii=False) + "\n")
    _trim()


def _trim() -> None:
    try:
        lines = _path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= KEEP_LINES:
        return
    _path().write_text("\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8")


def recent(limit: int = 100) -> list[dict]:
    """Newest first, malformed lines skipped rather than raised."""
    try:
        lines = _path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in reversed(lines):
        if len(records) >= limit:
            break
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def last() -> str:
    """The newest transcript's text, or "" if there is none.

    Expressed through `recent` rather than repeating the read-and-skip loop:
    the two disagreeing about what counts as a readable log is how `nabria
    last` and the history tab would come to show different things.
    """
    for record in recent(1):
        return str(record.get("text", ""))
    return ""


def clear() -> int:
    """Delete every transcript, and the audio kept alongside them.

    Returns how many were removed.

    The audio goes with the text, and that is the whole point rather than a
    tidiness measure: `keep_audio` leaves a WAV of everything that was ever
    said in this room, and somebody deleting their transcripts who was left
    with the recordings would have deleted nothing that matters. Failed takes
    too, which are kept precisely because they could not be transcribed and are
    therefore the ones with no text to look at.

    Everything is unlinked before the log is, so a failure part-way through
    leaves a log naming files that are gone rather than files with no log
    naming them -- the direction that can still be finished by pressing it
    again.
    """
    records = recent(KEEP_LINES)
    for record in records:
        audio = str(record.get("audio", ""))
        if audio:
            with suppress(OSError):
                Path(audio).unlink(missing_ok=True)

    # `failed/` holds the takes that never became text. Nothing in the log
    # points at them, so deleting only what the log names would leave them.
    with suppress(OSError):
        for leftover in config.FAILED_DIR.glob("*.wav"):
            leftover.unlink(missing_ok=True)

    with suppress(OSError):
        _path().unlink(missing_ok=True)
    return len(records)
