from __future__ import annotations

import re

from margin_schema.extraction import ExtractedEntity
from margin_schema.text_normalization import normalize_entity_name


def kind_for_name(name: str) -> str:
    lowered = name.casefold()
    if "scheme" in lowered or "oauth" in lowered:
        return "security_scheme"
    if "standard" in lowered:
        return "standard"
    if "requirement" in lowered:
        return "requirement"
    if lowered.endswith("control"):
        return "control"
    if "board" in lowered.split() or lowered.endswith(" board") or "steering board" in lowered:
        return "team"
    if "team" in lowered:
        return "team"
    if "guide" in lowered or "document" in lowered:
        return "concept"
    if "api" in lowered:
        return "api"
    if "event" in lowered:
        return "event"
    if lowered in {"postgresql", "redis", "minio"}:
        return "technology"
    if "database" in lowered or "db" == lowered:
        return "datastore"
    if "platform" in lowered or "system" in lowered:
        return "system"
    return "component"


def owner_kind_for_name(name: str) -> str:
    """Return the kind an owner is recorded as.

    An owner is whoever is responsible, so it is an actor. A name that names a
    company is an organization; anything else is a team, because a named group
    of people is what holds a record responsible in practice.
    """

    lowered = name.casefold()
    if any(marker in lowered for marker in ("corp", "inc", "ltd", "llc", "plc", "gmbh", "holding")):
        return "organization"
    if "role" in lowered:
        return "role"
    return "team"


def clean_entity_name(name: str) -> str:
    name = re.sub(r"\b(the|a|an)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip(" .:")
    return name


def ensure_entity(entities: list[ExtractedEntity], name: str, role: str, kind: str | None = None) -> bool:
    normalized_name = normalize_entity_name(name)
    if not normalized_name:
        return False
    if any(normalize_entity_name(entity.name) == normalized_name for entity in entities):
        return True
    entities.append(ExtractedEntity(name=name, kind=kind or kind_for_name(name), role=role))
    return True
