from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from margin_schema.text_normalization import normalize_canonical_text


@dataclass(frozen=True, slots=True)
class VocabularyTerm:
    """Controlled vocabulary term with governance and relationship metadata."""

    preferred_term: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    deprecated_terms: tuple[str, ...] = field(default_factory=tuple)
    broader_terms: tuple[str, ...] = field(default_factory=tuple)
    narrower_terms: tuple[str, ...] = field(default_factory=tuple)
    related_terms: tuple[str, ...] = field(default_factory=tuple)
    steward: str | None = None


@dataclass(frozen=True, slots=True)
class ControlledVocabulary:
    """A YAML-round-trippable controlled vocabulary document."""

    terms: tuple[VocabularyTerm, ...]
    version: int = 1


def load_vocabulary_yaml(text: str) -> ControlledVocabulary:
    """Parse controlled vocabulary YAML into stable dataclasses."""

    payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ValueError("Controlled vocabulary YAML must be a mapping.")

    raw_terms = payload.get("terms", [])
    if not isinstance(raw_terms, list):
        raise ValueError("Controlled vocabulary YAML field 'terms' must be a list.")

    terms = tuple(_term_from_mapping(item) for item in raw_terms)
    _validate_vocabulary_terms(terms)
    return ControlledVocabulary(terms=terms, version=int(payload.get("version", 1)))


def dump_vocabulary_yaml(vocabulary: ControlledVocabulary) -> str:
    """Serialize controlled vocabulary dataclasses without losing governance fields."""

    payload = {"version": vocabulary.version, "terms": [_term_to_mapping(term) for term in vocabulary.terms]}
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)


def _validate_vocabulary_terms(terms: tuple[VocabularyTerm, ...]) -> None:
    names: dict[str, str] = {}
    for term in terms:
        preferred = _normalize_name(term.preferred_term)
        if not preferred:
            raise ValueError("Vocabulary preferred term cannot be blank.")
        for name in (term.preferred_term, *term.aliases, *term.deprecated_terms):
            normalized = _normalize_name(name)
            if not normalized:
                raise ValueError("Vocabulary aliases cannot be blank.")
            owner = names.get(normalized)
            if owner is not None and owner != preferred:
                raise ValueError(f"Vocabulary name is assigned to more than one term: {name}")
            names[normalized] = preferred


def _term_from_mapping(item: Any) -> VocabularyTerm:
    if not isinstance(item, dict):
        raise ValueError("Each controlled vocabulary term must be a mapping.")

    preferred = item.get("preferred_term", item.get("preferred"))
    if not preferred or not isinstance(preferred, str):
        raise ValueError("Each controlled vocabulary term requires a preferred term.")

    return VocabularyTerm(
        preferred_term=preferred,
        aliases=_tuple_field(item, "aliases"),
        deprecated_terms=_tuple_field(item, "deprecated_terms", "deprecated"),
        broader_terms=_tuple_field(item, "broader_terms", "broader"),
        narrower_terms=_tuple_field(item, "narrower_terms", "narrower"),
        related_terms=_tuple_field(item, "related_terms", "related"),
        steward=_optional_str(item.get("steward")),
    )


def _term_to_mapping(term: VocabularyTerm) -> dict[str, Any]:
    payload: dict[str, Any] = {"preferred_term": term.preferred_term}
    _put_list(payload, "aliases", term.aliases)
    _put_list(payload, "deprecated_terms", term.deprecated_terms)
    _put_list(payload, "broader_terms", term.broader_terms)
    _put_list(payload, "narrower_terms", term.narrower_terms)
    _put_list(payload, "related_terms", term.related_terms)
    if term.steward is not None:
        payload["steward"] = term.steward
    return payload


def _tuple_field(item: dict[str, Any], *keys: str) -> tuple[str, ...]:
    for key in keys:
        if key in item:
            value = item[key]
            break
    else:
        return ()

    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"Controlled vocabulary field '{keys[0]}' must be a list.")
    if not all(isinstance(entry, str) for entry in value):
        raise ValueError(f"Controlled vocabulary field '{keys[0]}' must contain strings only.")
    return tuple(value)


def _put_list(payload: dict[str, Any], key: str, values: tuple[str, ...]) -> None:
    if values:
        payload[key] = list(values)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Controlled vocabulary field 'steward' must be a string.")
    return value


def _normalize_name(name: str) -> str:
    return normalize_canonical_text(name)
