"""Every transcript is appended here before anything is typed.

The point is that a dictation can never be lost: if injection fails, if the
wrong window had focus, if the text was typed into something that discarded
it, the words are still on disk and still recoverable with `dictate last`.
"""

from __future__ import annotations

import json
from datetime import datetime

from .config import DATA_DIR

HISTORY_PATH = DATA_DIR / "history.jsonl"
KEEP_LINES = 2000


def append(text: str, seconds: float, elapsed: float) -> None:
    if not text:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "seconds": round(seconds, 1),
        "elapsed": round(elapsed, 1),
        "text": text,
    }
    with HISTORY_PATH.open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(record, ensure_ascii=False) + "\n")
    _trim()


def _trim() -> None:
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= KEEP_LINES:
        return
    HISTORY_PATH.write_text("\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8")


def recent(limit: int = 100) -> list[dict]:
    """Newest first, malformed lines skipped rather than raised."""
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
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
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            return json.loads(line).get("text", "")
        except ValueError:
            continue
    return ""
