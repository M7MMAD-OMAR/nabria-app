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


@pytest.fixture
def gpu_machine(monkeypatch):
    """The opposite of `cpu_only`, and just as necessary.

    The suite runs on whatever hardware a contributor has, so a test about what
    a machine *with* a graphics card does cannot be left to the machine to
    decide -- it would pass here and quietly stop testing anything on a laptop
    without one.
    """
    card = gpu.Device(index=0, name="test discrete", kind="discrete", vendor=0, product=0)
    monkeypatch.setattr(gpu, "probe", lambda *_a, **_k: [card])
    monkeypatch.setattr(wizard.gpu, "probe", lambda *_a, **_k: [card])


def test_a_cpu_machine_is_not_offered_turbo_by_default(application, cpu_only, fresh_config):
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    assert setup.has_gpu is False
    assert setup._selected_choice().model.needs_gpu is False


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


# -- a model that is already on the machine ---------------------------------


def a_model_file(path, contents: bytes = b"") -> object:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(models.GGML_MAGIC + contents)
    return path


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """A directory the search looks in, standing in for the real list."""
    directory = tmp_path / "elsewhere"
    directory.mkdir()
    monkeypatch.setattr(models, "search_roots", lambda: [(directory, "*.bin")])
    return directory


def test_a_model_already_here_is_offered_and_chosen(
    application, cpu_only, fresh_config, elsewhere
):
    """The whole point: nobody downloads 148 MB they already have.

    Chosen rather than merely listed, because a card somebody has to notice
    and click is not much better than no card at all -- the download would
    already have started by then.
    """
    base = models.CATALOG["base"]
    a_model_file(elsewhere / "ggml-base.bin", b"\0" * (base.size - 4))

    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    chosen = setup._selected_choice()
    assert chosen.found is not None
    assert chosen.found.path == elsewhere / "ggml-base.bin"


def test_a_found_model_does_not_win_a_downgrade(
    application, gpu_machine, fresh_config, elsewhere
):
    """Saving one download is not worth every sentence afterwards.

    Finding the smallest model on a machine that can run the largest is not a
    reason to install the smallest: the saving happens once and the cost is
    paid on every take. The card is still there for anyone who would rather
    skip the download -- it just is not chosen for them.
    """
    base = models.CATALOG["base"]
    a_model_file(elsewhere / "ggml-base.bin", b"\0" * (base.size - 4))

    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    assert any(c.found is not None for c in setup.choices)
    assert setup._selected_choice().model.key == "large-v3-turbo"


def test_a_bigger_model_already_here_beats_the_recommendation(
    application, cpu_only, fresh_config, elsewhere
):
    # The other side of the same rule: `small` is recommended against on a
    # machine with no graphics card only because of the download, and there is
    # no download to weigh once it is already here.
    small = models.CATALOG["small"]
    a_model_file(elsewhere / "ggml-small.bin", b"\0" * (small.size - 4))

    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    assert setup._selected_choice().model.key == "small"
    assert setup._selected_choice().found is not None


def test_the_button_stops_saying_download_for_a_model_that_is_here(
    application, cpu_only, fresh_config, elsewhere
):
    # "Download" over a card reading "already on this machine" reads as the
    # search not having worked.
    base = models.CATALOG["base"]
    a_model_file(elsewhere / "ggml-base.bin", b"\0" * (base.size - 4))

    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    assert setup.fetch.get_label() == i18n.t("wizard.model.use")

    catalogue = [c for c in setup.choices if c.found is None][0]
    catalogue.radio.set_active(True)
    assert setup.fetch.get_label() == i18n.t("wizard.download")


def test_a_model_this_machine_cannot_run_is_offered_but_not_chosen(
    application, cpu_only, fresh_config, elsewhere
):
    """Finding the largest model on a machine without a graphics card is not a
    reason to select it. It runs slower than speech there, which is not a
    slower experience but an unusable one -- so it stays on the page, and the
    choice stays the reader's.
    """
    turbo = models.CATALOG["large-v3-turbo"]
    a_model_file(elsewhere / "ggml-large-v3-turbo.bin", b"\0" * (turbo.size - 4))

    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    assert any(c.found is not None for c in setup.choices)
    assert setup._selected_choice().found is None
    assert setup._selected_choice().model.needs_gpu is False


def test_an_unrecognised_file_is_never_chosen_for_anyone(
    application, fresh_config, elsewhere
):
    # No published copy to check it against, so taking it has to be somebody's
    # decision rather than this program's.
    a_model_file(elsewhere / "ggml-medium.bin", b"\0" * 500)
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    unknown = [c for c in setup.choices if c.found is not None][0]
    assert unknown.model is None
    assert not unknown.radio.get_active()


def test_a_hand_picked_file_is_added_and_chosen(application, fresh_config, tmp_path):
    # The other half of the answer: a model kept somewhere the search has no
    # business looking, which is most places a person might keep one.
    path = a_model_file(tmp_path / "somewhere/mine.bin", b"\0" * 500)
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    setup._offer_file(path)

    chosen = setup._selected_choice()
    assert chosen.found is not None and chosen.found.path == path
    assert sum(1 for c in setup.choices if c.radio.get_active()) == 1


def test_the_same_file_picked_twice_is_listed_once(application, fresh_config, tmp_path):
    path = a_model_file(tmp_path / "mine.bin", b"\0" * 500)
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    setup._offer_file(path)
    setup._offer_file(path)
    assert sum(1 for c in setup.choices if c.found is not None) == 1


def test_a_hand_picked_file_that_is_not_a_model_says_so(
    application, fresh_config, tmp_path
):
    """Said on the page, not swallowed.

    Nothing else on this machine can tell someone that the file they chose is
    not a model: accepting it silently postpones the answer to
    "whisper-server did not start", which names the wrong thing entirely.
    """
    (tmp_path / "photo.bin").write_bytes(b"\x89PNG\r\n\x1a\n")
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    setup._offer_file(tmp_path / "photo.bin")

    assert not any(c.found is not None for c in setup.choices)
    assert setup.model_note.get_text()
    assert setup.model_note.has_css_class("nabria-bad")


def test_choosing_a_model_that_is_here_never_shows_a_download(
    application, fresh_config, elsewhere
):
    """It is linked, not fetched -- and the page must not say otherwise.

    The largest model is checksummed on the way in, which takes long enough to
    need a progress bar; calling that bar a download would be the one piece of
    text on the screen and would be wrong.
    """
    a_model_file(elsewhere / "ggml-medium.bin", b"\0" * 500)
    setup = wizard.Wizard(application, fresh_config.load(), lambda: None)
    setup._offer_file(elsewhere / "ggml-medium.bin")
    setup._begin_download()

    assert setup.download_title.get_text() == i18n.t("wizard.checking")
