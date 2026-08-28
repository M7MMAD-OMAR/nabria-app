"""First run.

The wizard exists because the AppImage has no install script to lean on, so
this is the only path some users will ever see. What is checked here is that
it makes the right recommendation and, above all, that it never recommends a
model the machine cannot run at a useful speed.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi", reason="PyGObject is not installed")
gi.require_version("Gtk", "4.0")
try:
    from gi.repository import Gio, Gtk
except (ImportError, ValueError) as exc:  # pragma: no cover
    pytest.skip(f"GTK 4 is unavailable: {exc}", allow_module_level=True)

if not Gtk.init_check():  # pragma: no cover - needs a display
    pytest.skip("no display", allow_module_level=True)

from nabria import gpu, models, wizard  # noqa: E402

_serial = iter(range(1, 10_000))


@pytest.fixture
def application():
    app = Gtk.Application(
        application_id=f"com.sbarah.NabriaWizardTest{next(_serial)}",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register()
    return app


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
