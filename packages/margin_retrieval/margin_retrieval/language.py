"""Detection of the language a question is written in.

The answer is written in the language of the question unless the caller asks
for another one. Detection is deliberately coarse: it recognises scripts
rather than dialects, because a wrong guess of a specific locale is worse than
a plain answer in the reader's own script.
"""

from __future__ import annotations

import unicodedata

_SCRIPT_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("CJK", "Chinese"),
    ("HIRAGANA", "Japanese"),
    ("KATAKANA", "Japanese"),
    ("HANGUL", "Korean"),
    ("CYRILLIC", "Russian"),
    ("ARABIC", "Arabic"),
    ("HEBREW", "Hebrew"),
    ("DEVANAGARI", "Hindi"),
    ("THAI", "Thai"),
    ("GREEK", "Greek"),
)


def detect_output_language(text: str) -> str:
    """Return the language of a question, or an empty string when unknown."""

    counts: dict[str, int] = {}
    for character in text:
        if not character.isalpha():
            continue
        try:
            name = unicodedata.name(character)
        except ValueError:
            continue
        script = name.split(" ")[0]
        counts[script] = counts.get(script, 0) + 1

    if not counts:
        return ""
    for script, language in _SCRIPT_LANGUAGES:
        if counts.get(script, 0) > 0:
            return language
    return ""
