from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from margin_db.extraction import ExtractionPersistenceService, extraction_quality_report
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision
from margin_db.object_storage import FilesystemObjectStore
from margin_db.source_ingestion import SourceIndex, SourceIngestionService
from margin_db.source_models import SourceElement
from margin_schema.extraction import primary_claim_evidence
from tests.helpers.converters import TestTextDocumentConverter
from tests.helpers.source_scope import seed_source_scope, upload_scoped_source
from workers.extract.markdown import DeterministicMarkdownExtractor


def _session_factory() -> sessionmaker:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _service(tmp_path: Path) -> SourceIngestionService:
    return SourceIngestionService(
        FilesystemObjectStore(tmp_path / "objects"), converter=TestTextDocumentConverter(), source_index=SourceIndex()
    )


def test_deterministic_markdown_extracts_structured_claims_from_table_rows() -> None:
    elements = [
        type(
            "Element",
            (),
            {
                "id": "elem_table",
                "kind": "table",
                "text": (
                    "| System | Owner | Status | Technology | Version |\n"
                    "| --- | --- | --- | --- | --- |\n"
                    "| Billing API | Platform Team | active | PostgreSQL | 16 |\n"
                ),
            },
        )()
    ]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    assert len(output.idea_blocks) == 3
    block = output.idea_blocks[0]
    assert block.statement == "Billing API is owned by Platform Team."
    assert block.type == "structured_table_row"
    assert primary_claim_evidence(block).source_element_id == "elem_table"
    assert primary_claim_evidence(block).source_quote == "| Billing API | Platform Team | active | PostgreSQL | 16 |"
    assert block.metadata == {
        "claim_type": "owner",
        "subject": "Billing API",
        "attribute": "owner",
        "value": "Platform Team",
        "row_values": ["Billing API", "Platform Team", "active", "PostgreSQL", "16"],
        "verification_policy": "normalized_table_assertion",
    }
    assert [item.metadata["claim_type"] for item in output.idea_blocks] == ["owner", "status", "technology_version"]
    assert all(
        primary_claim_evidence(item).source_quote == "| Billing API | Platform Team | active | PostgreSQL | 16 |"
        for item in output.idea_blocks
    )


def test_deterministic_markdown_extracts_controlled_relationship_phrases() -> None:
    elements = [
        type("Element", (), {"id": "elem_rel", "kind": "paragraph", "text": "Order API belongs to Commerce Domain."})()
    ]

    block = DeterministicMarkdownExtractor().extract(elements=elements).idea_blocks[0]

    assert block.relationships[0].relation_type == "part_of"


def test_deterministic_markdown_extracts_table_and_diagram_relationship_labels() -> None:
    elements = [
        type(
            "Element",
            (),
            {
                "id": "elem_table",
                "kind": "table",
                "text": "| Source | Target | Relation |\n| --- | --- | --- |\n| API | Domain | belongs_to |",
            },
        )(),
        type(
            "Element",
            (),
            {
                "id": "elem_diagram",
                "kind": "code",
                "text": 'api = container "API"\nadr = component "ADR-001"\napi -> adr "decided_by"',
            },
        )(),
    ]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    assert output.idea_blocks[0].relationships[0].relation_type == "part_of"
    diagram_blocks = output.idea_blocks[1:]
    entity_blocks = [block for block in diagram_blocks if block.type == "c4_entity"]
    review_blocks = [block for block in diagram_blocks if block.unmapped]
    assert len(entity_blocks) == 2
    assert all(len(block.entities) == 1 and not block.relationships for block in entity_blocks)
    assert len(review_blocks) == 1
    assert review_blocks[0].unmapped[0].reason == "unknown_relationship_type"


def test_deterministic_markdown_splits_diagram_relationships_into_atomic_blocks() -> None:
    elements = [
        type(
            "Element",
            (),
            {
                "id": "elem_diagram",
                "kind": "code",
                "text": 'api = container "API"\nworker = container "Worker"\napi -> worker "depends_on"',
            },
        )()
    ]

    blocks = DeterministicMarkdownExtractor().extract(elements=elements).idea_blocks

    entity_blocks = [block for block in blocks if block.type == "c4_entity"]
    relationship_blocks = [block for block in blocks if block.type == "c4_relationship"]
    assert len(entity_blocks) == 2
    assert len(relationship_blocks) == 1
    relationship_block = relationship_blocks[0]
    assert len(relationship_block.entities) == 2
    assert len(relationship_block.relationships) == 1
    assert relationship_block.relationships[0].relation_type == "depends_on"
    assert all(len(block.relationships) == 0 for block in entity_blocks)


def test_extraction_persistence_stores_structured_claim_metadata(tmp_path: Path) -> None:
    session_factory = _session_factory()
    service = _service(tmp_path)
    with session_factory() as session:
        result = upload_scoped_source(
            service,
            session,
            filename="owners.md",
            media_type="text/markdown",
            data=b"# Owners\n\nBilling API is owned by Platform Team.\n",
        )
        element = session.scalar(select(SourceElement).where(SourceElement.kind == "paragraph"))
        assert element is not None
        ExtractionPersistenceService().persist_output(
            session,
            {
                "idea_blocks": [
                    {
                        "statement": "Billing API is owned by Platform Team.",
                        "type": "structured_table_row",
                        "confidence": "extracted",
                        "evidence": [
                            {"source_element_id": element.id, "source_quote": "Billing API is owned by Platform Team."}
                        ],
                        "extraction_method": "deterministic_table",
                        "metadata": {
                            "claim_type": "owner",
                            "subject": "Billing API",
                            "attribute": "owner",
                            "value": "Platform Team",
                            "verification_policy": "normalized_table_assertion",
                        },
                    }
                ]
            },
            source_document_id=result.source_document.id,
        )
        block = session.scalar(select(IdeaBlock))
        assert block is not None
        revision = session.scalar(select(IdeaBlockRevision).where(IdeaBlockRevision.idea_block_id == block.id))
        assert revision is not None
        assert revision.metadata_["claim_type"] == "owner"
        assert revision.metadata_["subject"] == "Billing API"


def test_table_claims_persist_with_literal_source_row_quotes(tmp_path: Path) -> None:
    session_factory = _session_factory()
    service = _service(tmp_path)
    with session_factory() as session:
        result = upload_scoped_source(
            service,
            session,
            filename="owners.md",
            media_type="text/markdown",
            data=(
                b"# Owners\n\n"
                b"| System | Owner | Status |\n"
                b"| --- | --- | --- |\n"
                b"| Billing API | Platform Team | active |\n"
            ),
        )
        elements = session.scalars(select(SourceElement)).all()
        output = DeterministicMarkdownExtractor().extract(elements=elements)
        report = ExtractionPersistenceService().persist_output(
            session, output, source_document_id=result.source_document.id
        )
        refreshed = extraction_quality_report(session, source_document_id=result.source_document.id)
        block = session.scalar(select(IdeaBlock))
        evidence = session.scalar(select(ClaimEvidence).where(ClaimEvidence.idea_block_id == block.id))

    assert report.idea_block_count == 2
    assert report.failed_element_count == 0
    assert refreshed.failed_element_count == 0
    assert block is not None
    assert evidence is not None
    assert evidence.source_quote == "| Billing API | Platform Team | active |"
