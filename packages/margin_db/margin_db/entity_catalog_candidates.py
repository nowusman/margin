"""Bounded, reproducible Entity candidates for extraction windows."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from margin_db.entity_models import Entity, EntityAlias
from margin_schema.ontology import ONTOLOGY_VERSION, normalize_entity_kind
from margin_schema.text_normalization import normalize_entity_name


@dataclass(frozen=True, slots=True)
class VocabularyCandidate:
    canonical_id: str
    preferred_label: str
    ontology_type: str
    selected_aliases: tuple[str, ...]
    definition_summary: str | None
    ontology_version: str

    def prompt_payload(self) -> dict[str, object]:
        return {
            "canonical_id": self.canonical_id,
            "preferred_label": self.preferred_label,
            "ontology_type": self.ontology_type,
            "selected_aliases": list(self.selected_aliases),
            "definition_summary": self.definition_summary,
            "ontology_version": self.ontology_version,
        }


def select_relevant_candidates(
    session: Session,
    *,
    domain_id: str,
    source_text: str,
    expected_ontology_types: Iterable[str] = (),
    nearby_entity_ids: Iterable[str] = (),
    limit: int = 24,
) -> tuple[VocabularyCandidate, ...]:
    if limit <= 0:
        return ()
    if limit > 64:
        raise ValueError("candidate limit must not exceed 64")
    expected_types = _normalized_types(expected_ontology_types)
    source_normalized = f" {normalize_entity_name(source_text)} "
    statement = select(Entity).where(Entity.lifecycle_status == "active", _domain_scope(Entity.domain_id, domain_id))
    if expected_types:
        statement = statement.where(Entity.ontology_type.in_(expected_types))
    entities = list(session.scalars(statement.order_by(Entity.id)).all())
    aliases_by_entity = _active_aliases_by_entity(session, [entity.id for entity in entities])
    nearby = {value for value in nearby_entity_ids if value}
    ranked: list[tuple[int, Entity, tuple[str, ...]]] = []
    for entity in entities:
        aliases = aliases_by_entity.get(entity.id, ())
        matched_aliases = tuple(
            alias for alias in (entity.preferred_label, *aliases) if _phrase_in_text(alias, source_normalized)
        )
        score = 2 if entity.id in nearby else 0
        score += 3 if matched_aliases else 0
        if score:
            ranked.append((score, entity, matched_aliases))
    ranked.sort(key=lambda item: (-item[0], item[1].ontology_type, item[1].preferred_label.casefold(), item[1].id))
    return tuple(
        VocabularyCandidate(
            canonical_id=entity.id,
            preferred_label=entity.preferred_label,
            ontology_type=entity.ontology_type,
            selected_aliases=tuple(dict.fromkeys(matched_aliases))[:4],
            definition_summary=_definition_summary(entity.definition),
            ontology_version=ONTOLOGY_VERSION,
        )
        for _score, entity, matched_aliases in ranked[:limit]
    )


def _active_aliases_by_entity(session: Session, entity_ids: Iterable[str]) -> dict[str, tuple[str, ...]]:
    ids = tuple(entity_ids)
    if not ids:
        return {}
    rows = session.scalars(
        select(EntityAlias).where(EntityAlias.entity_id.in_(ids), EntityAlias.lifecycle_status == "active")
    ).all()
    result: dict[str, list[str]] = {}
    for alias in rows:
        result.setdefault(alias.entity_id, []).append(alias.alias)
    return {entity_id: tuple(sorted(values, key=str.casefold)) for entity_id, values in result.items()}


def _domain_scope(column: InstrumentedAttribute[str | None], domain_id: str) -> ColumnElement[bool]:
    return or_(column == domain_id, column.is_(None))


def _normalized_types(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({value for item in values if (value := normalize_entity_kind(item)) is not None}))


def _phrase_in_text(value: str, normalized_source: str) -> bool:
    normalized = normalize_entity_name(value)
    return bool(normalized and f" {normalized} " in normalized_source)


def _definition_summary(definition: str | None) -> str | None:
    return definition.strip()[:240] if definition else None
