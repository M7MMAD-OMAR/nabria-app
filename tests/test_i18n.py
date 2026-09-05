"""The string table, and the bidi rules that make an Arabic window readable.

Most of these are structural: they catch a translation added in one language
and forgotten in the other, or a `{field}` renamed on one side only. Those are
exactly the mistakes that survive review -- the screen still renders, in the
wrong language or with a `KeyError` on a path nobody clicked during testing.
"""

from __future__ import annotations

import re

import pytest

from nabria import config, i18n, models

FIELDS = re.compile(r"\{(\w+)\}")


def test_every_string_exists_in_both_languages():
    for key, entry in i18n.STRINGS.items():
        assert set(entry) == set(i18n.LANGUAGES), key
        for language, text in entry.items():
            assert text.strip(), f"{key} is empty in {language}"


def test_a_translation_never_drops_or_invents_a_field():
    """`{level}` in English and `{المستوى}` in Arabic raises at the call site.

    Not at import, and not on the common path -- only when that one string is
    rendered, which is usually the error message nobody reaches on purpose.
    """
    for key, entry in i18n.STRINGS.items():
        expected = set(FIELDS.findall(entry["en"]))
        for language, text in entry.items():
            assert set(FIELDS.findall(text)) == expected, f"{key} in {language}"


def test_arabic_is_actually_arabic():
    """Guards against a copy-paste that leaves the English text in both slots.

    Skips the entries that are meant to be identical: a language names itself
    in its own script in every translation, so `language.en.label` is
    "English" in Arabic too, and that is correct rather than a missing
    translation.
    """
    same_by_design = {"language.ar.label", "language.en.label"}
    arabic = re.compile(r"[؀-ۿ]")
    for key, entry in i18n.STRINGS.items():
        if key in same_by_design:
            continue
        assert entry["ar"] != entry["en"], f"{key} was never translated"
        assert arabic.search(entry["ar"]), f"{key} has no Arabic in it"


@pytest.mark.parametrize(
    "keys",
    [
        [preset["label"] for preset in config.LANGUAGE_PRESETS.values()],
        [preset["summary"] for preset in config.LANGUAGE_PRESETS.values()],
        [model.summary for model in models.CATALOG.values()],
    ],
)
def test_catalogue_keys_resolve(keys):
    """The catalogues hold i18n keys, so a typo in one is a key rendered raw.

    `t()` returns the key for anything it does not know, which is the right
    behaviour at runtime -- a legible interface beats a traceback -- and
    exactly why nothing would report the typo without this.
    """
    for key in keys:
        if not key:  # `en` ships no summary; there is nothing to say about it
            continue
        assert key in i18n.STRINGS, key


def test_unknown_key_returns_itself_rather_than_raising():
    assert i18n.t("nothing.like.this") == "nothing.like.this"
    assert i18n.t("") == ""


def test_ltr_isolates_both_ends():
    """Both characters, or the isolate never closes and eats the rest of the line."""
    assert i18n.ltr("Ctrl+V") == "⁨Ctrl+V⁩"
    # Anything, not just strings: paths and exceptions are passed in directly
    # rather than being str()-ed at every call site.
    assert i18n.ltr(ValueError("nope")) == "⁨nope⁩"


def test_resolve_reads_the_locale_only_when_asked(monkeypatch):
    # The autouse `ui_language` fixture has already cleared LC_ALL and
    # LC_MESSAGES, so setting LANG here is the whole of the environment.
    monkeypatch.setenv("LANG", "ar_SY.UTF-8")
    assert i18n.resolve("auto") == "ar"
    # An explicit choice is not second-guessed by the locale.
    assert i18n.resolve("en") == "en"
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert i18n.resolve("auto") == "en", "an unsupported locale falls back, not half-way"


def test_selecting_a_language_changes_what_is_rendered():
    # No try/finally: the autouse `ui_language` fixture puts the module global
    # back, which is the whole reason it exists.
    assert i18n.t("wizard.done") == "Done"
    assert not i18n.is_rtl()
    assert i18n.start_align() == 0.0

    i18n.use("ar")
    assert i18n.t("wizard.done") == "تم"
    assert i18n.is_rtl()
    # 1.0, not 0.0: GTK's xalign is absolute, so the left edge stays the left
    # edge and Arabic pinned to it hugs the wrong side of its window.
    assert i18n.start_align() == 1.0


def test_the_dictation_prompt_is_not_translated():
    """`vocabulary` is fed to a speech model, not read by a person.

    It has to stay the same Levantine text whatever language the interface is
    in -- translating it would change what the engine is primed with, which is
    the one thing in this project measured to change the transcript.
    """
    assert config.LANGUAGE_PRESETS["ar"]["vocabulary"] == config.LEVANTINE_PROMPT
    assert config.LEVANTINE_PROMPT not in {
        text for entry in i18n.STRINGS.values() for text in entry.values()
    }


def test_every_string_renders_in_both_languages():
    """Catches a stray brace, which `t()` would otherwise raise on at the one
    call site that uses that string -- often an error message nobody reaches
    on purpose.

    Each string is rendered with no fields at all, because `t()` fills a
    missing one with its own placeholder rather than raising. What is under
    test is the *format string*, not the call site.
    """
    for key, entry in i18n.STRINGS.items():
        for language in entry:
            i18n.use(language)
            rendered = i18n.t(key)
            assert "{{" not in rendered and "}}" not in rendered, (
                f"{key} in {language} rendered its escaped braces literally"
            )


def test_every_field_name_can_actually_be_passed():
    """No field name may collide with `t()`'s own signature.

    `app.type_failed_body` has a field called `key`, and while `t` took its
    lookup argument as a normal named parameter that string was impossible to
    fill: `t("app.type_failed_body", key="Ctrl+V")` raised TypeError rather
    than returning a sentence. It raised inside the handler that tells the
    user their transcript is on the clipboard, so the notification never
    arrived and a take that had transcribed fine was filed as a failure.

    Passing each string its own fields is what distinguishes this from
    `test_every_string_renders_in_both_languages`, which renders with none.
    """
    for key, entry in i18n.STRINGS.items():
        for language, text in entry.items():
            i18n.use(language)
            fields = {name: "x" for name in FIELDS.findall(text)}
            rendered = i18n.t(key, **fields)
            for name in fields:
                assert "{" + name + "}" not in rendered, (
                    f"{key} in {language} did not fill {name}"
                )
