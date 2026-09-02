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
import time

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


def test_the_wizard_opens_on_first_run_even_with_a_model_installed(fresh_config):
    """The bug this guards is the whole reason setup_done exists.

    install.sh downloads a model before the daemon ever starts, so a wizard
    triggered only by a missing model never opened on the documented install
    path -- and the language step, and with it the Arabic dialect prompt, never
    ran for anyone who followed the README.

    Asserts `config.needs_setup`, which is what app.py calls. Restating the
    condition here instead would give a test that stays green while the daemon
    changes underneath it.
    """
    fresh_config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (fresh_config.MODEL_DIR / "ggml-base.bin").write_bytes(b"x")
    assert fresh_config.models(), "a model is installed"

    assert fresh_config.needs_setup(fresh_config.load()) is True
    assert fresh_config.needs_setup({**fresh_config.load(), "setup_done": True}) is False


def test_the_wizard_reopens_when_the_model_is_gone(fresh_config):
    # The repair path: a first-run flag on its own would stay marked done while
    # the app was unusable.
    assert fresh_config.needs_setup({"setup_done": True}) is True


# -- warning about a microphone that is not being heard --------------------
#
# The finished-take notice cannot help the case these cover: somebody who
# mutes their input, speaks for a minute and then stops has already lost the
# minute by the time anything can be said about it, and `silent_notice_after`
# waits for three such takes before it speaks at all.


class SilentTake:
    """A recorder that reports a long take with nothing in it."""

    def __init__(self, unheard: bool = True):
        self._unheard = unheard
        self.seconds = 14.0
        self.rms_dbfs = -70.0
        self.warned_unheard = False

    def unheard(self, threshold, after):
        # The verdict travels with the readings it came from, so the caller
        # never has to take the lock again for numbers it already has.
        return self._unheard, self.seconds, self.rms_dbfs


@pytest.fixture
def sent(monkeypatch):
    """Every notification the daemon sends, as (summary, body) pairs."""
    from nabria import app as app_module

    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_module.notify, "send",
        lambda summary, body="", urgency="normal": posted.append((summary, body)),
    )
    # wpctl is not run in a test: the mute state is what is under test, and
    # shelling out would make the result depend on the machine's own audio.
    monkeypatch.setattr(app_module, "_default_input", lambda: ("Test Mic", False))
    return posted


def test_a_take_that_hears_nothing_is_reported_while_it_is_still_recording(
    daemon, sent, monkeypatch
):
    take = SilentTake()
    daemon.recording = take
    monkeypatch.setattr(daemon, "_silence_warning_seconds", lambda: 12.0)

    assert daemon._poll_silence() is False, "the check does not run again after it fires"
    assert take.warned_unheard is True
    # _poll_silence hands the notification to a thread rather than shelling out
    # to wpctl on the main loop, so this waits for it instead of asserting on
    # a race it would lose on a loaded machine.
    for _ in range(100):
        if sent:
            break
        time.sleep(0.01)

    assert len(sent) == 1
    summary, body = sent[0]
    assert "14" in body, "the body reports the take, not the setting"
    assert "12" not in body


def test_a_muted_input_is_named_rather_than_described(daemon, sent, monkeypatch):
    from nabria import app as app_module

    monkeypatch.setattr(app_module, "_default_input", lambda: ("Test Mic", True))
    daemon._warn_unheard(14.0, -42.0)

    summary, body = sent[0]
    assert "muted" in body, "the one cause that can be named outright"
    assert "Test Mic" in body


def test_an_unknown_mute_state_is_not_guessed_at(daemon, sent, monkeypatch):
    # wpctl absent, or PipeWire wedged. Claiming "your microphone is muted"
    # here would send the user to fix something that may be fine.
    from nabria import app as app_module

    monkeypatch.setattr(app_module, "_default_input", lambda: ("the default input", None))
    daemon._warn_unheard(14.0, -42.0)

    assert "muted" not in sent[0][1].split(".")[0]


def test_a_take_that_is_being_heard_is_left_alone(daemon, sent, monkeypatch):
    take = SilentTake(unheard=False)
    daemon.recording = take
    monkeypatch.setattr(daemon, "_silence_warning_seconds", lambda: 12.0)

    daemon._poll_silence()

    assert take.warned_unheard is False
    assert sent == []


def test_the_finished_take_does_not_repeat_what_was_already_said(daemon, sent):
    """One sentence per recording, not two.

    The mid-take warning and the third-silent-take notice are about the same
    fault, so a take that already carried the first must not also trigger the
    second as it finishes.
    """
    daemon.settings["silent_notice_after"] = 1
    take = SilentTake()
    take.warned_unheard = True

    daemon._note_silent_take(take, -70.0, -42.0)

    assert sent == []


def test_a_mid_take_warning_does_not_consume_the_runs_notice(daemon, sent):
    """A suppressed notice must not spend the count that triggers it.

    The trigger is an equality, so advancing `silent_run` for a take whose
    notice was suppressed walks the run past `silent_notice_after` and it can
    never fire again. Measured before the fix: with after=3, a mid-take
    warning on exactly the third take silenced all eight that followed, on a
    microphone that was genuinely dead the whole time.
    """
    daemon.settings["silent_notice_after"] = 3

    for index in range(1, 9):
        take = SilentTake()
        take.warned_unheard = index == 3  # the one that warned mid-recording
        daemon._note_silent_take(take, -70.0, -42.0)

    assert len(sent) == 1, (
        "a dead microphone went unreported for eight takes because one of them "
        "had already warned mid-recording"
    )


