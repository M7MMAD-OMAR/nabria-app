"""Upgrades wait for a working replacement and a quiet daemon."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from nabria import runtime


@pytest.fixture
def watch(tmp_path, monkeypatch):
    executable = tmp_path / "python"
    executable.write_bytes(b"old runtime")
    executable.chmod(0o755)
    codec = tmp_path / "idna.py"
    codec.write_text("old codec")
    result = runtime.RuntimeWatch()
    result.executable = executable
    result.paths = (executable, codec)
    result.baseline = result._snapshot()
    result.loaded = result.baseline[0][:2]
    # Tests drive probe completion explicitly, so no thread outlives a fixture.
    monkeypatch.setattr(runtime.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    return result


def replace_executable(watch):
    replacement = watch.executable.with_suffix(".new")
    replacement.write_bytes(b"new runtime")
    replacement.chmod(0o755)
    replacement.replace(watch.executable)


def test_an_unchanged_runtime_never_starts_a_probe(watch):
    assert not watch.ready()
    assert not watch.checking


def test_an_executable_replacement_needs_a_successful_fresh_python(watch, monkeypatch):
    replace_executable(watch)
    assert not watch.ready()
    assert watch.checking
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda argv, **kw: (
        calls.append((argv, kw)) or SimpleNamespace(returncode=0)
    ))
    watch._verify(watch._snapshot())
    assert watch.ready()
    assert calls[0][0][0] == str(watch.executable)
    assert "codecs.lookup('idna')" in calls[0][0][2]
    assert calls[0][1]["timeout"] == 5


def test_runtime_replaced_before_watch_started_is_detected(watch):
    watch.loaded = (watch.loaded[0], watch.loaded[1] + 1)
    assert not watch.ready()
    assert watch.checking


def test_codec_replacement_is_detected_without_an_executable_change(watch):
    watch.paths[1].write_text("replacement codec")
    assert not watch.ready()
    assert watch.checking


def test_missing_files_defer_refresh(watch):
    watch.executable.unlink()
    assert not watch.ready()
    assert not watch.checking


@pytest.mark.parametrize("outcome", [1, OSError("not ready"), subprocess.TimeoutExpired("python", 5)])
def test_a_broken_replacement_keeps_the_old_process_alive(watch, monkeypatch, outcome):
    replace_executable(watch)

    def probe(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(returncode=outcome)

    monkeypatch.setattr(runtime.subprocess, "run", probe)
    watch._verify(watch._snapshot())
    assert watch.verified is None
    assert not watch.checking
    assert not watch.ready()


def test_a_second_upgrade_invalidates_an_earlier_successful_probe(watch):
    replace_executable(watch)
    watch.verified = watch._snapshot()
    replace_executable(watch)
    assert not watch.ready()
    assert watch.checking


def app_module():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from nabria import app
    return app


@pytest.mark.parametrize("busy", ["recording", "working", "settings", "wizard", "clipboard"])
def test_runtime_refresh_waits_for_active_work_and_windows(monkeypatch, busy):
    app = app_module()
    monkeypatch.setattr(app.time, "monotonic", lambda: 10.0)
    calls = []
    daemon = SimpleNamespace(
        state=busy if busy in {"recording", "working"} else "idle",
        settings_window=object() if busy == "settings" else None,
        wizard=object() if busy == "wizard" else None,
        runtime_idle_since=9.0 if busy == "clipboard" else 0.0,
        runtime_watch=SimpleNamespace(ready=lambda: calls.append("probe") or True),
        log=lambda message: calls.append(message),
        _quit=lambda: calls.append("quit"),
    )
    assert app.Daemon._refresh_runtime(daemon)
    assert calls == []


def test_an_idle_daemon_refresh_cleans_up_before_replacing_python(monkeypatch):
    app = app_module()
    monkeypatch.setattr(app.time, "monotonic", lambda: 10.0)
    calls = []
    daemon = SimpleNamespace(
        state="idle", settings_window=None, wizard=None,
        runtime_idle_since=0.0, runtime_source=42, restart_requested=False,
        runtime_watch=SimpleNamespace(ready=lambda: True),
        log=lambda message: calls.append("log"),
        _quit=lambda: calls.append("quit"),
    )
    assert not app.Daemon._refresh_runtime(daemon)
    assert daemon.restart_requested
    assert daemon.runtime_source == 0
    assert calls == ["log", "quit"]


def test_replacement_preserves_module_arguments_and_inherited_environment(monkeypatch):
    app = app_module()
    monkeypatch.setattr(app.sys, "argv", ["/install/nabria/__main__.py", "daemon"])
    monkeypatch.setattr(app.sys, "executable", "/usr/bin/python3")
    called = []
    monkeypatch.setattr(app.os, "execv", lambda *args: called.append(args))
    daemon = SimpleNamespace(
        application=SimpleNamespace(run=lambda args: 0), restart_requested=True,
    )
    assert app.Daemon.run(daemon) == 0
    assert called == [("/usr/bin/python3", ["/usr/bin/python3", "-m", "nabria", "daemon"])]


def test_runtime_refresh_waits_for_a_restore_that_has_already_started(monkeypatch):
    import threading

    app = app_module()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []

    def restore(previous, text):
        started.set()
        release.wait(5)
        finished.set()

    monkeypatch.setattr(app.inject, "RESTORE_DELAY", 0)
    monkeypatch.setattr(app.time, "monotonic", lambda: 10.0)
    daemon = SimpleNamespace(
        state="idle", settings_window=None, wizard=None,
        runtime_idle_since=0.0, runtime_source=1, restart_requested=False,
        runtime_watch=SimpleNamespace(ready=lambda: True),
        log=lambda message: calls.append("log"),
        _quit=lambda: calls.append("quit"),
    )
    app.inject._schedule_restore(restore, "previous", "transcript")
    try:
        assert started.wait(2)
        assert app.inject.clipboard_busy()
        assert app.Daemon._refresh_runtime(daemon)
        assert not daemon.restart_requested
        assert calls == []
    finally:
        release.set()
        assert finished.wait(2)
        with app.inject._restore_lock:
            timers = tuple(app.inject._pending_restores)
        for timer in timers:
            timer.join(timeout=2)
    assert not app.inject.clipboard_busy()
    daemon.runtime_idle_since = 0.0
    assert not app.Daemon._refresh_runtime(daemon)
    assert daemon.restart_requested
