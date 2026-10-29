from __future__ import annotations

from collections.abc import Collection
from math import sqrt

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.distillation import RetrievedSemanticCandidate
from margin_db.embeddings import required_embedding_index_config
from margin_db.knowledge_models import EmbeddingRecord, IdeaBlock, IdeaBlockRevision


def sqlite_semantic_candidate_retriever(
    session: Session,
    *,
    revision_id: str,
    query_embedding: list[float],
    domain_id: str,
    authorized_domain_ids: Collection[str] | None,
    limit: int,
    deadline_ms: int,
) -> list[RetrievedSemanticCandidate]:
    """Provide a small SQLite-only test double for the PostgreSQL vector query."""

    del deadline_ms
    if authorized_domain_ids is not None and domain_id not in set(authorized_domain_ids):
        return []
    config = required_embedding_index_config()
    rows = session.execute(
        select(IdeaBlock, IdeaBlockRevision, EmbeddingRecord.embedding)
        .join(
            IdeaBlockRevision,
            (IdeaBlock.current_revision_id == IdeaBlockRevision.id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .join(EmbeddingRecord, EmbeddingRecord.target_id == IdeaBlockRevision.id)
        .where(
            IdeaBlockRevision.id != revision_id,
            IdeaBlockRevision.publication_status.in_(("candidate", "verified")),
            IdeaBlock.domain_id == domain_id,
            IdeaBlock.archived_at.is_(None),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            EmbeddingRecord.target_type == "idea_block_revision",
            EmbeddingRecord.model_id == config.model_id,
            EmbeddingRecord.index_version == config.index_version,
            EmbeddingRecord.dimensions == config.dimensions,
        )
    ).all()
    candidates: list[RetrievedSemanticCandidate] = []
    for block, revision, embedding in rows:
        score = _cosine(query_embedding, embedding)
        if score > 0:
            candidates.append(RetrievedSemanticCandidate(block=block, revision=revision, score=score))
    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.revision.id))[:limit]


def _cosine(left: list[float], right: object) -> float:
    values = [float(value) for value in (right or [])]
    if not left or not values or len(left) != len(values):
        return 0.0
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in values))
    if not left_norm or not right_norm:
        return 0.0
    return round(sum(a * b for a, b in zip(left, values, strict=True)) / (left_norm * right_norm), 4)
