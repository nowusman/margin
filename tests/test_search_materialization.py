from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.embeddings import upsert_embedding_record
from margin_db.entity_catalog import add_alias, create_entity, update_entity
from margin_db.entity_catalog_lifecycle import retire_entity
from margin_db.entity_models import Entity
from margin_db.idea_block_resolution import create_idea_block_revision
from margin_db.knowledge_models import (
    EmbeddingJob,
    EmbeddingRecord,
    IdeaBlock,
    IdeaBlockEntity,
    IdeaBlockRevision,
    SearchEmbeddingAssignment,
    SearchMaterializationJob,
)
from margin_db.search_materialization import (
    activate_search_embedding_assignment,
    process_search_materialization_jobs,
    search_embedding_coverage,
    search_materialization_coverage,
)
from margin_db.user_models import Domain
from margin_embedding.interfaces import EMBEDDING_DIMENSIONS, EmbeddingIndexConfig


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _searchable_revision(session: Session) -> tuple[IdeaBlock, IdeaBlockRevision]:
    session.add(Domain(id="ARCH", name="Architecture"))
    entity = create_entity(
        session,
        preferred_label="PostgreSQL",
        ontology_type="datastore",
        domain_id="ARCH",
        reason="search materialization test entity",
    )
    add_alias(
        session,
        entity=entity,
        alias="Postgres",
        source_provenance={"kind": "test"},
        actor_id="test",
        reason="search materialization test alias",
    )
    block = IdeaBlock(id="ib_search", domain_id="ARCH")
    session.add(block)
    session.flush()
    revision = create_idea_block_revision(
        session,
        block,
        statement="Use PostgreSQL for the architecture evidence store.",
        normalized_statement="use postgresql for the architecture evidence store",
        type="decision",
        confidence="verified",
        polarity="supports",
        qualifiers={"scope": "architecture", "retention_days": 365},
        valid_from=None,
        valid_to=None,
        publication_status="verified",
        actor="test",
        process="test",
        change_reason="test revision",
    )
    session.add(IdeaBlockEntity(idea_block_id=block.id, entity_id=entity.id, role="subject"))
    session.flush()
    return block, revision


def test_materialization_owns_search_text_and_refreshes_only_linked_catalog_changes() -> None:
    with _session() as session:
        block, revision = _searchable_revision(session)
        initial_jobs = list(session.scalars(select(SearchMaterializationJob)).all())

        assert len(initial_jobs) == 1
        assert revision.search_text.startswith(
            "Use PostgreSQL for the architecture evidence store. decision supports verified"
        )
        assert "scope architecture" in revision.search_text
        assert "retention_days 365" in revision.search_text

        completed = process_search_materialization_jobs(session)
        session.flush()
        refreshed = session.get(IdeaBlockRevision, revision.id)
        coverage = search_materialization_coverage(session)

        assert completed == 1
        assert refreshed is not None
        assert "PostgreSQL" in refreshed.search_text
        assert "Postgres" in refreshed.search_text
        assert coverage.is_complete
        assert session.scalar(select(EmbeddingJob).where(EmbeddingJob.target_id == revision.id)) is None


def test_entity_label_changes_queue_and_refresh_only_affected_current_revision() -> None:
    with _session() as session:
        block, revision = _searchable_revision(session)
        process_search_materialization_jobs(session)
        entity_link = session.scalar(select(IdeaBlockEntity).where(IdeaBlockEntity.idea_block_id == block.id))
        assert entity_link is not None

        entity = session.get(Entity, entity_link.entity_id)
        assert entity is not None
        update_entity(
            session,
            entity=entity,
            preferred_label="PostgreSQL 16",
            actor_id="test",
            reason="test current label refresh",
        )
        queued = list(
            session.scalars(select(SearchMaterializationJob).where(SearchMaterializationJob.status == "queued")).all()
        )

        assert [job.idea_block_revision_id for job in queued] == [revision.id]

        completed = process_search_materialization_jobs(session)
        refreshed = session.get(IdeaBlockRevision, revision.id)

        assert completed == 1
        assert refreshed is not None
        assert "PostgreSQL 16" in refreshed.search_text


