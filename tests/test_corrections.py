from nabria.corrections import Corrections


def test_exact_arabic_words_and_punctuation():
    rules = Corrections({"سبارة": "صبارة"})
    assert rules.apply("سبارة، مشروع سبارة.") == "صبارة، مشروع صبارة."
    assert rules.apply("سبارات وسبارة سبارة_اسم سبارة2 سبارةَ") == (
        "سبارات وسبارة سبارة_اسم سبارة2 سبارةَ"
    )


def test_longest_phrase_wins_without_cascading():
    rules = Corrections({"a": "b", "a b": "c", "c": "d"})
    assert rules.apply("a b, a, c") == "c, b, d"


def test_literal_patterns_replacements_and_case():
    rules = Corrections({"C++": r"C:\code", "git hub": "GitHub"})
    assert rules.apply("C++ git hub Git Hub") == r"C:\code GitHub Git Hub"


def test_invalid_or_empty_rules_cannot_delete_a_transcript():
    for value in (None, [], "invalid", {"": "x", "word": "", 1: "x", "x": None}):
        assert Corrections(value).apply("word x") == "word x"
