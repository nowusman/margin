from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from margin_db.entity_models import Entity
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.review_models import ReviewTask
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_schema.ids import relationship_id
from margin_schema.mappings import CONFIDENCE_STATES
from margin_schema.ontology import (
    RELATIONSHIP_REGISTRY,
    RelationPersistence,
    canonical_relationship_endpoints,
    relationship_definition,
)


class RelationshipApprovalError(ValueError):
    """Raised when a candidate relationship cannot be approved or rejected."""


ACTIVE_RELATIONSHIP_STATUSES = frozenset({"candidate", "verified"})


def approve_candidate_relationship(
    session: Session, relationship_id_value: str, *, actor: str = "system"
) -> EntityRelationship:
    relationship = session.get(EntityRelationship, relationship_id_value)
    if relationship is None:
        raise RelationshipApprovalError(f"relationship not found: {relationship_id_value}")
    if relationship.status not in {"candidate", "verified"}:
        raise RelationshipApprovalError(
            f"relationship {relationship_id_value} cannot be approved from {relationship.status}"
        )
    _review_evidence(session, relationship, status="verified")
    relationship.status = "verified"
    relationship.confidence = "verified"
    relationship.metadata_ = {**(relationship.metadata_ or {}), "approved_by": actor, "approval_policy": "human_review"}
    closed_ids = close_relationships_contradicted_by(
        session, relationship, valid_to=relationship.valid_from, actor=actor
    )
    if closed_ids:
        relationship.metadata_ = {**(relationship.metadata_ or {}), "closed_relationship_ids": closed_ids}
    _refresh(session, relationship)
    sync_inverse_relationship(session, relationship)
    _close_open_review_tasks(session, target_id=relationship.id)
    session.flush()
    return relationship


def reject_candidate_relationship(
    session: Session,
    relationship_id_value: str,
    *,
    actor: str = "system",
    reason: str = "rejected during relationship review",
) -> EntityRelationship:
    relationship = session.get(EntityRelationship, relationship_id_value)
    if relationship is None:
        raise RelationshipApprovalError(f"relationship not found: {relationship_id_value}")
    _review_evidence(session, relationship, status="rejected")
    relationship.status = "rejected"
    relationship.metadata_ = {**(relationship.metadata_ or {}), "rejected_by": actor, "rejection_reason": reason}
    _refresh(session, relationship)
    sync_inverse_relationship(session, relationship)
    _close_open_review_tasks(session, target_id=relationship.id)
    session.flush()
    return relationship