def test_approved_alias_change_queues_and_refreshes_the_linked_current_revision() -> None:
    with _session() as session:
        block, revision = _searchable_revision(session)
        process_search_materialization_jobs(session)
        entity_link = session.scalar(select(IdeaBlockEntity).where(IdeaBlockEntity.idea_block_id == block.id))
        assert entity_link is not None

        entity = session.get(Entity, entity_link.entity_id)
        assert entity is not None
        add_alias(
            session,
            entity=entity,
            alias="PG",
            source_provenance={"kind": "test"},
            actor_id="test",
            reason="test approved alias refresh",
        )
        queued = list(
            session.scalars(select(SearchMaterializationJob).where(SearchMaterializationJob.status == "queued")).all()
        )

        assert [job.idea_block_revision_id for job in queued] == [revision.id]
        assert process_search_materialization_jobs(session) == 1
        refreshed = session.get(IdeaBlockRevision, revision.id)
        assert refreshed is not None
        assert "PG" in refreshed.search_text


def test_entity_review_lifecycle_change_removes_ineligible_labels_from_current_revision() -> None:
    with _session() as session:
        block, revision = _searchable_revision(session)
        text_without_entity_labels = revision.search_text
        process_search_materialization_jobs(session)
        entity_link = session.scalar(select(IdeaBlockEntity).where(IdeaBlockEntity.idea_block_id == block.id))
        assert entity_link is not None

        retire_entity(
            session,
            entity=session.get(Entity, entity_link.entity_id),
            actor_id="test",
            reason="test search text lifecycle refresh",
        )
        queued = list(
            session.scalars(select(SearchMaterializationJob).where(SearchMaterializationJob.status == "queued")).all()
        )

        assert [job.idea_block_revision_id for job in queued] == [revision.id]
        assert process_search_materialization_jobs(session) == 1
        refreshed = session.get(IdeaBlockRevision, revision.id)
        assert refreshed is not None
        assert refreshed.search_text == text_without_entity_labels


def test_assignment_requires_complete_current_revision_coverage_before_activation() -> None:
    config = EmbeddingIndexConfig(model_id="test-model", index_version="test-index")
    with _session() as session:
        _block, revision = _searchable_revision(session)
        process_search_materialization_jobs(session)

        incomplete = search_embedding_coverage(session, embedding_index=config)
        assert incomplete.eligible_revision_count == 1
        assert incomplete.covered_revision_count == 0
        with pytest.raises(ValueError, match="not ready"):
            activate_search_embedding_assignment(session, embedding_index=config)

        upsert_embedding_record(
            session,
            target_type="idea_block_revision",
            target_id=revision.id,
            model_id=config.model_id,
            index_version=config.index_version,
            vector=[0.0] * EMBEDDING_DIMENSIONS,
            content_hash="test-content-hash",
            provider_id="test-provider",
            dimensions=EMBEDDING_DIMENSIONS,
        )
        complete = search_embedding_coverage(session, embedding_index=config)
        assignment = activate_search_embedding_assignment(session, embedding_index=config)

        assert complete.is_complete
        assert assignment.status == "active"
        assert (
            session.scalar(select(SearchEmbeddingAssignment).where(SearchEmbeddingAssignment.status == "active"))
            == assignment
        )


def test_embedding_coverage_rejects_missing_revision_association() -> None:
    config = EmbeddingIndexConfig(model_id="test-model", index_version="test-index")
    with _session() as session:
        _block, revision = _searchable_revision(session)
        process_search_materialization_jobs(session)
        session.add(
            EmbeddingRecord(
                id="emb_mismatched_target",
                target_type="idea_block_revision",
                target_id=revision.id,
                idea_block_revision_id=None,
                model_id=config.model_id,
                index_version=config.index_version,
                embedding=[0.0] * EMBEDDING_DIMENSIONS,
                content_hash="malformed",
                provider_id="test-provider",
                dimensions=EMBEDDING_DIMENSIONS,
            )
        )
        session.flush()

        coverage = search_embedding_coverage(session, embedding_index=config)

        assert coverage.eligible_revision_count == 1
        assert coverage.covered_revision_count == 0
        assert coverage.orphan_count == 1