def test_the_warning_reports_the_instant_its_verdict_came_from(daemon, monkeypatch):
    """The caller must not re-read what the lock already handed it.

    A recorder is live while this runs: re-reading `seconds` and `rms_dbfs`
    after `unheard()` has released the lock samples a different instant, and
    one chunk of speech landing in that gap makes the notification say
    "nothing has risen above -42 dBFS" while the log line beside it reports a
    level above that threshold.

    The recorder here moves on every property read, which is what a real one
    does while the capture thread is running.
    """
    class MovingTake:
        """Values change on each read, the way a live recording does."""

        def __init__(self):
            self.warned_unheard = False
            self._reads = 0

        def unheard(self, threshold, after):
            return True, 14.0, -70.0

        @property
        def seconds(self):
            self._reads += 1
            return 14.0 + self._reads

        @property
        def rms_dbfs(self):
            self._reads += 1
            return -29.2  # a chunk of speech arrived after the verdict

    logged: list[str] = []
    monkeypatch.setattr(daemon, "log", logged.append)
    monkeypatch.setattr(daemon, "_silence_warning_seconds", lambda: 12.0)
    daemon.recording = MovingTake()
    daemon.takes = 1

    daemon._poll_silence()

    line = [text for text in logged if "warning while still recording" in text][0]
    assert "14s in at -70.0 dBFS" in line, (
        f"the log reported a different instant than the verdict: {line}"
    )


def test_a_wedged_notification_daemon_is_logged_not_swallowed(daemon, monkeypatch):
    """The warning thread has no other net.

    Every other notification path sits inside `_worker`'s except; this one is
    a bare thread. `notify.send` shells out with a timeout, and
    `TimeoutExpired` is raised whatever `check=False` says, so an unhandled
    one would kill the thread with its traceback going to stderr instead of
    to nabria.log: a silent failure inside the feature that exists to stop a
    silent failure.
    """
    from nabria import app as app_module

    logged: list[str] = []
    monkeypatch.setattr(daemon, "log", logged.append)
    monkeypatch.setattr(
        app_module.notify, "send",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("notification daemon wedged")),
    )
    monkeypatch.setattr(app_module, "_default_input", lambda: ("Test Mic", False))

    daemon._warn_unheard(14.0, -42.0)  # must not raise

    assert any("could not send the unheard warning" in line for line in logged)


def test_the_finished_take_notice_still_fires_when_nothing_warned(daemon, sent):
    daemon.settings["silent_notice_after"] = 1
    daemon._note_silent_take(SilentTake(), -70.0, -42.0)
    assert len(sent) == 1


def test_the_warning_can_be_switched_off(daemon, fresh_config):
    # 0 is the documented off switch and `_start` reads it to decide whether
    # to arm the timer at all.
    daemon.settings["silence_warning_seconds"] = 0
    assert daemon._silence_warning_seconds() == 0.0


def test_a_hand_edited_delay_is_repaired_by_config_not_by_the_call_site(fresh_config):
    """The guard belongs in one place, and config.py is it.

    A typo here used to raise inside the take, file the audio into failed/
    and report a broken transcriber. `_coerce_numbers` fixed that class of
    bug for every numeric setting at once by reading types from DEFAULTS, so
    a new setting is covered without anybody remembering to guard its reader.
    This asserts the general mechanism covers the new key, which is what lets
    the reader stay a plain float().
    """
    settings = {**fresh_config.DEFAULTS, "silence_warning_seconds": "soon"}
    warnings = fresh_config._coerce_numbers(settings)

    assert settings["silence_warning_seconds"] == float(
        fresh_config.DEFAULTS["silence_warning_seconds"]
    )
    assert any("silence_warning_seconds" in line for line in warnings), (
        "the repair happened silently; the log has to say what it could not read"
    )


def test_running_on_the_cpu_after_a_gpu_failure_is_reported_once(daemon, sent):
    """A tool that silently halves its own speed is the same misdiagnosis.

    The fallback keeps the take, which is the point, but every take after it
    is slower than the user has any reason to expect and nothing on screen
    says why.
    """
    daemon.whisper.gpu_failed = True

    daemon._note_gpu_fallback()
    daemon._note_gpu_fallback()  # a second take, same session

    assert len(sent) == 1, "one notice per daemon, not one per take"
    # It has to name the log, because the log is where the engine's own reason
    # for refusing the card now lands.
    assert "nabria.log" in sent[0][1]


def test_nothing_is_said_while_the_gpu_is_working(daemon, sent):
    daemon.whisper.gpu_failed = False
    daemon._note_gpu_fallback()
    assert sent == []


def test_both_silence_notices_word_a_muted_input_identically(daemon):
    """One fault, one sentence, whichever path reports it.

    The mid-take warning and the finished-take notice used to hold their own
    copy of these eight lines. A change to the muted wording then had to be
    made twice, and the copy that got missed would stay wrong until somebody
    actually muted their microphone, which is the hardest kind of bug to
    notice.
    """
    mid = daemon._unheard_notice("Test Mic", True, 14.0, -42.0)
    finished = daemon._unheard_notice("Test Mic", True, 30.0, -42.0)

    assert mid == finished, "the two notices worded the same fault differently"
    assert "muted" in mid[1]


def test_an_unknown_mute_state_never_claims_the_microphone_is_muted(daemon):
    # None means wpctl could not say. Claiming "muted" there sends the user to
    # fix something that may be perfectly fine.
    _, body = daemon._unheard_notice("Test Mic", None, 14.0, -42.0)
    assert "muted" not in body.split(".")[0]
    assert "14" in body, "the weaker sentence still reports the take"
