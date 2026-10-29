"""Affected-only materialization for Idea Block search inputs and embeddings."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from margin_db.entity_models import Entity, EntityAlias
from margin_db.knowledge_models import (
    EmbeddingRecord,
    IdeaBlock,
    IdeaBlockEntity,
    IdeaBlockRevision,
    SearchEmbeddingAssignment,
    SearchMaterializationJob,
)
from margin_embedding.interfaces import EMBEDDING_DIMENSIONS, EmbeddingIndexConfig

SEARCHABLE_PUBLICATION_STATUSES = frozenset({"candidate", "verified"})


@dataclass(frozen=True, slots=True)
class SearchEmbeddingCoverage:
    eligible_revision_count: int
    covered_revision_count: int
    invalid_dimension_count: int
    orphan_count: int

    @property
    def is_complete(self) -> bool:
        return (
            self.eligible_revision_count == self.covered_revision_count
            and self.invalid_dimension_count == 0
            and self.orphan_count == 0
        )


@dataclass(frozen=True, slots=True)
class SearchMaterializationCoverage:
    eligible_revision_count: int
    materialized_revision_count: int
    orphan_count: int
    duplicate_current_revision_count: int

    @property
    def is_complete(self) -> bool:
        return (
            self.eligible_revision_count == self.materialized_revision_count
            and self.orphan_count == 0
            and self.duplicate_current_revision_count == 0
        )


def initial_search_text(
    *,
    statement: str,
    qualifiers: Mapping[str, object] | None,
    idea_block_type: str,
    polarity: str,
    confidence: str,
    controlled_terms: Iterable[str] = (),
) -> str:
    """Build deterministic revision-owned text before linked labels are available."""

    return _normalize_search_text(
        (statement, idea_block_type, polarity, confidence, *controlled_terms, *_qualifier_terms(qualifiers or {}))
    )


def materialize_search_text(session: Session, *, idea_block_revision_id: str) -> bool:
    """Refresh one current, publishable revision without scanning unrelated rows."""

    row = session.execute(
        select(IdeaBlock, IdeaBlockRevision)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(IdeaBlockRevision.id == idea_block_revision_id)
        .limit(1)
    ).one_or_none()
    if row is None:
        return False
    block, revision = row
    if not _is_searchable(block, revision):
        revision.search_text = ""
        return False
    revision.search_text = _revision_search_text(session, revision)
    return True


def enqueue_search_materialization(session: Session, idea_block_revision_ids: Iterable[str], *, reason: str) -> int:
    """Queue only the distinct revision IDs whose owned search text changed."""

    revision_ids = tuple(dict.fromkeys(value for value in idea_block_revision_ids if value))
    if not revision_ids:
        return 0
    pending_ids = set(
        session.scalars(
            select(SearchMaterializationJob.idea_block_revision_id).where(
                SearchMaterializationJob.idea_block_revision_id.in_(revision_ids),
                SearchMaterializationJob.status.in_(("queued", "processing")),
            )
        ).all()
    )
    for revision_id in revision_ids:
        if revision_id in pending_ids:
            continue
        session.add(
            SearchMaterializationJob(
                id=f"searchjob_{uuid.uuid4().hex}", idea_block_revision_id=revision_id, reason=reason, status="queued"
            )
        )
    return len(revision_ids) - len(pending_ids)


def enqueue_search_materialization_for_entities(session: Session, entity_ids: Iterable[str], *, reason: str) -> int:
    """Refresh exactly the current revisions linked to changed catalog entities."""

    ids = tuple(dict.fromkeys(value for value in entity_ids if value))
    if not ids:
        return 0
    revision_ids = session.scalars(
        select(IdeaBlockRevision.id)
        .join(
            IdeaBlock,
            (IdeaBlock.current_revision_id == IdeaBlockRevision.id) & (IdeaBlock.id == IdeaBlockRevision.idea_block_id),
        )
        .join(IdeaBlockEntity, IdeaBlockEntity.idea_block_id == IdeaBlock.id)
        .where(
            IdeaBlockEntity.entity_id.in_(ids),
            IdeaBlockRevision.publication_status.in_(SEARCHABLE_PUBLICATION_STATUSES),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
        )
        .distinct()
        .order_by(IdeaBlockRevision.id)
    ).all()
    return enqueue_search_materialization(session, revision_ids, reason=reason)


def lease_search_materialization_jobs(
    session: Session, *, limit: int, idea_block_revision_ids: Iterable[str] = ()
) -> list[SearchMaterializationJob]:
    if limit <= 0:
        return []
    revision_ids = tuple(dict.fromkeys(value for value in idea_block_revision_ids if value))
    statement = select(SearchMaterializationJob).where(SearchMaterializationJob.status == "queued")
    if revision_ids:
        statement = statement.where(SearchMaterializationJob.idea_block_revision_id.in_(revision_ids))
    jobs = list(
        session.scalars(
            statement.order_by(SearchMaterializationJob.created_at, SearchMaterializationJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )
    leased_at = datetime.now(UTC).isoformat()
    for job in jobs:
        job.status = "processing"
        job.metadata_ = {
            **(job.metadata_ or {}),
            "attempt_count": int((job.metadata_ or {}).get("attempt_count", 0)) + 1,
            "leased_at": leased_at,
        }
    session.flush()
    return jobs


def process_search_materialization_jobs(
    session: Session,
    *,
    limit: int = 100,
    embedding_index: EmbeddingIndexConfig | None = None,
    idea_block_revision_ids: Iterable[str] = (),
) -> int:
    """Materialize a bounded batch and queue embeddings only for refreshed revisions."""

    jobs = lease_search_materialization_jobs(session, limit=limit, idea_block_revision_ids=idea_block_revision_ids)
    refreshed_ids: list[str] = []
    for job in jobs:
        try:
            if materialize_search_text(session, idea_block_revision_id=job.idea_block_revision_id):
                refreshed_ids.append(job.idea_block_revision_id)
        except Exception as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.metadata_ = {**(job.metadata_ or {}), "failed_at": datetime.now(UTC).isoformat()}
        else:
            job.status = "completed"
            job.error_message = None
            job.metadata_ = {**(job.metadata_ or {}), "completed_at": datetime.now(UTC).isoformat()}
    if embedding_index is not None and refreshed_ids:
        stage_search_embedding_assignment(session, embedding_index=embedding_index)
        from margin_db.embeddings import enqueue_idea_block_embeddings

        enqueue_idea_block_embeddings(
            session,
            refreshed_ids,
            model_id=embedding_index.model_id,
            index_version=embedding_index.index_version,
            reason="search_text_materialized",
        )
    session.flush()
    return len(refreshed_ids)


def requeue_search_materialization_job(session: Session, *, job_id: str) -> SearchMaterializationJob:
    job = session.get(SearchMaterializationJob, job_id)
    if job is None:
        raise ValueError(f"search materialization job not found: {job_id}")
    if job.status != "failed":
        raise ValueError("only failed search materialization jobs can be requeued")
    job.status = "queued"
    job.error_message = None
    return job


def stage_search_embedding_assignment(
    session: Session, *, embedding_index: EmbeddingIndexConfig
) -> SearchEmbeddingAssignment:
    """Record the fixed-dimension candidate assignment before it can become active."""

    _validate_embedding_index(embedding_index)
    assignment = session.scalar(
        select(SearchEmbeddingAssignment).where(
            SearchEmbeddingAssignment.model_id == embedding_index.model_id,
            SearchEmbeddingAssignment.index_version == embedding_index.index_version,
        )
    )
    if assignment is None:
        assignment = SearchEmbeddingAssignment(
            id=f"search_embedding_{uuid.uuid4().hex}",
            model_id=embedding_index.model_id,
            index_version=embedding_index.index_version,
            dimensions=embedding_index.dimensions,
            status="building",
        )
        session.add(assignment)
    elif assignment.dimensions != embedding_index.dimensions:
        raise ValueError("search embedding assignment dimensions cannot change for one model/index version")
    return assignment


def active_search_embedding_assignment(session: Session) -> SearchEmbeddingAssignment | None:
    return session.scalar(
        select(SearchEmbeddingAssignment)
        .where(SearchEmbeddingAssignment.status == "active")
        .order_by(SearchEmbeddingAssignment.activated_at.desc(), SearchEmbeddingAssignment.id)
        .limit(1)
    )


def search_embedding_coverage(session: Session, *, embedding_index: EmbeddingIndexConfig) -> SearchEmbeddingCoverage:
    """Report the clean-target coverage gates before an assignment is switched live."""

    _validate_embedding_index(embedding_index)
    eligible = _eligible_current_revision_ids().subquery()
    eligible_count = int(session.scalar(select(func.count()).select_from(eligible)) or 0)
    covered_count = int(
        session.scalar(
            select(func.count())
            .select_from(eligible)
            .join(
                EmbeddingRecord,
                (EmbeddingRecord.target_id == eligible.c.id)
                & (EmbeddingRecord.idea_block_revision_id == eligible.c.id)
                & (EmbeddingRecord.target_type == "idea_block_revision")
                & (EmbeddingRecord.model_id == embedding_index.model_id)
                & (EmbeddingRecord.index_version == embedding_index.index_version)
                & (EmbeddingRecord.dimensions == EMBEDDING_DIMENSIONS),
            )
        )
        or 0
    )
    invalid_dimension_count = int(
        session.scalar(
            select(func.count())
            .select_from(EmbeddingRecord)
            .where(
                EmbeddingRecord.target_type == "idea_block_revision",
                EmbeddingRecord.model_id == embedding_index.model_id,
                EmbeddingRecord.index_version == embedding_index.index_version,
                EmbeddingRecord.dimensions != EMBEDDING_DIMENSIONS,
            )
        )
        or 0
    )
    orphan_count = int(
        session.scalar(
            select(func.count())
            .select_from(EmbeddingRecord)
            .outerjoin(IdeaBlockRevision, IdeaBlockRevision.id == EmbeddingRecord.target_id)
            .where(
                EmbeddingRecord.target_type == "idea_block_revision",
                EmbeddingRecord.model_id == embedding_index.model_id,
                EmbeddingRecord.index_version == embedding_index.index_version,
                or_(
                    IdeaBlockRevision.id.is_(None),
                    EmbeddingRecord.idea_block_revision_id.is_(None),
                    EmbeddingRecord.idea_block_revision_id != EmbeddingRecord.target_id,
                ),
            )
        )
        or 0
    )
    return SearchEmbeddingCoverage(
        eligible_revision_count=eligible_count,
        covered_revision_count=covered_count,
        invalid_dimension_count=invalid_dimension_count,
        orphan_count=orphan_count,
    )


def search_materialization_coverage(session: Session) -> SearchMaterializationCoverage:
    """Report owned text and current-revision integrity before dense search is enabled."""

    eligible = _eligible_current_revision_ids().subquery()
    eligible_count = int(session.scalar(select(func.count()).select_from(eligible)) or 0)
    materialized_count = int(
        session.scalar(
            select(func.count())
            .select_from(eligible)
            .join(IdeaBlockRevision, IdeaBlockRevision.id == eligible.c.id)
            .where(func.length(func.trim(IdeaBlockRevision.search_text)) > 0)
        )
        or 0
    )
    orphan_count = int(
        session.scalar(
            select(func.count())
            .select_from(IdeaBlock)
            .outerjoin(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .where(IdeaBlock.current_revision_id.is_not(None), IdeaBlockRevision.id.is_(None))
        )
        or 0
    )
    duplicate_current_revision_count = int(
        session.scalar(
            select(func.count()).select_from(
                select(IdeaBlock.current_revision_id)
                .where(IdeaBlock.current_revision_id.is_not(None))
                .group_by(IdeaBlock.current_revision_id)
                .having(func.count() > 1)
                .subquery()
            )
        )
        or 0
    )
    return SearchMaterializationCoverage(
        eligible_revision_count=eligible_count,
        materialized_revision_count=materialized_count,
        orphan_count=orphan_count,
        duplicate_current_revision_count=duplicate_current_revision_count,
    )


def activate_search_embedding_assignment(
    session: Session, *, embedding_index: EmbeddingIndexConfig
) -> SearchEmbeddingAssignment:
    """Switch only a fully covered fixed-dimension index version into ordinary search."""

    assignment = stage_search_embedding_assignment(session, embedding_index=embedding_index)
    materialization = search_materialization_coverage(session)
    if not materialization.is_complete:
        raise ValueError(
            "search text materialization is not ready: "
            f"eligible={materialization.eligible_revision_count}, "
            f"materialized={materialization.materialized_revision_count}, "
            f"orphans={materialization.orphan_count}, "
            f"duplicate_current_revisions={materialization.duplicate_current_revision_count}"
        )
    coverage = search_embedding_coverage(session, embedding_index=embedding_index)
    if not coverage.is_complete:
        raise ValueError(
            "search embedding assignment is not ready: "
            f"eligible={coverage.eligible_revision_count}, covered={coverage.covered_revision_count}, "
            f"invalid_dimensions={coverage.invalid_dimension_count}, orphans={coverage.orphan_count}"
        )
    if assignment.status == "active":
        return assignment
    previous = active_search_embedding_assignment(session)
    now = datetime.now(UTC)
    if previous is not None:
        previous.status = "retired"
        previous.retired_at = now
    assignment.status = "active"
    assignment.activated_at = now
    if previous is not None:
        session.execute(
            delete(EmbeddingRecord).where(
                EmbeddingRecord.target_type == "idea_block_revision",
                EmbeddingRecord.model_id == previous.model_id,
                EmbeddingRecord.index_version == previous.index_version,
            )
        )
    session.flush()
    return assignment


def _eligible_current_revision_ids():
    return (
        select(IdeaBlockRevision.id)
        .join(
            IdeaBlock,
            (IdeaBlock.current_revision_id == IdeaBlockRevision.id) & (IdeaBlock.id == IdeaBlockRevision.idea_block_id),
        )
        .where(
            IdeaBlockRevision.publication_status.in_(SEARCHABLE_PUBLICATION_STATUSES),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
        )
    )


def _revision_search_text(session: Session, revision: IdeaBlockRevision) -> str:
    labels = _approved_entity_labels(session, idea_block_id=revision.idea_block_id)
    return _normalize_search_text(
        (
            revision.statement,
            revision.type,
            revision.polarity,
            revision.confidence,
            *_metadata_controlled_terms(revision),
            *_qualifier_terms(revision.qualifiers or {}),
            *labels,
        )
    )


def _approved_entity_labels(session: Session, *, idea_block_id: str) -> tuple[str, ...]:
    preferred_labels = session.scalars(
        select(Entity.preferred_label)
        .join(IdeaBlockEntity, IdeaBlockEntity.entity_id == Entity.id)
        .join(IdeaBlock, IdeaBlock.id == IdeaBlockEntity.idea_block_id)
        .where(
            IdeaBlockEntity.idea_block_id == idea_block_id,
            Entity.lifecycle_status == "active",
            or_(Entity.domain_id.is_(None), Entity.domain_id == IdeaBlock.domain_id),
        )
        .order_by(Entity.id)
    ).all()
    aliases = session.scalars(
        select(EntityAlias.alias)
        .join(Entity, Entity.id == EntityAlias.entity_id)
        .join(IdeaBlockEntity, IdeaBlockEntity.entity_id == Entity.id)
        .join(IdeaBlock, IdeaBlock.id == IdeaBlockEntity.idea_block_id)
        .where(
            IdeaBlockEntity.idea_block_id == idea_block_id,
            Entity.lifecycle_status == "active",
            EntityAlias.lifecycle_status == "active",
            or_(Entity.domain_id.is_(None), Entity.domain_id == IdeaBlock.domain_id),
        )
        .order_by(Entity.id, EntityAlias.id)
    ).all()
    return tuple(str(value) for value in (*preferred_labels, *aliases) if str(value).strip())


def _is_searchable(block: IdeaBlock, revision: IdeaBlockRevision) -> bool:
    return (
        revision.publication_status in SEARCHABLE_PUBLICATION_STATUSES
        and block.merged_into_id is None
        and block.tombstoned_at is None
        and block.archived_at is None
    )


def _metadata_controlled_terms(revision: IdeaBlockRevision) -> tuple[str, ...]:
    tags = (revision.metadata_ or {}).get("tags", [])
    if not isinstance(tags, list):
        return ()
    return tuple(str(tag) for tag in tags if isinstance(tag, str) and tag.strip())


def _qualifier_terms(value: object) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        terms: list[str] = []
        for key in sorted(value, key=str):
            terms.append(str(key))
            terms.extend(_qualifier_terms(value[key]))
        return tuple(terms)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        terms = [term for item in value for term in _qualifier_terms(item)]
        return tuple(sorted(terms))
    if value is None:
        return ()
    return (str(value),)


def _normalize_search_text(values: Iterable[str]) -> str:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = re.sub(r"\s+", " ", str(value)).strip()
        if term and term not in seen:
            normalized.append(term)
            seen.add(term)
    return " ".join(normalized)


def _validate_embedding_index(embedding_index: EmbeddingIndexConfig) -> None:
    if embedding_index.dimensions != EMBEDDING_DIMENSIONS:
        raise ValueError(f"search embeddings require exactly {EMBEDDING_DIMENSIONS} dimensions")
    if not embedding_index.model_id.strip() or not embedding_index.index_version.strip():
        raise ValueError("search embedding assignments require model_id and index_version")


__all__ = [
    "SEARCHABLE_PUBLICATION_STATUSES",
    "SearchEmbeddingCoverage",
    "SearchMaterializationCoverage",
    "activate_search_embedding_assignment",
    "active_search_embedding_assignment",
    "enqueue_search_materialization",
    "enqueue_search_materialization_for_entities",
    "initial_search_text",
    "lease_search_materialization_jobs",
    "materialize_search_text",
    "process_search_materialization_jobs",
    "requeue_search_materialization_job",
    "search_embedding_coverage",
    "search_materialization_coverage",
    "stage_search_embedding_assignment",
]
