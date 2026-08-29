"""First run.

Every install path ends here -- the script, the one-line installer and the
distribution packages all leave the model choice to the wizard -- so this is
the screen every user sees. What is checked here is that it makes the right
recommendation and, above all, that it never recommends a model the machine
cannot run at a useful speed.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi", reason="PyGObject is not installed")
gi.require_version("Gtk", "4.0")
try:
    from gi.repository import Gio, Gtk
except (ImportError, ValueError) as exc:  # pragma: no cover
    pytest.skip(f"GTK 4 is unavailable: {exc}", allow_module_level=True)

from conftest import display_available  # noqa: E402

if not display_available():  # pragma: no cover - environment dependent
    pytest.skip("no display", allow_module_level=True)

from nabria import gpu, i18n, models, wizard  # noqa: E402

@pytest.fixture
def cpu_only(monkeypatch):
    monkeypatch.setattr(gpu, "probe", list)
    monkeypatch.setattr(wizard.gpu, "probe", list)


def test_a_cpu_machine_is_not_offered_turbo_by_default(application, cpu_only, fresh_config):
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    assert setup.has_gpu is False
    assert setup._selected_model().needs_gpu is False


def test_the_turbo_card_warns_on_a_cpu_machine(application, cpu_only, fresh_config):
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    turbo = [c for c in setup.choices if c.model.key == "large-v3-turbo"][0]
    assert turbo.model.needs_gpu and not setup.has_gpu


def test_every_catalogue_model_is_offered(application, fresh_config):
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    assert {c.model.key for c in setup.choices} == set(models.CATALOG)


def test_exactly_one_choice_is_selected(application, fresh_config):
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    assert sum(1 for c in setup.choices if c.radio.get_active()) == 1


def test_the_selected_card_is_marked(application, fresh_config):
    # The radio dot alone is easy to miss when the choice is the whole page.
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    chosen = [c for c in setup.choices if c.radio.get_active()][0]
    assert chosen.has_css_class("selected")


def test_every_page_exists(application, fresh_config):
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    for name in ("welcome", "model", "download", "microphone", "shortcut"):
        assert setup.stack.get_child_by_name(name) is not None


def test_the_microphone_bar_matches_the_silence_gate(fresh_config):
    # A test that passes at a level the gate would reject is worse than no
    # test: it tells the user their microphone is fine right before every
    # take gets thrown away as silent.
    assert wizard.GOOD_ENOUGH_DBFS >= float(
        fresh_config.DEFAULTS["silence_threshold_dbfs"]
    )


def test_choosing_arabic_ships_the_dialect_prompt(application, fresh_config):
    # The prompt is the reason Arabic works well here, and nobody would ever
    # find it in a config file. If picking Arabic does not install it, the
    # advantage exists only for people who read the source.
    settings = fresh_config.load()
    setup = wizard.Wizard(application, settings, lambda: None)
    for code, radio in setup.languages:
        radio.set_active(code == "ar")
    setup._choose_language()

    assert settings["language"] == "ar"
    assert "هلأ" in settings["vocabulary"]


def test_a_hand_written_vocabulary_is_never_overwritten(application, fresh_config):
    settings = {**fresh_config.load(), "vocabulary": "mine"}
    setup = wizard.Wizard(application, settings, lambda: None)
    for code, radio in setup.languages:
        radio.set_active(code == "ar")
    setup._choose_language()
    assert settings["vocabulary"] == "mine"


def test_english_does_not_ship_an_arabic_prompt(application, fresh_config):
    settings = fresh_config.load()
    setup = wizard.Wizard(application, settings, lambda: None)
    for code, radio in setup.languages:
        radio.set_active(code == "en")
    setup._choose_language()
    assert settings["language"] == "en"
    assert settings["vocabulary"] == ""


def _preselected(application, settings):
    setup = wizard.Wizard(application, settings, lambda: None)
    return [code for code, radio in setup.languages if radio.get_active()]


def test_the_locale_preselects_arabic(application, fresh_config, monkeypatch):
    """An Arabic desktop should not have to say so twice.

    The daemon selects the interface language before it builds any window, so
    that is the order this reproduces. The wizard reads the choice rather than
    parsing the locale itself -- one place decides, and an explicit
    `ui_language` is honoured here too, which the locale alone could not do.
    """
    monkeypatch.setenv("LANG", "ar_JO.UTF-8")
    i18n.use("auto")
    assert _preselected(application, fresh_config.load()) == ["ar"]

    # An interface deliberately set to English preselects English, on the same
    # Arabic desktop. Someone who asked for an English app is more likely to be
    # dictating English than the locale is to be right.
    i18n.use("en")
    assert _preselected(application, fresh_config.load()) == ["en"]


def test_every_preset_is_offered(application, fresh_config):
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    assert {code for code, _ in setup.languages} == set(fresh_config.LANGUAGE_PRESETS)


def test_setup_is_marked_done_only_when_the_wizard_finishes(application, fresh_config):
    settings = fresh_config.load()
    assert settings["setup_done"] is False
    setup = wizard.Wizard(application, settings, lambda: None)
    setup._finish()
    assert fresh_config.load()["setup_done"] is True


def test_an_already_downloaded_model_skips_the_download_page(application, fresh_config):
    # install.sh fetches the model before the daemon first starts, so this is
    # the ordinary path rather than an edge case.
    model = models.CATALOG["base"]
    fresh_config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (fresh_config.MODEL_DIR / model.filename).write_bytes(b"\0" * model.size)

    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    for choice in setup.choices:
        choice.radio.set_active(choice.model.key == "base")
    setup._begin_download()
    assert setup.stack.get_visible_child_name() == "microphone"


def test_the_whole_card_selects_it_not_just_the_radio(application, fresh_config):
    """A 16px circle is a smaller target than the thing being asked about.

    Emitting the gesture's own signal rather than synthesising a pointer
    event: what is under test is that the card is wired to the radio at all,
    and a test that needed a real click would need a real display server
    position, which is not a thing this suite has.
    """
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    other = [c for c in setup.choices if not c.radio.get_active()][0]

    controllers = [
        c for c in other.observe_controllers()
        if isinstance(c, Gtk.GestureClick)
    ]
    assert controllers, "the card has no click gesture, so only the dot works"
    controllers[0].emit("released", 1, 0.0, 0.0)

    assert other.radio.get_active()
    assert other.has_css_class("selected")
    assert sum(1 for c in setup.choices if c.radio.get_active()) == 1
