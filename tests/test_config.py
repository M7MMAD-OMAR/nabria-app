"""Settings.

The load path has one job beyond reading JSON: never stop dictation from
working. A hand-edited file with a trailing comma in it should cost the user
their customisations for that session, not the ability to dictate.
"""

from __future__ import annotations

import json


def test_defaults_are_returned_when_nothing_is_saved(fresh_config):
    settings = fresh_config.load()
    assert settings["language"] == fresh_config.DEFAULTS["language"]


def test_saved_values_overlay_the_defaults(fresh_config):
    fresh_config.save({"language": "ar", "threads": 4})
    settings = fresh_config.load()
    assert settings["language"] == "ar"
    assert settings["threads"] == 4
    assert "silence_threshold_dbfs" in settings  # untouched keys still present


def test_a_corrupt_config_does_not_stop_dictation(fresh_config):
    fresh_config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fresh_config.CONFIG_PATH.write_text("{ not json at all", encoding="utf-8")
    settings = fresh_config.load()
    assert settings == {
        **fresh_config.DEFAULTS,
        "server_binary": settings["server_binary"],
        "model": settings["model"],
    }


def test_unknown_keys_are_kept_not_dropped(fresh_config):
    # A config written by a newer version must survive a downgrade.
    fresh_config.save({"from_the_future": 1})
    assert fresh_config.load()["from_the_future"] == 1


def test_arabic_is_saved_readable_not_escaped(fresh_config):
    fresh_config.save({**fresh_config.DEFAULTS, "vocabulary": "هلأ بحكي"})
    raw = fresh_config.CONFIG_PATH.read_text(encoding="utf-8")
    assert "هلأ بحكي" in raw
    assert "\\u" not in raw


def test_paths_are_expanded(fresh_config):
    fresh_config.save({"model": "~/model.bin"})
    assert not fresh_config.load()["model"].startswith("~")


def test_models_lists_only_finished_downloads(fresh_config):
    fresh_config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (fresh_config.MODEL_DIR / "ggml-base.bin").write_bytes(b"x")
    (fresh_config.MODEL_DIR / "ggml-small.bin").write_bytes(b"x")
    # A half-finished download leaves a sidecar; offering it in the picker
    # would load a truncated model and fail with something unhelpful.
    (fresh_config.MODEL_DIR / "ggml-small.bin.part").write_bytes(b"")

    names = [path.name for path in fresh_config.models()]
    assert names == ["ggml-base.bin"]


def test_round_trip_through_json_is_stable(fresh_config):
    fresh_config.save(fresh_config.DEFAULTS)
    assert json.loads(fresh_config.CONFIG_PATH.read_text(encoding="utf-8"))
