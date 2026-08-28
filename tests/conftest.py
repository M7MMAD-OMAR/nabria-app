"""Shared fixtures.

`src` goes on the path here rather than through an installed package, because
that is how the app itself is run -- `run.sh` sets PYTHONPATH and the systemd
unit calls `run.sh`. Testing an installed copy would be testing something the
user never executes.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from nabria import i18n  # noqa: E402  -- after the path insert above


def display_available() -> bool:
    """Whether GTK can actually open a display.

    `Gtk.init_check()` alone is not a reliable test from Python: depending on
    the PyGObject version it returns a bool or a `(bool, argv)` tuple, and a
    bare `if not Gtk.init_check()` is therefore always false against the tuple
    form. That is how the headless guard silently stopped guarding on CI, where
    the tests then failed inside the first widget constructor instead of
    skipping.
    """
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gtk
    except (ImportError, ValueError):
        return False

    result = Gtk.init_check()
    started = result[0] if isinstance(result, tuple) else bool(result)
    # The display is the thing actually needed, so ask for it rather than
    # trusting the return value.
    return bool(started) and Gdk.Display.get_default() is not None


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


_serial = iter(range(1, 100_000))


@pytest.fixture
def application():
    """A throwaway Gtk.Application for widget tests.

    A fresh id each time, and NON_UNIQUE. Without the flag the application
    hands its activation to the daemon already running on this machine and
    never activates; without a fresh id the second registration collides,
    because the first is still exported on the session bus.
    """
    from gi.repository import Gio, Gtk

    app = Gtk.Application(
        application_id=f"com.sbarah.NabriaTest{next(_serial)}",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register()
    return app


@pytest.fixture
def models_dir(tmp_path):
    directory = tmp_path / "models"
    directory.mkdir()
    return directory


@pytest.fixture(autouse=True)
def ui_language():
    """English, and no locale in the environment, for every test.

    `i18n` keeps the selected language in a module global, the way GTK keeps
    its default text direction -- so anything that calls `i18n.use()` leaks
    into every test that runs after it. `Daemon()` calls it during
    construction, resolving `auto` against whatever `LANG` the developer
    happens to have, which made results depend on the machine and on test
    order. `test_shortcut.py` passed only because the Arabic sentence also
    ends with a colon.

    Autouse, and the same argument as `fresh_config`: a leaked module global
    means later tests quietly stop testing what they claim to, while the suite
    stays green.
    """
    saved = {name: os.environ.get(name) for name in ("LC_ALL", "LC_MESSAGES", "LANG")}
    for name in saved:
        os.environ.pop(name, None)
    i18n.use("en")
    yield
    for name, value in saved.items():
        if value is not None:
            os.environ[name] = value
    i18n.use("en")
