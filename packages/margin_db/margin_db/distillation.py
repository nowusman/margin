from __future__ import annotations

from collections.abc import Collection, Iterator
from dataclasses import dataclass
from time import monotonic

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from margin_db.embeddings import required_embedding_index_config
from margin_db.knowledge_models import EmbeddingRecord, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.review_models import ReviewTask
from margin_db.semantic_resolution import (
    SEMANTIC_KEY_VERSION,
    SEMANTIC_RESOLUTION_TASK_TYPE,
    SemanticClassifier,
    SemanticResolutionResult,
    classify_semantic_pair,
    compare_protected_fields,
    configured_semantic_classifier,
)
from margin_schema.text_normalization import normalize_entity_name, tokenize_keywords

_STOPWORDS = frozenset({"the", "a", "an", "and", "or", "to", "for", "of", "in", "on", "is", "are"})


def weighted_jaccard(left: _RevisionView, right: _RevisionView) -> float:
    """Return the explainable lexical signal used for semantic review."""

    keyword_score = _jaccard(_keywords(left.statement), _keywords(right.statement))
    entity_score = _jaccard(_metadata_set(left, "entities"), _metadata_set(right, "entities"))
    tag_score = _jaccard(_metadata_set(left, "tags"), _metadata_set(right, "tags"))
    return round((keyword_score * 0.5) + (entity_score * 0.3) + (tag_score * 0.2), 4)


def _keywords(value: str) -> set[str]:
    return set(tokenize_keywords(value, stopwords=_STOPWORDS))


def _metadata_set(block: _RevisionView, key: str) -> set[str]:
    value = block.metadata_.get(key, [])
    if not isinstance(value, list):
        return set()
    return {normalized for item in value if (normalized := normalize_entity_name(str(item)))}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


@dataclass
class _RevisionView:
    id: str
    statement: str
    confidence: str
    metadata_: dict[str, object]
    type: str
    polarity: str
    qualifiers: dict[str, object]
    valid_from: object
    valid_to: object


@dataclass(frozen=True)
class RetrievedSemanticCandidate:
    block: IdeaBlock
    revision: IdeaBlockRevision
    score: float


@dataclass(frozen=True)
class SemanticCandidate:
    left_id: str
    right_id: str
    score: float
    lexical_score: float
    action: str
    reason: str
    protected_field_differences: dict[str, object]
    classification: SemanticResolutionResult
    idea_block_revision_ids: dict[str, str]
    embedding_model_id: str
    embedding_index_version: str


def create_semantic_review_tasks(
    session: Session,
    *,
    domain_id: str | None = None,
    idea_block_revision_ids: Collection[str] | None = None,
    authorized_domain_ids: Collection[str] | None = None,
    candidate_limit: int = 20,
    max_candidates: int = 100,
    deadline_ms: int = 500,
    threshold: float = 0.5,
    classifier: SemanticClassifier | None = None,
) -> list[SemanticCandidate]:
    """Discover bounded semantic pairs and persist review-only suggestions."""

    candidate_limit = _bounded_limit(candidate_limit, maximum=100)
    max_candidates = _bounded_limit(max_candidates, maximum=1000)
    deadline_ms = max(1, min(deadline_ms, 10_000))
    allowed_domains = set(authorized_domain_ids) if authorized_domain_ids is not None else None
    if domain_id is not None and allowed_domains is not None and domain_id not in allowed_domains:
        return []
    classifier = classifier if classifier is not None else configured_semantic_classifier(session)
    started = monotonic()
    candidates: list[SemanticCandidate] = []
    seen_pairs: set[tuple[str, str]] = set()
    rows = _current_revision_rows(
        session,
        domain_id=domain_id,
        idea_block_revision_ids=idea_block_revision_ids,
        authorized_domain_ids=allowed_domains,
    )
    for block, revision in rows:
        remaining_ms = _remaining_deadline_ms(started, deadline_ms)
        if remaining_ms <= 0 or len(candidates) >= max_candidates:
            break
        vector = _revision_embedding(session, revision.id)
        if vector is None:
            continue
        for retrieved in retrieve_semantic_candidates(
            session,
            revision_id=revision.id,
            query_embedding=vector,
            domain_id=block.domain_id,
            authorized_domain_ids=allowed_domains,
            limit=candidate_limit,
            deadline_ms=remaining_ms,
        ):
            pair = tuple(sorted((block.id, retrieved.block.id)))
            if pair in seen_pairs or len(candidates) >= max_candidates:
                continue
            seen_pairs.add(pair)
            candidate = _semantic_candidate(
                session, block, revision, retrieved.block, retrieved.revision, retrieved.score, classifier
            )
            if candidate.score >= threshold:
                candidates.append(candidate)

    for candidate in candidates:
        _persist_semantic_review_task(session, candidate)
    session.flush()
    return candidates


