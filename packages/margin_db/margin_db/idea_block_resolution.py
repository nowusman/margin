from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from margin_db.knowledge_models import IdeaBlock, IdeaBlockEntity, IdeaBlockRelation, IdeaBlockRevision
from margin_db.search_materialization import (
    SEARCHABLE_PUBLICATION_STATUSES,
    enqueue_search_materialization,
    initial_search_text,
)
from margin_db.semantic_resolution import SEMANTIC_KEY_VERSION, canonical_semantic_key
from margin_schema.extraction import ExtractedIdeaBlock
from margin_schema.ids import idea_block_id, idea_block_revision_id, relationship_id
from margin_schema.ontology import (
    ONTOLOGY_VERSION,
    RELATIONSHIP_REGISTRY,
    RelationPersistence,
    validate_relationship_endpoints,
)
from margin_schema.text_normalization import normalize_statement

IDEA_BLOCK_RELATION_TYPES = frozenset(
    name
    for name, definition in RELATIONSHIP_REGISTRY.items()
    if definition.persistence_class is RelationPersistence.IDEA_BLOCK
)
_SYMMETRIC_RELATION_TYPES = frozenset(
    name for name in IDEA_BLOCK_RELATION_TYPES if RELATIONSHIP_REGISTRY[name].symmetric
)


@dataclass(frozen=True)
class ExactResolution:
    block: IdeaBlock
    revision: IdeaBlockRevision
    semantic_key: str
    attached_existing: bool
    requires_review: bool
    review_task_created: bool = False


def idea_block_id_for_candidate(
    candidate: ExtractedIdeaBlock,
    normalized_statement: str,
    *,
    domain_id: str,
    resolved_entity_ids: tuple[str, ...] | list[str] = (),
) -> str:
    semantic_key = canonical_semantic_key(
        domain_id=domain_id,
        normalized_statement=normalized_statement,
        type=candidate.type,
        polarity=candidate.polarity,
        qualifiers=candidate.qualifiers,
        valid_from=candidate.valid_from,
        valid_to=candidate.valid_to,
        resolved_entity_ids=resolved_entity_ids,
    )
    return idea_block_id({"domain_id": domain_id, "semantic_key": semantic_key})


def create_idea_block_revision(
    session: Session,
    block: IdeaBlock,
    *,
    statement: str,
    normalized_statement: str,
    type: str,
    confidence: str,
    polarity: str,
    qualifiers: dict,
    valid_from: date | None,
    valid_to: date | None,
    publication_status: str,
    actor: str,
    process: str,
    change_reason: str | None = None,
    metadata: dict | None = None,
    ontology_version: str = ONTOLOGY_VERSION,
    resolved_entity_ids: tuple[str, ...] | list[str] = (),
    semantic_key: str | None = None,
    semantic_key_version: str = SEMANTIC_KEY_VERSION,
) -> IdeaBlockRevision:
    """Append one immutable revision and make it the block's current revision."""
    canonical_entity_ids = tuple(resolved_entity_ids)
    if not canonical_entity_ids:
        canonical_entity_ids = tuple(
            session.scalars(
                select(IdeaBlockEntity.entity_id)
                .where(IdeaBlockEntity.idea_block_id == block.id)
                .order_by(IdeaBlockEntity.entity_id)
            ).all()
        )
    latest_number = session.scalar(
        select(IdeaBlockRevision.revision_number)
        .where(IdeaBlockRevision.idea_block_id == block.id)
        .order_by(IdeaBlockRevision.revision_number.desc())
        .limit(1)
    )
    revision_number = (latest_number or 0) + 1
    revision = IdeaBlockRevision(
        id=idea_block_revision_id({"idea_block_id": block.id, "revision_number": revision_number}),
        idea_block_id=block.id,
        revision_number=revision_number,
        statement=statement,
        normalized_statement=normalized_statement,
        search_text=(
            initial_search_text(
                statement=statement,
                qualifiers=qualifiers,
                idea_block_type=type,
                polarity=polarity,
                confidence=confidence,
                controlled_terms=tuple(
                    str(tag) for tag in (metadata or {}).get("tags", []) if isinstance(tag, str) and tag.strip()
                ),
            )
            if publication_status in SEARCHABLE_PUBLICATION_STATUSES
            else ""
        ),
        type=type,
        confidence=confidence,
        polarity=polarity,
        qualifiers=qualifiers,
        valid_from=valid_from,
        valid_to=valid_to,
        publication_status=publication_status,
        semantic_key=semantic_key
        or canonical_semantic_key(
            domain_id=block.domain_id,
            normalized_statement=normalized_statement,
            type=type,
            polarity=polarity,
            qualifiers=qualifiers,
            valid_from=valid_from,
            valid_to=valid_to,
            resolved_entity_ids=canonical_entity_ids,
        ),
        semantic_key_version=semantic_key_version,
        ontology_version=ontology_version,
        actor=actor,
        process=process,
        change_reason=change_reason,
        metadata_=metadata or {},
    )
    session.add(revision)
    session.flush()
    block.current_revision_id = revision.id
    block.updated_at = revision.created_at
    session.flush()
    if publication_status in SEARCHABLE_PUBLICATION_STATUSES:
        enqueue_search_materialization(session, (revision.id,), reason="current_revision_changed")
    return revision


