"""Transcript cleaning.

Whisper reliably invents a polite sentence out of room tone, in whichever
language it guessed. Those are training data bleeding through, never something
anyone said, and typing them into a document is worse than typing nothing.
The RMS gate catches most of it; this catches the rest.
"""

from __future__ import annotations

from nabria.whisper import clean


def test_leading_space_is_trimmed():
    # Whisper emits a leading space on essentially every segment.
    assert clean(" hello there") == "hello there"


def test_internal_whitespace_is_collapsed():
    assert clean("hello\n  there\tworld") == "hello there world"


def test_english_hallucinations_are_dropped():
    for phrase in ("Thanks for watching!", "thank you", "  Bye. ", "You"):
        assert clean(phrase) == "", phrase


def test_arabic_hallucinations_are_dropped():
    assert clean("شكرا للمشاهدة") == ""
    assert clean("اشتركوا في القناة") == ""


def test_arabic_matching_survives_diacritics_and_alef_variants():
    # The same sentence comes back spelled differently run to run, so the
    # filter normalises before matching -- otherwise it catches one spelling
    # and lets the next one through.
    assert clean("شُكْرًا للمشاهدة") == ""
    assert clean("شكراً للمشاهدة.") == ""


def test_a_real_sentence_containing_a_hallucination_phrase_is_kept():
    # The filter matches whole transcripts, not substrings. Someone dictating
    # "thank you for the report" must not lose it.
    assert clean("thank you for the report") == "thank you for the report"
    assert clean("شكرا للمشاهدة يا شباب") == "شكرا للمشاهدة يا شباب"


def test_empty_and_whitespace_only():
    assert clean("") == ""
    assert clean("   \n ") == ""