def _current_revision_rows(
    session: Session,
    *,
    domain_id: str | None,
    idea_block_revision_ids: Collection[str] | None,
    authorized_domain_ids: set[str] | None,
) -> Iterator[tuple[IdeaBlock, IdeaBlockRevision]]:
    statement = (
        select(IdeaBlock, IdeaBlockRevision)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(
            IdeaBlockRevision.publication_status.in_(("candidate", "verified")),
            IdeaBlock.archived_at.is_(None),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
        )
        .order_by(IdeaBlock.domain_id, IdeaBlock.id)
    )
    if domain_id is not None:
        statement = statement.where(IdeaBlock.domain_id == domain_id)
    if idea_block_revision_ids is not None:
        if not idea_block_revision_ids:
            return
        statement = statement.where(IdeaBlockRevision.id.in_(idea_block_revision_ids))
    if authorized_domain_ids is not None:
        if not authorized_domain_ids:
            return
        statement = statement.where(IdeaBlock.domain_id.in_(authorized_domain_ids))
    yield from session.execute(statement).tuples().yield_per(100)


def _revision_embedding(session: Session, revision_id: str) -> list[float] | None:
    config = required_embedding_index_config()
    embedding = session.scalar(
        select(EmbeddingRecord.embedding).where(
            EmbeddingRecord.target_type == "idea_block_revision",
            EmbeddingRecord.target_id == revision_id,
            EmbeddingRecord.model_id == config.model_id,
            EmbeddingRecord.index_version == config.index_version,
            EmbeddingRecord.dimensions == config.dimensions,
        )
    )
    if embedding is None:
        return None
    return [float(value) for value in embedding]


def _semantic_candidate(
    session: Session,
    left_block: IdeaBlock,
    left_revision: IdeaBlockRevision,
    right_block: IdeaBlock,
    right_revision: IdeaBlockRevision,
    score: float,
    classifier: SemanticClassifier | None,
) -> SemanticCandidate:
    comparison = compare_protected_fields(
        left_revision,
        right_revision,
        left_domain_id=left_block.domain_id,
        right_domain_id=right_block.domain_id,
        left_entity_ids=_entity_ids(session, left_block.id),
        right_entity_ids=_entity_ids(session, right_block.id),
    )
    classification = classify_semantic_pair(
        left_revision, right_revision, comparison=comparison, score=score, classifier=classifier
    )
    lexical_score = weighted_jaccard(_revision_view(left_block), _revision_view(right_block))
    embedding_config = required_embedding_index_config()
    return SemanticCandidate(
        left_id=left_block.id,
        right_id=right_block.id,
        score=round(score, 4),
        lexical_score=lexical_score,
        action="review",
        reason=(
            "deterministic protected-field difference requires semantic review"
            if comparison.guard_conflicts
            else "embedding similarity is advisory and requires semantic review"
        ),
        protected_field_differences=comparison.differences,
        classification=classification,
        idea_block_revision_ids={left_block.id: left_revision.id, right_block.id: right_revision.id},
        embedding_model_id=embedding_config.model_id,
        embedding_index_version=embedding_config.index_version,
    )


def _persist_semantic_review_task(session: Session, candidate: SemanticCandidate) -> None:
    target_id = f"{candidate.left_id}:{candidate.right_id}"
    existing = session.scalar(
        select(ReviewTask).where(
            ReviewTask.target_id.in_((target_id, f"{candidate.right_id}:{candidate.left_id}")),
            ReviewTask.task_type == SEMANTIC_RESOLUTION_TASK_TYPE,
            ReviewTask.status.in_(("open", "assigned", "needs_evidence")),
        )
    )
    if existing is not None:
        return
    session.add(
        ReviewTask(
            task_type=SEMANTIC_RESOLUTION_TASK_TYPE,
            target_type="idea_block_pair",
            target_id=target_id,
            reason=f"{candidate.reason}; similarity_score={candidate.score}",
            recommendation=candidate.classification.outcome,
            evidence=[
                {
                    "protected_field_differences": candidate.protected_field_differences,
                    "classification": candidate.classification.model_dump(mode="json"),
                }
            ],
            metadata_={
                "semantic_key_version": SEMANTIC_KEY_VERSION,
                "idea_block_ids": [candidate.left_id, candidate.right_id],
                "idea_block_revision_ids": dict(candidate.idea_block_revision_ids),
                "model_version": candidate.classification.model_version,
                "rule_version": candidate.classification.rule_version,
                "embedding_model_id": candidate.embedding_model_id,
                "embedding_index_version": candidate.embedding_index_version,
                "score": candidate.score,
                "lexical_score": candidate.lexical_score,
                "suggested_outcome": candidate.classification.outcome,
                "decision_provenance": {
                    "model_version": candidate.classification.model_version,
                    "rule_version": candidate.classification.rule_version,
                    "embedding_model_id": candidate.embedding_model_id,
                    "embedding_index_version": candidate.embedding_index_version,
                    "idea_block_revision_ids": dict(candidate.idea_block_revision_ids),
                    "score": candidate.score,
                    "lexical_score": candidate.lexical_score,
                    "protected_field_differences": candidate.protected_field_differences,
                },
            },
        )
    )