def upsert_relationship_evidence(
    session: Session,
    *,
    source_entity_id: str,
    target_entity_id: str,
    relation_type: str,
    confidence: str,
    status: str,
    idea_block_id: str,
    source_element_id: str,
    polarity: str = "supports",
    source_quote: str = "",
    extraction_method: str = "deterministic",
    raw_predicate: str | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    qualifiers: dict | None = None,
) -> EntityRelationship:
    """Upsert a relationship with its single evidence occurrence.

    Each call persists one ``RelationshipProvenance`` row anchored to the
    parent IdeaBlock's evidence and source element.
    """
    if polarity not in {"supports", "refutes"}:
        raise ValueError("relationship evidence polarity must be supports or refutes")
    if not isinstance(status, str) or status not in {"candidate", "verified", "rejected", "stale"}:
        raise ValueError("relationship evidence status is invalid")
    if not isinstance(confidence, str) or confidence.strip().casefold() not in CONFIDENCE_STATES:
        raise ValueError("relationship evidence confidence is invalid")
    confidence = confidence.strip().casefold()
    if not isinstance(extraction_method, str) or not extraction_method.strip():
        raise ValueError("relationship evidence extraction_method is required")
    if valid_from is not None and (not isinstance(valid_from, date) or isinstance(valid_from, datetime)):
        raise ValueError("relationship evidence valid_from must be a date")
    if valid_to is not None and (not isinstance(valid_to, date) or isinstance(valid_to, datetime)):
        raise ValueError("relationship evidence valid_to must be a date")
    if valid_from is not None and valid_to is not None and valid_to < valid_from:
        raise ValueError("relationship evidence valid_to cannot precede valid_from")
    if qualifiers is not None and not isinstance(qualifiers, dict):
        raise ValueError("relationship evidence qualifiers must be an object")
    definition = relationship_definition(relation_type)
    relation_type = definition.name
    source_entity_id, target_entity_id = canonical_relationship_endpoints(
        relation_type, source_entity_id, target_entity_id
    )

    if definition.persistence_class is not RelationPersistence.ENTITY:
        raise ValueError(f"Relationship {relation_type} is not persisted as an EntityRelationship")
    if source_entity_id == target_entity_id:
        raise ValueError("Entity relationships require distinct endpoints")
    source_entity = session.get(Entity, source_entity_id)
    target_entity = session.get(Entity, target_entity_id)
    if source_entity is None or target_entity is None:
        raise ValueError(f"Relationship {relation_type} requires existing entity endpoints")
    if source_entity.lifecycle_status != "active" or target_entity.lifecycle_status != "active":
        raise ValueError("Entity relationships require active entity endpoints")
    if source_entity.domain_id != target_entity.domain_id:
        raise ValueError("Entity relationships require endpoints in the same domain")
    block = session.get(IdeaBlock, idea_block_id)
    if block is None:
        raise ValueError("Relationship evidence requires an existing IdeaBlock")
    if block.domain_id != source_entity.domain_id:
        raise ValueError("Relationship evidence IdeaBlock must be in the endpoint domain")
    if block.merged_into_id is not None or block.tombstoned_at is not None or block.archived_at is not None:
        raise ValueError("Relationship evidence requires an active IdeaBlock")
    current_revision = (
        session.get(IdeaBlockRevision, block.current_revision_id) if block.current_revision_id is not None else None
    )
    if current_revision is None or current_revision.idea_block_id != block.id:
        raise ValueError("Relationship evidence requires the IdeaBlock's current revision")
    if status == "verified" and current_revision.publication_status != "verified":
        raise ValueError("Verified relationship evidence requires a verified current IdeaBlock revision")
    source_element = session.get(SourceElement, source_element_id)
    if source_element is None:
        raise ValueError("Relationship evidence requires an existing SourceElement")
    source_version = session.get(SourceDocumentVersion, source_element.source_version_id)
    if source_version is None or source_version.status != "active":
        raise ValueError("Relationship evidence requires an active source version")
    source_document = session.get(SourceDocument, source_version.source_document_id)
    if source_document is None or source_document.domain_id != block.domain_id:
        raise ValueError("Relationship evidence source must be in the IdeaBlock domain")
    if (
        source_entity.ontology_type not in definition.source_kinds
        or target_entity.ontology_type not in definition.target_kinds
    ):
        raise ValueError(
            f"Invalid endpoint kinds for relationship {relation_type}: "
            f"{source_entity.ontology_type} -> {target_entity.ontology_type}"
        )
    if definition.cardinality == "many_to_one":
        conflicting = session.scalar(
            select(EntityRelationship).where(
                EntityRelationship.source_entity_id == source_entity_id,
                EntityRelationship.target_entity_id != target_entity_id,
                EntityRelationship.relation_type == relation_type,
                EntityRelationship.status.in_(("candidate", "verified")),
                EntityRelationship.valid_to.is_(None),
            )
        )
        if conflicting is not None:
            raise ValueError(
                f"Cardinality violation for {relation_type}: {source_entity_id} already targets "
                f"{conflicting.target_entity_id}"
            )

    rel_id = relationship_id(
        {"source_entity_id": source_entity_id, "target_entity_id": target_entity_id, "relation_type": relation_type}
    )
    relationship = session.scalar(
        select(EntityRelationship).where(
            EntityRelationship.source_entity_id == source_entity_id,
            EntityRelationship.target_entity_id == target_entity_id,
            EntityRelationship.relation_type == relation_type,
        )
    )
    if relationship is None:
        relationship = EntityRelationship(
            id=rel_id,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            relation_type=relation_type,
            confidence="unknown",
            status="candidate",
        )
        session.add(relationship)
    else:
        rel_id = relationship.id
    session.flush()

    resolved_quote = _resolve_evidence_anchors(
        session, idea_block_id=idea_block_id, source_element_id=source_element_id, source_quote=source_quote
    )
    if not resolved_quote.strip():
        raise ValueError(
            "relationship evidence must carry an exact source quote; legacy rows must be backfilled or marked stale."
        )
    parent_evidence = session.scalar(
        select(ClaimEvidence)
        .where(
            ClaimEvidence.idea_block_id == idea_block_id,
            ClaimEvidence.idea_block_revision_id == current_revision.id,
            ClaimEvidence.source_element_id == source_element_id,
            ClaimEvidence.polarity == polarity,
            ClaimEvidence.status == "active",
        )
        .order_by(ClaimEvidence.created_at, ClaimEvidence.id)
    )
    if parent_evidence is None or parent_evidence.source_quote.strip() != resolved_quote.strip():
        raise ValueError("relationship evidence must match an active exact ClaimEvidence occurrence")

    provenance_id = relationship_id(
        {
            "relationship_id": rel_id,
            "idea_block_id": idea_block_id,
            "source_element_id": source_element_id,
            "polarity": polarity,
            "source_quote": resolved_quote,
        }
    ).replace("rel_", "evidence_", 1)
    provenance = session.get(RelationshipProvenance, provenance_id)
    if provenance is None:
        session.add(
            RelationshipProvenance(
                id=provenance_id,
                relationship_id=rel_id,
                idea_block_id=idea_block_id,
                source_element_id=source_element_id,
                confidence=confidence,
                extraction_method=extraction_method,
                source_quote=resolved_quote,
                raw_predicate=raw_predicate,
                polarity=polarity,
                status=status,
                valid_from=valid_from,
                valid_to=valid_to,
                qualifiers=qualifiers or {},
            )
        )
    elif provenance.valid_to is not None or provenance.status == "stale":
        relationship.metadata_ = {
            **(relationship.metadata_ or {}),
            "last_reprocessed_status": status,
            "last_reprocessed_evidence_id": provenance.id,
        }
    else:
        provenance.confidence = confidence
        provenance.polarity = polarity
        provenance.status = status
        provenance.extraction_method = extraction_method
        provenance.source_quote = resolved_quote
        provenance.raw_predicate = raw_predicate
        if valid_from is not None:
            provenance.valid_from = valid_from
        if valid_to is not None:
            provenance.valid_to = valid_to
        if qualifiers is not None:
            provenance.qualifiers = qualifiers
    session.flush()
    aggregate_relationship_evidence(session, relationship)

    sync_inverse_relationship(session, relationship)

    return relationship


