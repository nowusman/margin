from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from margin_db.knowledge_models import EmbeddingJob, EmbeddingRecord, IdeaBlockRevision, SearchMaterializationJob
from margin_db.source_models import SourceElement
from margin_embedding.factory import required_embedding_index_config
from margin_embedding.interfaces import (
    EMBEDDING_DIMENSIONS,
    EmbeddingIndexConfig,
    EmbeddingProvider,
    validate_embedding_batch,
)
from margin_embedding.text import embedding_content_hash, embedding_text_for_source_element

logger = logging.getLogger(__name__)

EmbeddingTargetType = Literal["idea_block_revision", "source_element"]
_EMBEDDING_TARGET_TYPES: frozenset[str] = frozenset({"idea_block_revision", "source_element"})

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "EmbeddingIndexConfig",
    "EmbeddingTargetType",
    "embedding_content_hash",
    "embedding_record_is_current",
    "enqueue_embedding_job",
    "enqueue_idea_block_embeddings",
    "enqueue_source_element_embeddings",
    "lease_embedding_jobs",
    "process_embedding_jobs",
    "required_embedding_index_config",
    "upsert_embedding_record",
]


def _validate_target_type(target_type: str) -> None:
    if target_type not in _EMBEDDING_TARGET_TYPES:
        allowed = ", ".join(sorted(_EMBEDDING_TARGET_TYPES))
        raise ValueError(f"unsupported embedding target type {target_type!r}; allowed targets are {allowed}")


def _idea_block_text(session: Session, revision: IdeaBlockRevision) -> str:
    pending_materialization = session.scalar(
        select(SearchMaterializationJob.id).where(
            SearchMaterializationJob.idea_block_revision_id == revision.id,
            SearchMaterializationJob.status.in_(("queued", "processing")),
        )
    )
    if pending_materialization is not None:
        raise ValueError(f"IdeaBlockRevision search text is still pending materialization: {revision.id}")
    if not revision.search_text.strip():
        raise ValueError(f"IdeaBlockRevision has no materialized search text: {revision.id}")
    return revision.search_text


def _target_text(session: Session, target_type: EmbeddingTargetType, target_id: str) -> str:
    _validate_target_type(target_type)
    if target_type == "idea_block_revision":
        revision = session.get(IdeaBlockRevision, target_id)
        if revision is None:
            raise ValueError(f"IdeaBlockRevision not found for embedding target: {target_id}")
        return _idea_block_text(session, revision)
    element = session.get(SourceElement, target_id)
    if element is None:
        raise ValueError(f"SourceElement not found for embedding target: {target_id}")
    text = embedding_text_for_source_element(element)
    if not text:
        raise ValueError(f"SourceElement has no eligible text or visual description: {target_id}")
    return text


def embedding_record_is_current(
    session: Session, *, target_type: EmbeddingTargetType, target_id: str, model_id: str, index_version: str
) -> bool:
    expected_hash = embedding_content_hash(_target_text(session, target_type, target_id))
    record_hash = session.scalar(
        select(EmbeddingRecord.content_hash).where(
            EmbeddingRecord.target_type == target_type,
            EmbeddingRecord.target_id == target_id,
            EmbeddingRecord.model_id == model_id,
            EmbeddingRecord.index_version == index_version,
            EmbeddingRecord.dimensions == EMBEDDING_DIMENSIONS,
        )
    )
    return record_hash == expected_hash


def enqueue_embedding_job(
    session: Session,
    *,
    target_type: EmbeddingTargetType,
    target_id: str,
    model_id: str,
    index_version: str,
    reason: str,
    ingestion_job_id: str | None = None,
) -> EmbeddingJob:
    _validate_target_type(target_type)
    existing = session.scalar(
        select(EmbeddingJob).where(
            EmbeddingJob.target_type == target_type,
            EmbeddingJob.target_id == target_id,
            EmbeddingJob.model_id == model_id,
            EmbeddingJob.index_version == index_version,
            EmbeddingJob.status.in_(("queued", "processing")),
        )
    )
    if existing is not None:
        if existing.ingestion_job_id is None and ingestion_job_id is not None:
            existing.ingestion_job_id = ingestion_job_id
        return existing
    job = EmbeddingJob(
        id=f"embjob_{uuid.uuid4().hex}",
        target_type=target_type,
        target_id=target_id,
        model_id=model_id,
        index_version=index_version,
        reason=reason,
        status="queued",
        idea_block_revision_id=target_id if target_type == "idea_block_revision" else None,
        ingestion_job_id=ingestion_job_id,
    )
    session.add(job)
    return job


