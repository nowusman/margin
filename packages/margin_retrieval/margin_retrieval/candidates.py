"""Database-backed candidate retrieval for current Idea Block revisions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import Select, exists, false, func, or_, select
from sqlalchemy.orm import Session, aliased

from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord, GovernanceRecordRevision
from margin_db.knowledge_models import (
    ClaimEvidence,
    EmbeddingRecord,
    IdeaBlock,
    IdeaBlockEntity,
    IdeaBlockRevision,
    SearchEmbeddingAssignment,
)
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_embedding.interfaces import EMBEDDING_DIMENSIONS, EmbeddingProvider, validate_embedding_batch
from margin_retrieval.interfaces import RetrievalScope
from margin_retrieval.policy import RECIPROCAL_RANK_FUSION_K, RETRIEVAL_CANDIDATE_LIMIT
from margin_retrieval.support import exclusive_date_to, is_postgresql


@dataclass(frozen=True)
class RetrievalCandidate:
    revision_id: str
    channel: str
    rank: int
    score: float


@dataclass(frozen=True)
class FusedCandidate:
    revision_id: str
    score: float
    channel_ranks: dict[str, int]


@dataclass(frozen=True)
class DenseCandidateResult:
    candidates: tuple[RetrievalCandidate, ...]
    mode: str
    detail: str | None = None


@dataclass(frozen=True)
class SourceDiscoveryCandidate:
    source_element_id: str
    rank: int
    score: float


def lexical_candidates(
    session: Session, *, query_text: str, scope: RetrievalScope, limit: int = RETRIEVAL_CANDIDATE_LIMIT
) -> tuple[RetrievalCandidate, ...]:
    """Rank current revisions with the persisted PostgreSQL search vector."""

    if not is_postgresql(session) or not query_text.strip():
        return ()
    ts_query = func.websearch_to_tsquery("simple", query_text)
    rank = func.ts_rank_cd(IdeaBlockRevision.search_vector, ts_query)
    rows = session.execute(
        _authorized_current_revision_query(scope)
        .add_columns(rank.label("rank_score"))
        .where(IdeaBlockRevision.search_vector.op("@@")(ts_query))
        .order_by(rank.desc(), IdeaBlockRevision.id.asc())
        .limit(limit)
    ).all()
    return tuple(
        RetrievalCandidate(revision_id=revision_id, channel="lexical", rank=index, score=float(score or 0))
        for index, (revision_id, score) in enumerate(rows, start=1)
    )


def dense_candidates(
    session: Session,
    *,
    query_text: str,
    scope: RetrievalScope,
    embedding_provider: EmbeddingProvider | None,
    deadline_ms: int,
    limit: int = RETRIEVAL_CANDIDATE_LIMIT,
) -> DenseCandidateResult:
    """Return current-revision vector candidates only when dense retrieval is healthy."""

    if embedding_provider is None:
        return DenseCandidateResult((), "not_configured")
    if not is_postgresql(session):
        return DenseCandidateResult((), "postgres_unavailable")
    if not query_text.strip():
        return DenseCandidateResult((), "empty_query")
    active_assignment = session.scalar(
        select(SearchEmbeddingAssignment.id).where(
            SearchEmbeddingAssignment.status == "active",
            SearchEmbeddingAssignment.model_id == embedding_provider.model_id,
            SearchEmbeddingAssignment.index_version == embedding_provider.index_version,
            SearchEmbeddingAssignment.dimensions == EMBEDDING_DIMENSIONS,
        )
    )
    if active_assignment is None:
        return DenseCandidateResult((), "assignment_unavailable")
    try:
        started = _monotonic_ms()
        vectors = embedding_provider.embed((query_text,))
        elapsed_ms = _monotonic_ms() - started
        if elapsed_ms > deadline_ms:
            return DenseCandidateResult((), "deadline_exceeded", f"embedding exceeded {deadline_ms}ms")
        validate_embedding_batch(vectors, expected_count=1, declared_dimensions=embedding_provider.dimensions)
    except (RuntimeError, ValueError, IndexError, TimeoutError, OSError) as exc:
        return DenseCandidateResult((), "unhealthy", str(exc))

    distance = EmbeddingRecord.embedding.cosine_distance(vectors[0])
    rows = session.execute(
        _authorized_current_revision_query(scope)
        .join(
            EmbeddingRecord,
            (EmbeddingRecord.target_type == "idea_block_revision")
            & (EmbeddingRecord.target_id == IdeaBlockRevision.id)
            & (EmbeddingRecord.idea_block_revision_id == IdeaBlockRevision.id),
        )
        .join(
            SearchEmbeddingAssignment,
            (SearchEmbeddingAssignment.model_id == EmbeddingRecord.model_id)
            & (SearchEmbeddingAssignment.index_version == EmbeddingRecord.index_version)
            & (SearchEmbeddingAssignment.dimensions == EmbeddingRecord.dimensions),
        )
        .where(SearchEmbeddingAssignment.status == "active")
        .where(EmbeddingRecord.model_id == embedding_provider.model_id)
        .where(EmbeddingRecord.index_version == embedding_provider.index_version)
        .where(EmbeddingRecord.dimensions == EMBEDDING_DIMENSIONS)
        .add_columns(distance.label("vector_distance"))
        .order_by(distance.asc(), IdeaBlockRevision.id.asc())
        .limit(limit)
    ).all()
    return DenseCandidateResult(
        tuple(
            RetrievalCandidate(
                revision_id=revision_id, channel="dense", rank=index, score=max(0.0, 1.0 - float(distance_value or 1.0))
            )
            for index, (revision_id, distance_value) in enumerate(rows, start=1)
        ),
        "healthy",
    )


def fuse_candidates(
    channels: Iterable[Iterable[RetrievalCandidate]], *, limit: int = RETRIEVAL_CANDIDATE_LIMIT
) -> tuple[FusedCandidate, ...]:
    """Fuse bounded candidate lists with Reciprocal Rank Fusion."""

    scores: dict[str, float] = {}
    channel_ranks: dict[str, dict[str, int]] = {}
    for candidates in channels:
        for candidate in candidates:
            scores[candidate.revision_id] = scores.get(candidate.revision_id, 0.0) + 1 / (
                RECIPROCAL_RANK_FUSION_K + candidate.rank
            )
            channel_ranks.setdefault(candidate.revision_id, {})[candidate.channel] = candidate.rank
    ordered = sorted(scores, key=lambda revision_id: (-scores[revision_id], revision_id))[:limit]
    return tuple(
        FusedCandidate(revision_id=revision_id, score=scores[revision_id], channel_ranks=channel_ranks[revision_id])
        for revision_id in ordered
    )


def source_discovery_candidates(
    session: Session, *, query_text: str, scope: RetrievalScope, limit: int = RETRIEVAL_CANDIDATE_LIMIT
) -> tuple[SourceDiscoveryCandidate, ...]:
    """Search source elements only for explicit extraction-gap discovery."""

    if not is_postgresql(session) or not query_text.strip():
        return ()
    if not scope.all_category_ids_authorized and not scope.category_ids:
        return ()
    ts_query = func.websearch_to_tsquery("simple", query_text)
    source_vector = func.to_tsvector("simple", func.coalesce(SourceElement.text, ""))
    rank = func.ts_rank_cd(source_vector, ts_query)
    category_entity = aliased(Entity)
    query = (
        select(SourceElement.id, rank.label("rank_score"))
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .join(
            category_entity,
            (category_entity.id == SourceDocument.category_id) & (category_entity.ontology_type == "category"),
        )
        .where(SourceDocumentVersion.status == "active")
        .where(category_entity.lifecycle_status == "active")
        .where(source_vector.op("@@")(ts_query))
        .order_by(rank.desc(), SourceElement.id.asc())
        .limit(limit)
    )
    if scope.domain_ids:
        query = query.where(SourceDocument.domain_id.in_(scope.domain_ids))
    if not scope.all_category_ids_authorized:
        query = query.where(SourceDocument.category_id.in_(scope.category_ids))
    if scope.sensitivities:
        query = query.where(SourceDocument.sensitivity.in_(scope.sensitivities))
    if scope.source_document_ids:
        query = query.where(SourceDocument.id.in_(scope.source_document_ids))
    rows = session.execute(query).all()
    return tuple(
        SourceDiscoveryCandidate(source_element_id=element_id, rank=index, score=float(score or 0))
        for index, (element_id, score) in enumerate(rows, start=1)
    )


def authorized_current_revisions_for_block_ids(
    session: Session, *, block_ids: Iterable[str], scope: RetrievalScope
) -> dict[str, str]:
    """Return readable current revisions for post-retrieval expansion only."""

    ids = tuple(dict.fromkeys(block_ids))
    if not ids:
        return {}
    rows = session.execute(
        _authorized_current_revision_query(scope).add_columns(IdeaBlock.id).where(IdeaBlock.id.in_(ids))
    ).all()
    return {block_id: revision_id for revision_id, block_id in rows}


def readable_idea_block_ids(session: Session, scope: RetrievalScope) -> frozenset[str]:
    """Return the Idea Blocks the authorised scope may read.

    A summary that counts records the reader may not read would leak the shape
    of the corpus, so the count follows the same evidence chain as the search.
    """

    rows = session.execute(
        select(IdeaBlock.id)
        .join(IdeaBlockRevision, IdeaBlock.current_revision_id == IdeaBlockRevision.id)
        .where(
            IdeaBlockRevision.idea_block_id == IdeaBlock.id,
            IdeaBlockRevision.publication_status.in_(scope.statuses),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
            or_(_readable_source_evidence(scope), _readable_governance_evidence(scope)),
        )
        .distinct()
    ).all()
    return frozenset(block_id for (block_id,) in rows)


def _authorized_current_revision_query(scope: RetrievalScope) -> Select[tuple[str]]:
    """Select unique readable current revision IDs without result-hydration joins."""

    source_evidence = _readable_source_evidence(scope)
    governance_evidence = _readable_governance_evidence(scope)
    query = (
        select(IdeaBlockRevision.id)
        .join(IdeaBlock, IdeaBlock.current_revision_id == IdeaBlockRevision.id)
        .where(
            IdeaBlockRevision.idea_block_id == IdeaBlock.id, IdeaBlockRevision.publication_status.in_(scope.statuses)
        )
        .where(IdeaBlock.merged_into_id.is_(None))
        .where(IdeaBlock.tombstoned_at.is_(None))
        .where(IdeaBlock.archived_at.is_(None))
        .where(or_(source_evidence, governance_evidence))
    )
    if scope.domain_ids:
        query = query.where(IdeaBlock.domain_id.in_(scope.domain_ids))
    if scope.idea_block_types:
        query = query.where(IdeaBlockRevision.type.in_(scope.idea_block_types))
    if scope.entity_ids or scope.entity_types:
        query = query.where(_idea_block_entity_filter(scope))
    if scope.relationship_types:
        query = query.where(_relationship_filter(scope))
    if scope.date_from:
        query = query.where(or_(IdeaBlockRevision.valid_to.is_(None), IdeaBlockRevision.valid_to >= scope.date_from))
    if scope.date_to:
        query = query.where(
            or_(IdeaBlockRevision.valid_from.is_(None), IdeaBlockRevision.valid_from < exclusive_date_to(scope.date_to))
        )
    return query


def _readable_source_evidence(scope: RetrievalScope):
    category_entity = aliased(Entity)
    query = (
        select(ClaimEvidence.id)
        .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .join(
            category_entity,
            (category_entity.id == SourceDocument.category_id) & (category_entity.ontology_type == "category"),
        )
        .where(
            ClaimEvidence.idea_block_id == IdeaBlock.id, ClaimEvidence.idea_block_revision_id == IdeaBlockRevision.id
        )
        .where(ClaimEvidence.status == "active")
        .where(SourceDocumentVersion.status == "active")
        .where(category_entity.lifecycle_status == "active")
        .where(SourceDocument.domain_id == IdeaBlock.domain_id)
    )
    if scope.domain_ids:
        query = query.where(SourceDocument.domain_id.in_(scope.domain_ids))
    if scope.all_category_ids_authorized:
        pass
    elif scope.category_ids:
        query = query.where(SourceDocument.category_id.in_(scope.category_ids))
    else:
        return false()
    if scope.sensitivities:
        query = query.where(SourceDocument.sensitivity.in_(scope.sensitivities))
    if scope.source_document_ids:
        query = query.where(SourceDocument.id.in_(scope.source_document_ids))
    if scope.date_from:
        query = query.where(or_(ClaimEvidence.valid_to.is_(None), ClaimEvidence.valid_to >= scope.date_from))
    if scope.date_to:
        query = query.where(
            or_(ClaimEvidence.valid_from.is_(None), ClaimEvidence.valid_from < exclusive_date_to(scope.date_to))
        )
    return exists(query)


def _readable_governance_evidence(scope: RetrievalScope):
    if not scope.governance_record_types:
        return false()
    query = (
        select(ClaimEvidence.id)
        .join(GovernanceRecordRevision, GovernanceRecordRevision.id == ClaimEvidence.governance_record_revision_id)
        .join(GovernanceRecord, GovernanceRecord.id == GovernanceRecordRevision.governance_record_id)
        .where(
            ClaimEvidence.idea_block_id == IdeaBlock.id, ClaimEvidence.idea_block_revision_id == IdeaBlockRevision.id
        )
        .where(ClaimEvidence.status == "active")
        .where(GovernanceRecord.current_revision_id == GovernanceRecordRevision.id)
        .where(GovernanceRecordRevision.governance_record_id == GovernanceRecord.id)
        .where(GovernanceRecord.record_type.in_(scope.governance_record_types))
        .where(GovernanceRecord.domain_id == IdeaBlock.domain_id)
    )
    if scope.domain_ids:
        query = query.where(GovernanceRecord.domain_id.in_(scope.domain_ids))
    if scope.governance_statuses:
        query = query.where(GovernanceRecord.lifecycle_status.in_(scope.governance_statuses))
    if scope.architecture_backlog_states:
        query = query.where(GovernanceRecord.record_type == "architecture_backlog").where(
            GovernanceRecord.lifecycle_status.in_(scope.architecture_backlog_states)
        )
    if scope.date_from:
        query = query.where(or_(ClaimEvidence.valid_to.is_(None), ClaimEvidence.valid_to >= scope.date_from))
    if scope.date_to:
        query = query.where(
            or_(ClaimEvidence.valid_from.is_(None), ClaimEvidence.valid_from < exclusive_date_to(scope.date_to))
        )
    return exists(query)


def _idea_block_entity_filter(scope: RetrievalScope):
    query = (
        select(IdeaBlockEntity.idea_block_id)
        .join(Entity, Entity.id == IdeaBlockEntity.entity_id)
        .where(IdeaBlockEntity.idea_block_id == IdeaBlock.id)
        .where(Entity.lifecycle_status == "active")
        .where(or_(Entity.domain_id.is_(None), Entity.domain_id == IdeaBlock.domain_id))
    )
    if scope.entity_ids:
        query = query.where(IdeaBlockEntity.entity_id.in_(scope.entity_ids))
    if scope.entity_types:
        query = query.where(Entity.ontology_type.in_(scope.entity_types))
    return exists(query)


def _relationship_filter(scope: RetrievalScope):
    source_entity = aliased(Entity)
    target_entity = aliased(Entity)
    query = (
        select(RelationshipProvenance.id)
        .join(EntityRelationship, EntityRelationship.id == RelationshipProvenance.relationship_id)
        .join(source_entity, source_entity.id == EntityRelationship.source_entity_id)
        .join(target_entity, target_entity.id == EntityRelationship.target_entity_id)
        .join(
            ClaimEvidence,
            (ClaimEvidence.idea_block_id == IdeaBlock.id)
            & (ClaimEvidence.idea_block_revision_id == IdeaBlock.current_revision_id)
            & (ClaimEvidence.source_element_id == RelationshipProvenance.source_element_id)
            & (ClaimEvidence.source_quote == RelationshipProvenance.source_quote)
            & (ClaimEvidence.polarity == RelationshipProvenance.polarity)
            & (ClaimEvidence.status == "active")
            & ClaimEvidence.governance_record_revision_id.is_(None),
        )
        .join(SourceElement, SourceElement.id == RelationshipProvenance.source_element_id)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .join(Entity, (Entity.id == SourceDocument.category_id) & (Entity.ontology_type == "category"))
        .where(RelationshipProvenance.idea_block_id == IdeaBlock.id)
        .where(RelationshipProvenance.status == "verified")
        .where(EntityRelationship.status == "verified")
        .where(SourceDocumentVersion.status == "active")
        .where(Entity.lifecycle_status == "active")
        .where(SourceDocument.domain_id == IdeaBlock.domain_id)
        .where(EntityRelationship.relation_type.in_(scope.relationship_types))
        .where(source_entity.lifecycle_status == "active")
        .where(target_entity.lifecycle_status == "active")
        .where(source_entity.domain_id == target_entity.domain_id)
        .where(source_entity.domain_id == IdeaBlock.domain_id)
        .where(target_entity.domain_id == IdeaBlock.domain_id)
    )
    if not scope.all_category_ids_authorized and not scope.category_ids:
        return false()
    if scope.domain_ids:
        query = query.where(
            or_(source_entity.domain_id.is_(None), source_entity.domain_id.in_(scope.domain_ids)),
            or_(target_entity.domain_id.is_(None), target_entity.domain_id.in_(scope.domain_ids)),
            SourceDocument.domain_id.in_(scope.domain_ids),
        )
    if not scope.all_category_ids_authorized:
        query = query.where(SourceDocument.category_id.in_(scope.category_ids))
    if scope.sensitivities:
        query = query.where(SourceDocument.sensitivity.in_(scope.sensitivities))
    if scope.source_document_ids:
        query = query.where(SourceDocument.id.in_(scope.source_document_ids))
    if scope.date_from:
        query = query.where(
            or_(RelationshipProvenance.valid_to.is_(None), RelationshipProvenance.valid_to >= scope.date_from),
            or_(EntityRelationship.valid_to.is_(None), EntityRelationship.valid_to >= scope.date_from),
            or_(ClaimEvidence.valid_to.is_(None), ClaimEvidence.valid_to >= scope.date_from),
        )
    if scope.date_to:
        query = query.where(
            or_(
                RelationshipProvenance.valid_from.is_(None),
                RelationshipProvenance.valid_from < exclusive_date_to(scope.date_to),
            ),
            or_(
                EntityRelationship.valid_from.is_(None),
                EntityRelationship.valid_from < exclusive_date_to(scope.date_to),
            ),
            or_(ClaimEvidence.valid_from.is_(None), ClaimEvidence.valid_from < exclusive_date_to(scope.date_to)),
        )
    return exists(query)


def _monotonic_ms() -> int:
    from time import perf_counter

    return int(perf_counter() * 1000)
