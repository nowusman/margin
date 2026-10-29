"""Persist ExtractedRelationship occurrences to RelationshipProvenance.

This module isolates the per-relationship persistence block from
``margin_db.extraction`` so the persistence service stays under the repository
size limit. The behaviour is unchanged: each relationship occurrence
becomes one ``RelationshipProvenance`` row anchored to the parent
IdeaBlock's evidence.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from margin_db.relationship_evidence import (
    close_relationship_if_contradicted_by_existing,
    close_relationships_contradicted_by,
    refresh_relationship_evidence_aggregation,
    upsert_relationship_evidence,
)
from margin_schema.extraction import ExtractedIdeaBlock
from margin_schema.text_normalization import normalize_entity_name


class _AnonymousOccurrence:
    """Lightweight stand-in for relationship evidence occurrences."""

    __slots__ = ("source_element_id", "source_quote", "polarity")

    def __init__(self, *, source_element_id: str, source_quote: str, polarity: str = "supports") -> None:
        self.source_element_id = source_element_id
        self.source_quote = source_quote
        self.polarity = polarity


def _create_review_task(session: Session, *, task_type: str, target_type: str, target_id: str, reason: str) -> int:
    from margin_db.extraction import _create_review_task as _persist_review_task

    return _persist_review_task(
        session, task_type=task_type, target_type=target_type, target_id=target_id, reason=reason
    )


def _resolve_relationship_occurrences(
    relationship: Any, resolved_evidence: list[tuple[Any, Any]]
) -> list[_AnonymousOccurrence]:
    relationship_evidence = list(relationship.evidence)
    if not relationship_evidence:
        relationship_evidence = [
            _AnonymousOccurrence(
                source_element_id=extracted.source_element_id,
                source_quote=extracted.source_quote,
                polarity=extracted.polarity,
            )
            for extracted, _element in resolved_evidence
        ]
    for occurrence in relationship_evidence:
        if not getattr(occurrence, "source_quote", "").strip():
            occurrence.source_quote = next(
                (
                    extracted.source_quote
                    for extracted, _element in resolved_evidence
                    if extracted.source_element_id == getattr(occurrence, "source_element_id", None)
                ),
                "",
            )
    return relationship_evidence


def _persist_relationship_occurrences(
    session: Session,
    *,
    candidate: ExtractedIdeaBlock,
    relationship: Any,
    idea_block_id: str,
    entity_by_name: dict[str, str],
    entity_resolution_review: dict[str, bool],
    resolved_evidence: list[tuple[Any, Any]],
    auto_verified: bool,
) -> tuple[set[str], int]:
    source_name = normalize_entity_name(relationship.source)
    target_name = normalize_entity_name(relationship.target)
    source_entity_id = entity_by_name[source_name]
    target_entity_id = entity_by_name[target_name]
    relationship_verified = (
        auto_verified
        and relationship.confidence == "extracted"
        and not entity_resolution_review[source_name]
        and not entity_resolution_review[target_name]
    )
    occurrences = _resolve_relationship_occurrences(relationship, resolved_evidence)
    relationship_ids: set[str] = set()
    review_count = 0
    for occurrence in occurrences:
        persisted = upsert_relationship_evidence(
            session,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            relation_type=relationship.relation_type,
            confidence=relationship.confidence,
            status="verified" if relationship_verified else "candidate",
            idea_block_id=idea_block_id,
            source_element_id=occurrence.source_element_id,
            source_quote=occurrence.source_quote,
            extraction_method=candidate.extraction_method,
            raw_predicate=getattr(relationship, "raw_predicate", None),
            polarity=getattr(occurrence, "polarity", None) or relationship.polarity,
        )
        relationship_ids.add(persisted.id)
        closed_ids = close_relationships_contradicted_by(session, persisted, actor="extraction")
        if closed_ids:
            persisted.metadata_ = {**(persisted.metadata_ or {}), "closed_relationship_ids": closed_ids}
        superseded_ids = close_relationship_if_contradicted_by_existing(session, persisted, actor="extraction")
        if superseded_ids:
            persisted.metadata_ = {**(persisted.metadata_ or {}), "superseded_by_existing_contradiction": True}
        refresh_relationship_evidence_aggregation(
            session, source_entity_id=source_entity_id, target_entity_id=target_entity_id
        )
        if not relationship_verified:
            review_count += _create_review_task(
                session,
                task_type="relationship_review",
                target_type="entity_relationship",
                target_id=persisted.id,
                reason="Relationship requires review before verification",
            )
    return relationship_ids, review_count