def enqueue_idea_block_embeddings(
    session: Session,
    idea_block_revision_ids: Iterable[str],
    *,
    model_id: str,
    index_version: str,
    reason: str = "idea_block_changed",
    ingestion_job_id: str | None = None,
) -> int:
    count = 0
    for revision_id in dict.fromkeys(idea_block_revision_ids):
        enqueue_embedding_job(
            session,
            target_type="idea_block_revision",
            target_id=revision_id,
            model_id=model_id,
            index_version=index_version,
            reason=reason,
            ingestion_job_id=ingestion_job_id,
        )
        count += 1
    return count


def enqueue_source_element_embeddings(
    session: Session,
    source_element_ids: Iterable[str],
    *,
    model_id: str,
    index_version: str,
    reason: str = "source_element_indexed",
    ingestion_job_id: str | None = None,
) -> int:
    count = 0
    for element_id in dict.fromkeys(source_element_ids):
        element = session.get(SourceElement, element_id)
        if element is None:
            continue
        text = embedding_text_for_source_element(element)
        if not text:
            continue
        content_hash = embedding_content_hash(text)
        existing = session.scalar(
            select(EmbeddingRecord.id).where(
                EmbeddingRecord.target_type == "source_element",
                EmbeddingRecord.target_id == element_id,
                EmbeddingRecord.model_id == model_id,
                EmbeddingRecord.index_version == index_version,
                EmbeddingRecord.content_hash == content_hash,
                EmbeddingRecord.dimensions == EMBEDDING_DIMENSIONS,
            )
        )
        if existing is None:
            enqueue_embedding_job(
                session,
                target_type="source_element",
                target_id=element_id,
                model_id=model_id,
                index_version=index_version,
                reason=reason,
                ingestion_job_id=ingestion_job_id,
            )
            count += 1
    return count


