"""Exact, user-owned dictation corrections without another inference pass."""

from __future__ import annotations

import re


class Corrections:
    def __init__(self, rules: object):
        self.rules = {
            source.strip(): target.strip()
            for source, target in (rules.items() if isinstance(rules, dict) else [])
            if isinstance(source, str) and isinstance(target, str)
            and source.strip() and target.strip()
        }
        # Combining marks belong to the adjacent word even though Python's
        # \w excludes them. Never replace part of a vocalised Arabic word.
        word = r"\w\u0300-\u036f\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed"
        alternatives = "|".join(
            re.escape(source) for source in sorted(self.rules, key=len, reverse=True)
        )
        self.pattern = (
            re.compile(rf"(?<![{word}])(?:{alternatives})(?![{word}])")
            if alternatives else None
        )

    def apply(self, text: str) -> str:
        if self.pattern is None:
            return text
        # One pass prevents a correction's output becoming another rule's
        # input. A callable keeps backslashes in names literal as well.
        return self.pattern.sub(lambda match: self.rules[match.group()], text)
