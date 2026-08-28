"""The control socket, against a real daemon object and a real socket.

The hotkey's entire job is to write one line here, so this is the path every
single dictation goes through. It is exercised for real rather than mocked:
a bound AF_UNIX socket, the daemon's own accept loop, and the same client
module the `nabria` command uses.

GTK and the layer-shell typelib are needed to import the daemon at all, so the
whole module skips where they are absent -- CI runners have neither.
"""

from __future__ import annotations

import importlib
import os
import stat

import pytest

gtk = pytest.importorskip("gi", reason="PyGObject is not installed")
gtk.require_version("Gtk", "4.0")
try:
    from gi.repository import Gtk  # noqa: F401
except (ImportError, ValueError) as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"GTK 4 is unavailable: {exc}", allow_module_level=True)


@pytest.fixture
def daemon(fresh_config):
    from nabria import app as app_module
    from nabria import history as history_module

    importlib.reload(history_module)
    app_module = importlib.reload(app_module)

    instance = app_module.Daemon()
    instance._serve()
    yield instance
    instance.log_file.close()


@pytest.fixture
def talk(fresh_config):
    from nabria import client as client_module

    return importlib.reload(client_module).send


def test_status_answers_idle(daemon, talk):
    assert talk("status") == "idle"


def test_unknown_commands_are_reported_not_ignored(daemon, talk):
    assert "unknown command" in talk("nonsense")


def test_dictation_commands_are_accepted(daemon, talk):
    # They are dispatched onto the GTK main loop, which is not running here,
    # so the acknowledgement is all that can be checked -- but that
    # acknowledgement is what tells the hotkey it was heard.
    assert talk("toggle") == "ok"
    assert talk("cancel") == "ok"


def test_last_returns_the_most_recent_transcript(daemon, talk, fresh_config):
    from nabria import history

    history.append("the words I said", 2.0, 0.3)
    assert talk("last") == "the words I said"


def test_last_survives_a_transcript_longer_than_one_packet(daemon, talk):
    # The client reads to EOF for exactly this reason; a single recv would
    # truncate a minute of dictation to the first 8 KiB.
    from nabria import history

    long_text = "كلمة " * 4000
    history.append(long_text, 60.0, 3.0)
    assert talk("last") == long_text.strip()


def test_the_socket_is_private_to_the_user(daemon, fresh_config):
    # It accepts commands that record audio, so it must not be group or
    # world writable.
    mode = os.stat(fresh_config.SOCKET_PATH).st_mode
    assert not mode & (stat.S_IRWXG | stat.S_IRWXO)


def test_a_stale_socket_file_does_not_stop_the_daemon(fresh_config):
    # Left behind by a crash. bind() fails on an existing path, so the daemon
    # removes it first -- otherwise one crash means dictation never starts
    # again until someone deletes a file they do not know about.
    from nabria import app as app_module

    fresh_config.SOCKET_PATH.write_text("leftover", encoding="utf-8")
    instance = importlib.reload(app_module).Daemon()
    instance._serve()
    try:
        assert fresh_config.SOCKET_PATH.exists()
    finally:
        instance.log_file.close()


def test_client_reports_a_daemon_that_is_not_running(fresh_config, capsys):
    from nabria import client as client_module

    client_module = importlib.reload(client_module)
    assert client_module.main("status") == 1
    assert "daemon is not running" in capsys.readouterr().err