def resolve_exact_idea_block_candidate(
    session: Session,
    candidate: ExtractedIdeaBlock,
    *,
    domain_id: str,
    resolved_entity_ids: tuple[str, ...] | list[str] = (),
    entities_resolved: bool | None = None,
    status: str = "verified",
    actor: str = "extraction",
    process: str | None = None,
    extraction_run_id: str | None = None,
    metadata: dict | None = None,
) -> ExactResolution:
    """Attach exact evidence or create one canonical block in the caller transaction."""

    from margin_db.evidence_mapping import upsert_claim_evidence

    entity_ids = tuple(sorted({str(item).strip() for item in resolved_entity_ids if str(item).strip()}))
    if entities_resolved is None:
        entities_resolved = not candidate.entities or len(entity_ids) == len(candidate.entities)
    normalized_statement = normalize_statement(candidate.statement)
    semantic_key = canonical_semantic_key(
        domain_id=domain_id,
        normalized_statement=normalized_statement,
        type=candidate.type,
        polarity=candidate.polarity,
        qualifiers=candidate.qualifiers,
        valid_from=candidate.valid_from,
        valid_to=candidate.valid_to,
        resolved_entity_ids=entity_ids,
    )

    if entities_resolved:
        existing = _block_for_key(
            session, semantic_key=semantic_key, semantic_key_version=SEMANTIC_KEY_VERSION, lock=True
        )
        if existing is not None:
            revision = current_idea_block_revision(session, existing)
            for evidence in candidate.evidence:
                upsert_claim_evidence(
                    session,
                    idea_block_id=existing.id,
                    idea_block_revision_id=revision.id,
                    candidate=candidate,
                    evidence=evidence,
                    extraction_run_id=extraction_run_id,
                )
            return ExactResolution(existing, revision, semantic_key, True, False)

    block_id = (
        idea_block_id_for_candidate(
            candidate, normalized_statement, domain_id=domain_id, resolved_entity_ids=entity_ids
        )
        if entities_resolved
        else idea_block_id(
            {
                "domain_id": domain_id,
                "semantic_key": semantic_key,
                "primary_evidence": candidate.evidence[0].model_dump(mode="json"),
            }
        )
    )
    existing_by_id = session.get(IdeaBlock, block_id)
    if existing_by_id is not None:
        if existing_by_id.merged_into_id or existing_by_id.archived_at or existing_by_id.tombstoned_at:
            raise ValueError("exact semantic key belongs to an archived or tombstoned IdeaBlock")
        revision = current_idea_block_revision(session, existing_by_id)
        if revision.publication_status in {"candidate", "verified"}:
            for evidence in candidate.evidence:
                upsert_claim_evidence(
                    session,
                    idea_block_id=existing_by_id.id,
                    idea_block_revision_id=revision.id,
                    candidate=candidate,
                    evidence=evidence,
                    extraction_run_id=extraction_run_id,
                )
            requires_review = not entities_resolved
            return ExactResolution(existing_by_id, revision, semantic_key, True, requires_review, False)
        revision = create_idea_block_revision(
            session,
            existing_by_id,
            statement=candidate.statement,
            normalized_statement=normalized_statement,
            type=candidate.type,
            confidence=candidate.confidence,
            polarity=candidate.polarity,
            qualifiers=dict(candidate.qualifiers),
            valid_from=candidate.valid_from,
            valid_to=candidate.valid_to,
            publication_status=status if entities_resolved else "candidate",
            actor=actor,
            process=process or candidate.extraction_method,
            change_reason="reactivated by current source evidence",
            metadata=metadata or dict(candidate.metadata),
            resolved_entity_ids=entity_ids,
            semantic_key=semantic_key,
        )
        for evidence in candidate.evidence:
            upsert_claim_evidence(
                session,
                idea_block_id=existing_by_id.id,
                idea_block_revision_id=revision.id,
                candidate=candidate,
                evidence=evidence,
                extraction_run_id=extraction_run_id,
            )
        requires_review = not entities_resolved
        return ExactResolution(existing_by_id, revision, semantic_key, False, requires_review, False)

    block = IdeaBlock(id=block_id, domain_id=domain_id)
    try:
        with session.begin_nested():
            session.add(block)
            session.flush()
            revision = create_idea_block_revision(
                session,
                block,
                statement=candidate.statement,
                normalized_statement=normalized_statement,
                type=candidate.type,
                confidence=candidate.confidence,
                polarity=candidate.polarity,
                qualifiers=dict(candidate.qualifiers),
                valid_from=candidate.valid_from,
                valid_to=candidate.valid_to,
                publication_status=status if entities_resolved else "candidate",
                actor=actor,
                process=process or candidate.extraction_method,
                change_reason="initial exact semantic resolution",
                metadata=metadata or dict(candidate.metadata),
                resolved_entity_ids=entity_ids,
                semantic_key=semantic_key,
            )
            for evidence in candidate.evidence:
                upsert_claim_evidence(
                    session,
                    idea_block_id=block.id,
                    idea_block_revision_id=revision.id,
                    candidate=candidate,
                    evidence=evidence,
                    extraction_run_id=extraction_run_id,
                )
    except IntegrityError:
        if not entities_resolved:
            raise
        existing = _block_for_key(
            session, semantic_key=semantic_key, semantic_key_version=SEMANTIC_KEY_VERSION, lock=True
        )
        if existing is None:
            raise
        revision = current_idea_block_revision(session, existing)
        for evidence in candidate.evidence:
            upsert_claim_evidence(
                session,
                idea_block_id=existing.id,
                idea_block_revision_id=revision.id,
                candidate=candidate,
                evidence=evidence,
                extraction_run_id=extraction_run_id,
            )
        return ExactResolution(existing, revision, semantic_key, True, False)

    requires_review = not entities_resolved
    session.flush()
    return ExactResolution(block, revision, semantic_key, False, requires_review, False)


