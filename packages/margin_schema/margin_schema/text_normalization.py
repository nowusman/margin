from __future__ import annotations

import unicodedata


def normalize_canonical_text(value: str) -> str:
    """Normalize Unicode, case, and whitespace for stored canonical text."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def normalize_statement(value: str) -> str:
    """Normalize an IdeaBlock statement without removing meaningful punctuation."""
    return normalize_canonical_text(value)


def normalize_entity_name(value: str) -> str:
    """Normalize an entity name to Unicode letter and number runs."""
    return " ".join(_tokenize(value))


def tokenize_search(value: str) -> tuple[str, ...]:
    """Return deterministic Unicode-safe lexical search tokens."""
    return tuple(_tokenize(value))


def tokenize_keywords(value: str, *, stopwords: frozenset[str] = frozenset()) -> frozenset[str]:
    """Return Unicode-safe tokens used by similarity calculations."""
    return frozenset(token for token in _tokenize(value) if token not in stopwords)


def _tokenize(value: str) -> list[str]:
    normalized = normalize_canonical_text(value)
    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category[0] in {"L", "N"} or (current and category[0] == "M"):
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


__all__ = [
    "normalize_canonical_text",
    "normalize_entity_name",
    "normalize_statement",
    "tokenize_keywords",
    "tokenize_search",
]