def lease_embedding_jobs(
    session: Session,
    *,
    model_id: str,
    index_version: str,
    limit: int,
    targets: tuple[tuple[EmbeddingTargetType, str], ...] | None = None,
    ingestion_job_id: str | None = None,
) -> list[EmbeddingJob]:
    """Claim queued jobs in the DB transaction so workers do not duplicate work."""
    if limit <= 0 or (targets is not None and not targets):
        return []
    if targets is not None:
        for target_type, _target_id in targets:
            _validate_target_type(target_type)
    query = (
        select(EmbeddingJob)
        .where(
            EmbeddingJob.status == "queued",
            EmbeddingJob.model_id == model_id,
            EmbeddingJob.index_version == index_version,
        )
        .order_by(EmbeddingJob.created_at, EmbeddingJob.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if ingestion_job_id is not None:
        query = query.where(EmbeddingJob.ingestion_job_id == ingestion_job_id)
    if targets is not None:
        query = query.where(
            or_(
                *(
                    (EmbeddingJob.target_type == target_type) & (EmbeddingJob.target_id == target_id)
                    for target_type, target_id in targets
                )
            )
        )
    jobs = list(session.scalars(query).all())
    leased_at = datetime.now(UTC).isoformat()
    for job in jobs:
        job.status = "processing"
        job.metadata_ = {
            **(job.metadata_ or {}),
            "attempt_count": int((job.metadata_ or {}).get("attempt_count", 0)) + 1,
            "lease_id": job.id,
            "leased_at": leased_at,
        }
    session.flush()
    return jobs


def process_embedding_jobs(
    session: Session,
    *,
    provider: EmbeddingProvider,
    limit: int = 100,
    targets: tuple[tuple[EmbeddingTargetType, str], ...] | None = None,
    ingestion_job_id: str | None = None,
) -> int:
    start = datetime.now(UTC)
    request_payload = {
        "limit": limit,
        "provider_id": provider.provider_id,
        "model_id": provider.model_id,
        "index_version": provider.index_version,
    }
    jobs = lease_embedding_jobs(
        session,
        model_id=provider.model_id,
        index_version=provider.index_version,
        limit=limit,
        targets=targets,
        ingestion_job_id=ingestion_job_id,
    )
    completed = 0
    valid_jobs: list[EmbeddingJob] = []
    texts: list[str] = []
    for job in jobs:
        try:
            text = _target_text(session, job.target_type, job.target_id)
        except Exception as exc:
            _fail_job(job, exc)
        else:
            valid_jobs.append(job)
            texts.append(text)
    if valid_jobs:
        try:
            vectors = provider.embed(tuple(texts))
            validate_embedding_batch(vectors, expected_count=len(valid_jobs), declared_dimensions=provider.dimensions)
        except Exception as exc:
            for job in valid_jobs:
                _fail_job(job, exc)
        else:
            for job, text, vector in zip(valid_jobs, texts, vectors, strict=True):
                try:
                    upsert_embedding_record(
                        session,
                        target_type=job.target_type,
                        target_id=job.target_id,
                        model_id=provider.model_id,
                        index_version=provider.index_version,
                        vector=vector,
                        content_hash=embedding_content_hash(text),
                        provider_id=provider.provider_id,
                        dimensions=provider.dimensions,
                    )
                except Exception as exc:
                    _fail_job(job, exc)
                else:
                    job.status = "completed"
                    job.error_message = None
                    job.metadata_ = {**(job.metadata_ or {}), "completed_at": datetime.now(UTC).isoformat()}
                    completed += 1
    session.flush()
    _log_embedding_interaction(
        event="process_embedding_jobs",
        request_payload={**request_payload, "queued_job_count": len(jobs)},
        response_payload={
            "completed": completed,
            "failed": sum(job.status == "failed" for job in jobs),
            "job_ids": [str(job.id) for job in jobs],
        },
        start=start,
    )
    return completed


def upsert_embedding_record(
    session: Session,
    *,
    target_type: EmbeddingTargetType,
    target_id: str,
    model_id: str,
    index_version: str,
    vector: list[float],
    content_hash: str,
    provider_id: str,
    dimensions: int,
) -> EmbeddingRecord:
    _validate_target_type(target_type)
    if dimensions != EMBEDDING_DIMENSIONS or len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"embedding_records requires exactly {EMBEDDING_DIMENSIONS} dimensions; "
            f"received declared={dimensions}, vector={len(vector)}"
        )
    row = session.scalar(
        select(EmbeddingRecord).where(
            EmbeddingRecord.target_type == target_type,
            EmbeddingRecord.target_id == target_id,
            EmbeddingRecord.model_id == model_id,
            EmbeddingRecord.index_version == index_version,
        )
    )
    if row is None:
        row = EmbeddingRecord(
            id=f"emb_{uuid.uuid4().hex}",
            target_type=target_type,
            target_id=target_id,
            model_id=model_id,
            index_version=index_version,
            embedding=vector,
            content_hash=content_hash,
            provider_id=provider_id,
            dimensions=EMBEDDING_DIMENSIONS,
            idea_block_revision_id=target_id if target_type == "idea_block_revision" else None,
        )
        session.add(row)
    else:
        row.embedding = vector
        row.content_hash = content_hash
        row.provider_id = provider_id
        row.dimensions = EMBEDDING_DIMENSIONS
        row.idea_block_revision_id = target_id if target_type == "idea_block_revision" else None
    return row


def _fail_job(job: EmbeddingJob, error: Exception) -> None:
    job.status = "failed"
    job.error_message = str(error)


def _log_embedding_interaction(
    *, event: str, request_payload: dict[str, object], response_payload: dict[str, object] | None, start: datetime
) -> None:
    end = datetime.now(UTC)
    payload = {
        "event": event,
        "request": request_payload,
        "response": response_payload,
        "start_timestamp": start.isoformat(),
        "end_timestamp": end.isoformat(),
        "duration_ms": round((end - start).total_seconds() * 1000, 3),
    }
    logger.info(
        "embedding_interaction %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        extra={"embedding_interaction": payload},
    )
