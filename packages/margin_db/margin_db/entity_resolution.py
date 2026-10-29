"""Strict compatibility-free entity resolution facade."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from margin_db.entity_catalog import ResolutionResult, add_alias, match_existing_entity, resolve_mention
from margin_db.entity_catalog_lifecycle import merge_entities, retire_entity, supersede_entity


@dataclass(frozen=True, slots=True)
class ResolvedEntity:
    id: str | None
    preferred_label: str | None
    ontology_type: str
    matched_by: str
    lifecycle_status: str | None
    needs_review: bool
    candidate_entity_ids: tuple[str, ...]


class EntityResolver:
    """Resolve only existing canonical IDs; unmatched text becomes a mention."""

    def resolve_existing(
        self,
        session: Session,
        *,
        text: str,
        ontology_type: str,
        domain_id: str,
        external_id: str | None = None,
        supplied_candidate_ids: Iterable[str] = (),
        canonical_id: str | None = None,
    ) -> ResolvedEntity:
        return _as_resolved(
            match_existing_entity(
                session,
                text=text,
                ontology_type=ontology_type,
                domain_id=domain_id,
                external_id=external_id,
                supplied_candidate_ids=supplied_candidate_ids,
                canonical_id=canonical_id,
            )
        )

    def resolve_mention(
        self,
        session: Session,
        *,
        text: str,
        ontology_type: str,
        domain_id: str,
        source_element_id: str,
        idea_block_revision_id: str,
        external_id: str | None = None,
        context: dict[str, object] | None = None,
        supplied_candidate_ids: Iterable[str] = (),
        canonical_id: str | None = None,
    ) -> ResolvedEntity:
        return _as_resolved(
            resolve_mention(
                session,
                text=text,
                proposed_type=ontology_type,
                domain_id=domain_id,
                source_element_id=source_element_id,
                idea_block_revision_id=idea_block_revision_id,
                external_id=external_id,
                context=context,
                supplied_candidate_ids=supplied_candidate_ids,
                canonical_id=canonical_id,
            )
        )


def _as_resolved(result: ResolutionResult) -> ResolvedEntity:
    entity = result.entity
    return ResolvedEntity(
        id=entity.id if entity else None,
        preferred_label=entity.preferred_label if entity else None,
        ontology_type=entity.ontology_type if entity else "",
        matched_by=result.matched_by,
        lifecycle_status=entity.lifecycle_status if entity else None,
        needs_review=result.needs_review,
        candidate_entity_ids=result.candidate_entity_ids,
    )


__all__ = ["EntityResolver", "ResolvedEntity", "add_alias", "merge_entities", "retire_entity", "supersede_entity"]
