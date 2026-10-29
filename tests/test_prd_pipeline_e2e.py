from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock
from margin_db.object_storage import FilesystemObjectStore
from margin_db.source_ingestion import SourceIndex, SourceIngestionService
from tests.helpers.converters import TestTextDocumentConverter
from tests.helpers.source_scope import seed_source_scope, upload_scoped_source
from workers.extract.pipeline import MarginIdeaBlockPipeline


def _session_factory() -> sessionmaker:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _service(tmp_path: Path) -> SourceIngestionService:
    return SourceIngestionService(
        FilesystemObjectStore(tmp_path / "objects"), converter=TestTextDocumentConverter(), source_index=SourceIndex()
    )


def test_prd_pipeline_repeatably_persists_canonical_extraction(tmp_path: Path) -> None:
    session_factory = _session_factory()
    service = _service(tmp_path)
    markdown = b"""# Order Architecture

Order Service depends on Kafka Service.

# Billing

Billing Service depends on Order Service.
"""

    with session_factory() as session:
        upload = upload_scoped_source(
            service,
            session,
            filename="order-architecture.md",
            media_type="text/markdown",
            data=markdown,
            domain_id="OSS",
            category="Design Document",
            sensitivity="internal",
        )
        pipeline = MarginIdeaBlockPipeline()
        first = pipeline.run_once(session, source_document_id=upload.source_document.id)
        first_block_ids = tuple(sorted(session.scalars(select(IdeaBlock.id)).all()))
        first_evidence = {
            (row.idea_block_id, row.source_element_id, row.source_quote, row.polarity)
            for row in session.scalars(select(ClaimEvidence))
        }
        second = pipeline.run_once(session, source_document_id=upload.source_document.id)
        second_block_ids = tuple(sorted(session.scalars(select(IdeaBlock.id)).all()))
        second_evidence = {
            (row.idea_block_id, row.source_element_id, row.source_quote, row.polarity)
            for row in session.scalars(select(ClaimEvidence))
        }

    assert first.stages == ("ingest", "extract", "distill")
    assert first.report.idea_block_count > 0
    assert second.stages == first.stages
    assert second_block_ids == first_block_ids
    assert second_evidence == first_evidence


def test_prd_pipeline_reprocessing_keeps_canonical_history(tmp_path: Path) -> None:
    session_factory = _session_factory()
    service = _service(tmp_path)

    with session_factory() as session:
        upload = upload_scoped_source(
            service,
            session,
            filename="order-architecture.md",
            media_type="text/markdown",
            data=(
                b"# Order Architecture\n\nOrder Service depends on Kafka Service.\n"
                b"\n# Billing\n\nBilling Service depends on Order Service.\n"
            ),
            domain_id="OSS",
            category="Design Document",
            sensitivity="internal",
        )
        source_document_id = upload.source_document.id
        first = MarginIdeaBlockPipeline().run_once(session, source_document_id=source_document_id)

        replacement = service.object_store.put_bytes(
            b"# Replacement\n\nBilling Service depends on Order Service.\n"
            b"\n# Notifications\n\nNotifications Service depends on Billing Service.\n"
        )
        document = upload.source_document
        document.sha256 = replacement.content_hash
        document.object_key = replacement.content_hash
        document.size_bytes = replacement.size_bytes
        job = service.create_reprocess_job(session, source_document_id)
        assert service.process_job(session, job.id).status == "extracting"
        second = MarginIdeaBlockPipeline().run_once(session, source_document_id=source_document_id)
        idea_blocks = session.scalars(select(IdeaBlock)).all()

    assert first.report.idea_block_count > 0
    assert second.report.idea_block_count > 0
    assert idea_blocks
