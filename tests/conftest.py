"""Shared fixtures.

`src` goes on the path here rather than through an installed package, because
that is how the app itself is run -- `run.sh` sets PYTHONPATH and the systemd
unit calls `run.sh`. Testing an installed copy would be testing something the
user never executes.
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    """Point every XDG directory at a temporary tree.

    config.py reads these at import time, so anything that imports it must be
    reloaded after this fixture runs -- see `fresh_config`.
    """
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        directory = tmp_path / name.lower()
        directory.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(directory))
    return tmp_path


@pytest.fixture
def fresh_config(xdg, monkeypatch):
    """config, reloaded against the temporary tree -- and put back afterwards.

    The reload rebinds module-level paths in place, so without the teardown
    every later test in the session sees a config pointing at a deleted temp
    directory. That is not a loud failure: the engine tests simply find no
    model and skip themselves, so the suite stays green while quietly no
    longer testing the engine.
    """
    import importlib

    from nabria import config

    yield importlib.reload(config)
    # The environment has to go back before the reload, not after: monkeypatch
    # undoes its own changes only once every fixture that depends on it has
    # finished, so a reload here would otherwise re-read the temporary paths
    # and restore nothing.
    monkeypatch.undo()
    importlib.reload(config)


def write_wav(path: Path, samples: list[int], rate: int = 16_000) -> Path:
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(rate)
        sink.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def tone(frames: int, amplitude: int, rate: int = 16_000, hz: float = 440.0) -> list[int]:
    """A sine at a known amplitude, so an expected dBFS can be computed."""
    return [
        int(amplitude * math.sin(2 * math.pi * hz * index / rate))
        for index in range(frames)
    ]