def _resolve_evidence_anchors(
    session: Session, *, idea_block_id: str, source_element_id: str, source_quote: str
) -> str:
    """Resolve relationship evidence quote anchored to the parent IdeaBlock's evidence."""
    from margin_db.knowledge_models import ClaimEvidence

    resolved_quote = source_quote.strip()
    if resolved_quote:
        return resolved_quote
    parent = session.scalar(
        select(ClaimEvidence).where(
            ClaimEvidence.idea_block_id == idea_block_id, ClaimEvidence.source_element_id == source_element_id
        )
    )
    if parent is None:
        return resolved_quote
    return parent.source_quote.strip() or resolved_quote


def aggregate_relationship_evidence(session: Session, relationship: EntityRelationship) -> None:
    all_evidence = list(
        session.scalars(
            select(RelationshipProvenance).where(RelationshipProvenance.relationship_id == relationship.id)
        ).all()
    )
    if not all_evidence:
        return
    evidence = [item for item in all_evidence if item.valid_to is None and item.status != "stale"]
    supporting = [item for item in evidence if item.polarity == "supports"]
    refuting = [item for item in evidence if item.polarity == "refutes"]
    approved_supporting = [item for item in supporting if item.status == "verified"]
    approved_refuting = [item for item in refuting if item.status == "verified"]
    if approved_supporting and not approved_refuting:
        relationship.status = "verified"
    elif approved_refuting and not approved_supporting:
        relationship.status = "rejected"
    elif evidence:
        relationship.status = (
            "rejected"
            if supporting and not refuting and all(item.status == "rejected" for item in supporting)
            else "candidate"
        )
    else:
        relationship.status = "stale"
    approved = [*approved_supporting, *approved_refuting]
    confidences = {item.confidence for item in approved or evidence}
    relationship.confidence = (
        "contradicted"
        if approved_refuting
        else (
            "verified"
            if approved_supporting
            else "extracted" if "extracted" in confidences else "inferred" if "inferred" in confidences else "ambiguous"
        )
    )
    relationship.metadata_ = {
        **(relationship.metadata_ or {}),
        "evidence_count": len(evidence),
        "supporting_evidence_count": len(supporting),
        "refuting_evidence_count": len(refuting),
        "approved_evidence_count": len(approved),
        "approved_supporting_evidence_count": len(approved_supporting),
        "approved_refuting_evidence_count": len(approved_refuting),
        "evidence_idea_block_ids": sorted({item.idea_block_id for item in evidence}),
        "aggregated_confidence": relationship.confidence,
        "has_contradiction": bool(approved_supporting and approved_refuting),
        "aggregation_policy": "approved_evidence_with_polarity",
    }
    start_dates = [item.valid_from for item in all_evidence if item.valid_from is not None]
    end_dates = [item.valid_to for item in all_evidence if item.valid_to is not None]
    relationship.valid_from = min(start_dates) if start_dates else None
    relationship.valid_to = None if evidence else max(end_dates) if end_dates else relationship.valid_to
    sync_inverse_relationship(session, relationship)