def _block_for_key(session: Session, *, semantic_key: str, semantic_key_version: str, lock: bool) -> IdeaBlock | None:
    statement = (
        select(IdeaBlock)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(
            IdeaBlockRevision.semantic_key == semantic_key,
            IdeaBlockRevision.semantic_key_version == semantic_key_version,
            IdeaBlockRevision.publication_status.in_(("candidate", "verified")),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.archived_at.is_(None),
        )
        .order_by(IdeaBlock.id)
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def current_idea_block_revision(session: Session, block: IdeaBlock) -> IdeaBlockRevision:
    revision = session.get(IdeaBlockRevision, block.current_revision_id) if block.current_revision_id else None
    if revision is None or revision.idea_block_id != block.id:
        raise ValueError(f"IdeaBlock {block.id} has no current revision")
    return revision


def upsert_idea_block_relation(
    session: Session,
    *,
    source_idea_block_id: str,
    target_idea_block_id: str,
    relation_type: str,
    status: str = "candidate",
    confidence: Decimal | float = Decimal("0"),
    valid_from: date | None = None,
    valid_to: date | None = None,
    decision_provenance: dict | None = None,
) -> IdeaBlockRelation:
    if relation_type not in IDEA_BLOCK_RELATION_TYPES:
        raise ValueError(f"unsupported IdeaBlock relation type: {relation_type}")
    validate_relationship_endpoints(relation_type, "idea_block", "idea_block")
    if source_idea_block_id == target_idea_block_id:
        raise ValueError("IdeaBlock relations require distinct endpoints")
    source = session.get(IdeaBlock, source_idea_block_id)
    target = session.get(IdeaBlock, target_idea_block_id)
    if source is None or target is None:
        raise ValueError("IdeaBlock relation endpoints must exist")
    if source.domain_id != target.domain_id:
        raise ValueError("IdeaBlock relations cannot cross domains")
    if status not in {"candidate", "verified", "rejected", "stale"}:
        raise ValueError(f"invalid IdeaBlock relation status: {status}")
    try:
        normalized_confidence = Decimal(str(confidence))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("IdeaBlock relation confidence must be numeric") from exc
    if not normalized_confidence.is_finite() or not 0 <= normalized_confidence <= 1:
        raise ValueError("IdeaBlock relation confidence must be between 0 and 1")
    if valid_from is not None and (not isinstance(valid_from, date) or isinstance(valid_from, datetime)):
        raise ValueError("IdeaBlock relation valid_from must be a date")
    if valid_to is not None and (not isinstance(valid_to, date) or isinstance(valid_to, datetime)):
        raise ValueError("IdeaBlock relation valid_to must be a date")
    if valid_from is not None and valid_to is not None and valid_to < valid_from:
        raise ValueError("IdeaBlock relation valid_to cannot precede valid_from")
    if decision_provenance is not None and not isinstance(decision_provenance, dict):
        raise ValueError("IdeaBlock relation decision provenance must be an object")
    if status == "verified" and not decision_provenance:
        raise ValueError("verified IdeaBlock relations require decision provenance")
    direction = "symmetric" if relation_type in _SYMMETRIC_RELATION_TYPES else "directed"
    if direction == "symmetric" and target_idea_block_id < source_idea_block_id:
        source_idea_block_id, target_idea_block_id = target_idea_block_id, source_idea_block_id
    row = session.scalar(
        select(IdeaBlockRelation).where(
            IdeaBlockRelation.source_idea_block_id == source_idea_block_id,
            IdeaBlockRelation.target_idea_block_id == target_idea_block_id,
            IdeaBlockRelation.relation_type == relation_type,
        )
    )
    if row is None:
        row = IdeaBlockRelation(
            id=relationship_id(
                {
                    "source_idea_block_id": source_idea_block_id,
                    "target_idea_block_id": target_idea_block_id,
                    "relation_type": relation_type,
                }
            ),
            source_idea_block_id=source_idea_block_id,
            target_idea_block_id=target_idea_block_id,
            relation_type=relation_type,
        )
        session.add(row)
    row.direction = direction
    row.status = status
    row.confidence = normalized_confidence
    row.valid_from = valid_from
    row.valid_to = valid_to
    row.decision_provenance = decision_provenance or {}
    session.flush()
    return row


__all__ = [
    "IDEA_BLOCK_RELATION_TYPES",
    "ExactResolution",
    "create_idea_block_revision",
    "current_idea_block_revision",
    "idea_block_id_for_candidate",
    "resolve_exact_idea_block_candidate",
    "upsert_idea_block_relation",
]