def _entity_ids(session: Session, idea_block_id: str) -> tuple[str, ...]:
    return tuple(
        session.scalars(
            select(IdeaBlockEntity.entity_id)
            .where(IdeaBlockEntity.idea_block_id == idea_block_id)
            .order_by(IdeaBlockEntity.entity_id)
        ).all()
    )


def _bounded_limit(value: int, *, maximum: int) -> int:
    if value < 1:
        raise ValueError("candidate limit must be positive")
    return min(value, maximum)


def _remaining_deadline_ms(started: float, deadline_ms: int) -> int:
    return max(0, deadline_ms - int((monotonic() - started) * 1000))


def _cosine_score(distance: object) -> float:
    if not isinstance(distance, int | float | str):
        return 0.0
    try:
        return min(1.0, max(0.0, 1.0 - float(distance)))
    except (TypeError, ValueError):
        return 0.0


def retrieve_semantic_candidates(
    session: Session,
    *,
    revision_id: str,
    query_embedding: list[float],
    domain_id: str,
    authorized_domain_ids: Collection[str] | None = None,
    limit: int = 20,
    deadline_ms: int = 500,
) -> list[RetrievedSemanticCandidate]:
    """Return top current revisions from the bounded PostgreSQL vector query."""

    if session.get_bind().dialect.name != "postgresql":
        raise RuntimeError("semantic candidate retrieval requires PostgreSQL with pgvector")
    if not query_embedding:
        return []
    if authorized_domain_ids is not None and domain_id not in set(authorized_domain_ids):
        return []
    limit = _bounded_limit(limit, maximum=100)
    deadline_ms = max(1, min(deadline_ms, 10_000))
    session.execute(text(f"SET LOCAL statement_timeout = {deadline_ms}"))
    config = required_embedding_index_config()
    distance = EmbeddingRecord.embedding.cosine_distance(query_embedding).label("distance")
    candidate_embeddings = (
        select(EmbeddingRecord.target_id.label("revision_id"), IdeaBlock.id.label("idea_block_id"), distance)
        .join(IdeaBlockRevision, IdeaBlockRevision.id == EmbeddingRecord.target_id)
        .join(
            IdeaBlock,
            (IdeaBlock.current_revision_id == IdeaBlockRevision.id) & (IdeaBlock.id == IdeaBlockRevision.idea_block_id),
        )
        .where(
            IdeaBlockRevision.id != revision_id,
            IdeaBlockRevision.publication_status.in_(("candidate", "verified")),
            IdeaBlock.domain_id == domain_id,
            EmbeddingRecord.target_type == "idea_block_revision",
            EmbeddingRecord.model_id == config.model_id,
            EmbeddingRecord.index_version == config.index_version,
            EmbeddingRecord.dimensions == config.dimensions,
            IdeaBlock.archived_at.is_(None),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
        )
        .order_by(distance)
        .limit(limit)
        .subquery("semantic_embedding_candidates")
    )
    statement = (
        select(IdeaBlock, IdeaBlockRevision, candidate_embeddings.c.distance)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .join(candidate_embeddings, candidate_embeddings.c.idea_block_id == IdeaBlock.id)
        .where(IdeaBlockRevision.id == candidate_embeddings.c.revision_id)
        .order_by(candidate_embeddings.c.distance, IdeaBlockRevision.id)
        .limit(limit)
    )
    return [
        RetrievedSemanticCandidate(block=block, revision=revision, score=_cosine_score(distance_value))
        for block, revision, distance_value in session.execute(statement).all()
    ]


def _revision_for_view(block: IdeaBlock) -> IdeaBlockRevision:
    revision = block.current_revision
    if revision is None:
        raise ValueError(f"IdeaBlock {block.id} has no current revision")
    return revision


def _revision_view(block: IdeaBlock) -> _RevisionView:
    revision = _revision_for_view(block)
    return _RevisionView(
        id=block.id,
        statement=revision.statement,
        confidence=revision.confidence,
        metadata_=revision.metadata_ or {},
        type=revision.type,
        polarity=revision.polarity,
        qualifiers=revision.qualifiers or {},
        valid_from=revision.valid_from,
        valid_to=revision.valid_to,
    )