def sync_inverse_relationship(session: Session, relationship: EntityRelationship) -> EntityRelationship | None:
    """Create or update the ontology-derived inverse without duplicating provenance."""
    if (relationship.metadata_ or {}).get("derived"):
        return None
    definition = RELATIONSHIP_REGISTRY.get(relationship.relation_type)
    if definition is None or definition.inverse is None:
        return None
    inverse = session.scalar(
        select(EntityRelationship).where(
            EntityRelationship.source_entity_id == relationship.target_entity_id,
            EntityRelationship.target_entity_id == relationship.source_entity_id,
            EntityRelationship.relation_type == definition.inverse,
        )
    )
    if inverse is None:
        inverse = EntityRelationship(
            id=relationship_id(
                {
                    "source_entity_id": relationship.target_entity_id,
                    "target_entity_id": relationship.source_entity_id,
                    "relation_type": definition.inverse,
                }
            ),
            source_entity_id=relationship.target_entity_id,
            target_entity_id=relationship.source_entity_id,
            relation_type=definition.inverse,
            confidence=relationship.confidence,
            status=relationship.status,
        )
        session.add(inverse)
    inverse.confidence = relationship.confidence
    inverse.status = relationship.status
    inverse.valid_from = relationship.valid_from
    inverse.valid_to = relationship.valid_to
    inverse.metadata_ = {**(inverse.metadata_ or {}), "derived": True, "inverse_of": relationship.id}
    return inverse


def _review_evidence(session: Session, relationship: EntityRelationship, *, status: str) -> None:
    """Update the relationship's provenance rows only.

            Relationship review must never mutate the parent
    ``IdeaBlockRevision.publication_status``. IdeaBlock review is an independent operation
    performed through a separate transition function.
    """
    evidence = list(
        session.scalars(
            select(RelationshipProvenance).where(RelationshipProvenance.relationship_id == relationship.id)
        ).all()
    )
    for item in evidence:
        if item.valid_to is not None or item.status == "stale":
            continue
        item.status = status
        item.reviewed_at = datetime.now(UTC)


def _refresh(session: Session, relationship: EntityRelationship) -> None:
    refresh_relationship_evidence_aggregation(
        session, source_entity_id=relationship.source_entity_id, target_entity_id=relationship.target_entity_id
    )


def _close_open_review_tasks(session: Session, *, target_id: str) -> None:
    tasks = session.scalars(
        select(ReviewTask).where(
            ReviewTask.target_type == "entity_relationship",
            ReviewTask.target_id == target_id,
            ReviewTask.status == "open",
        )
    ).all()
    for task in tasks:
        task.status = "closed"


def close_relationships_contradicted_by(
    session: Session, contradiction: EntityRelationship, *, valid_to: date | None = None, actor: str = "extraction"
) -> list[str]:
    """Close only the unambiguous active assertions refuted by an asserted edge."""

    if contradiction.status != "verified" or not is_contradiction_relationship(contradiction):
        return []
    effective_date = valid_to or datetime.now(UTC).date()
    rows = list(session.scalars(select(EntityRelationship)).all())
    closed: list[str] = []
    for relationship in _contradicted_relationships(rows, contradiction, contradiction_date=effective_date):
        if _close_relationship(
            session, relationship, contradiction=contradiction, valid_to=effective_date, actor=actor
        ):
            closed.append(relationship.id)
    refresh_relationship_evidence_aggregation(
        session, source_entity_id=contradiction.source_entity_id, target_entity_id=contradiction.target_entity_id
    )
    return closed


def close_relationship_if_contradicted_by_existing(
    session: Session, relationship: EntityRelationship, *, as_of: date | None = None, actor: str = "extraction"
) -> list[str]:
    """Apply an existing verified contradiction when it unambiguously targets a new edge."""

    if (
        relationship.status not in ACTIVE_RELATIONSHIP_STATUSES
        or relationship.valid_to is not None
        or is_contradiction_relationship(relationship)
    ):
        return []
    effective_date = as_of or datetime.now(UTC).date()
    rows = list(session.scalars(select(EntityRelationship)).all())
    contradictions = sorted(
        (item for item in rows if item.status == "verified" and is_contradiction_relationship(item)),
        key=lambda item: item.valid_from or effective_date,
    )
    for contradiction in contradictions:
        close_date = contradiction.valid_from or effective_date
        affected_ids = {
            item.id for item in _contradicted_relationships(rows, contradiction, contradiction_date=close_date)
        }
        if relationship.id not in affected_ids:
            continue
        if _close_relationship(session, relationship, contradiction=contradiction, valid_to=close_date, actor=actor):
            refresh_relationship_evidence_aggregation(
                session, source_entity_id=relationship.source_entity_id, target_entity_id=relationship.target_entity_id
            )
            return [relationship.id]
        break
    refresh_relationship_evidence_aggregation(
        session, source_entity_id=relationship.source_entity_id, target_entity_id=relationship.target_entity_id
    )
    return []


def refresh_relationship_evidence_aggregation(
    session: Session, *, source_entity_id: str, target_entity_id: str
) -> None:
    """Refresh evidence-derived state for both directions of one explicit edge pair."""

    relationships = list(
        session.scalars(
            select(EntityRelationship).where(
                or_(
                    and_(
                        EntityRelationship.source_entity_id == source_entity_id,
                        EntityRelationship.target_entity_id == target_entity_id,
                    ),
                    and_(
                        EntityRelationship.source_entity_id == target_entity_id,
                        EntityRelationship.target_entity_id == source_entity_id,
                    ),
                ),
                EntityRelationship.status != "rejected",
            )
        ).all()
    )
    contradictions = [item for item in relationships if is_contradiction_relationship(item)]
    for relationship in relationships:
        aggregate_relationship_evidence(session, relationship)
        contradiction_ids = [
            item.id
            for item in contradictions
            if item.id != relationship.id and _contradiction_applies_to_relationship(relationship, item)
        ]
        explicit_contradiction = relationship.relation_type != "contradicts" and bool(contradiction_ids)
        relationship.metadata_ = {
            **(relationship.metadata_ or {}),
            "has_contradiction": bool(
                explicit_contradiction or (relationship.metadata_ or {}).get("approved_refuting_evidence_count")
            ),
            "contradicting_relationship_ids": contradiction_ids if explicit_contradiction else [],
        }


def is_contradiction_relationship(relationship: EntityRelationship) -> bool:
    return relationship.relation_type == "contradicts"


def _contradicted_relationships(
    rows: list[EntityRelationship], contradiction: EntityRelationship, *, contradiction_date: date
) -> list[EntityRelationship]:
    endpoints = {contradiction.source_entity_id, contradiction.target_entity_id}
    affected = [
        relationship
        for relationship in rows
        if relationship.id != contradiction.id
        and relationship.valid_to is None
        and relationship.status in ACTIVE_RELATIONSHIP_STATUSES
        and not is_contradiction_relationship(relationship)
        and {relationship.source_entity_id, relationship.target_entity_id} == endpoints
        and (relationship.valid_from is None or relationship.valid_from <= contradiction_date)
    ]
    target_relation_type = str((contradiction.metadata_ or {}).get("target_relation_type") or "").strip()
    if target_relation_type:
        affected = [item for item in affected if item.relation_type == target_relation_type]
    elif len({item.relation_type for item in affected}) > 1:
        return []
    return sorted(affected, key=lambda item: item.created_at)


def _contradiction_applies_to_relationship(relationship: EntityRelationship, contradiction: EntityRelationship) -> bool:
    if contradiction.valid_from is None:
        return True
    return (relationship.valid_from or date.min) <= contradiction.valid_from <= (relationship.valid_to or date.max)


def _close_relationship(
    session: Session, relationship: EntityRelationship, *, contradiction: EntityRelationship, valid_to: date, actor: str
) -> bool:
    evidence = list(
        session.scalars(
            select(RelationshipProvenance).where(
                RelationshipProvenance.relationship_id == relationship.id, RelationshipProvenance.valid_to.is_(None)
            )
        ).all()
    )
    if not evidence:
        relationship.status = "stale"
        relationship.valid_to = valid_to
    else:
        closable = [item for item in evidence if item.valid_from is None or item.valid_from <= valid_to]
        if not closable:
            return False
        for item in closable:
            item.valid_to = valid_to
            if item.status in ACTIVE_RELATIONSHIP_STATUSES:
                item.status = "stale"
        session.flush()
        aggregate_relationship_evidence(session, relationship)
    relationship.metadata_ = {
        **(relationship.metadata_ or {}),
        "temporal_status": "closed_by_contradiction",
        "superseded_by_relationship_id": contradiction.id,
        "closed_by": actor,
        "closed_at": datetime.now(UTC).isoformat(),
    }
    return True
