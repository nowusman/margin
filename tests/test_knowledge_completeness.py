from __future__ import annotations

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.extraction import ExtractionPersistenceService
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage, UnmappedExtraction
from margin_db.user_models import Domain
from tests.helpers.catalog import catalog_entity


def test_completeness_schema_separates_claims_from_source_evidence() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    schema = inspect(engine)

    assert {
        "source_pages",
        "source_elements",
        "source_element_outcomes",
        "claim_evidence",
        "unmapped_extractions",
    }.issubset(schema.get_table_names())
    idea_block_columns = {column["name"] for column in schema.get_columns("idea_blocks")}
    revision_columns = {column["name"] for column in schema.get_columns("idea_block_revisions")}
    assert {"domain_id", "current_revision_id"}.issubset(idea_block_columns)
    assert {"polarity", "qualifiers", "valid_from", "valid_to"}.issubset(revision_columns)
    assert {"source_page_id", "source_element_id", "source_quote"}.isdisjoint(idea_block_columns)


def test_compiled_articles_are_absent_from_canonical_metadata() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    assert "compiled_articles" not in inspect(engine).get_table_names()


def test_claim_deduplication_is_scoped_and_evidence_is_multi_span() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_source(session, domain_id="OSS", suffix="a")
        _seed_source(session, domain_id="OSS", suffix="b")
        _seed_source(session, domain_id="BSS", suffix="c")
        _seed_source(session, domain_id="OSS", suffix="d")
        service = ExtractionPersistenceService()
        service.persist_output(session, _output("a"), source_document_id="src_a")
        service.persist_output(session, _output("b"), source_document_id="src_b")
        service.persist_output(session, _output("c"), source_document_id="src_c")
        service.persist_output(session, _output("d", polarity="refutes"), source_document_id="src_d")

        blocks = list(session.scalars(select(IdeaBlock)).all())
        oss_support = next(
            block for block in blocks if block.domain_id == "OSS" and block.current_revision.polarity == "supports"
        )
        evidence = list(
            session.scalars(select(ClaimEvidence).where(ClaimEvidence.idea_block_id == oss_support.id)).all()
        )

        assert len(blocks) == 3
        assert {item.source_element_id for item in evidence} == {"elem_a", "elem_b"}


def test_unmapped_extractions_are_durable_and_reviewable() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_source(session, domain_id="OSS", suffix="a")
        service = ExtractionPersistenceService()
        service.persist_output(session, _output("a", unmapped=True), source_document_id="src_a")

        row = session.scalar(select(UnmappedExtraction))
        assert row is not None
        assert row.kind == "relationship"
        assert row.review_task_id is not None


def _seed_source(session: Session, *, domain_id: str, suffix: str) -> None:
    if session.get(Domain, domain_id) is None:
        session.add(Domain(id=domain_id, name=domain_id))
    if session.get(Entity, "category_design") is None:
        session.add(
            catalog_entity(entity_id="category_design", preferred_label="Design Document", ontology_type="category")
        )
    session.add(
        SourceDocument(
            id=f"src_{suffix}",
            title=f"Source {suffix}",
            object_key=f"object-{suffix}",
            media_type="text/markdown",
            sha256=suffix * 64,
            domain_id=domain_id,
            category_id="category_design",
        )
    )
    session.add(
        SourceDocumentVersion(
            id=f"version_{suffix}",
            source_document_id=f"src_{suffix}",
            version_sha256=suffix * 64,
            converter_name="test",
            converter_version="1",
            status="active",
        )
    )
    session.add(
        SourcePage(
            id=f"page_{suffix}",
            source_version_id=f"version_{suffix}",
            page_number=1,
            content_checksum=suffix * 64,
            classification="text_extracted",
        )
    )
    session.add(
        SourceElement(
            id=f"elem_{suffix}",
            source_version_id=f"version_{suffix}",
            source_page_id=f"page_{suffix}",
            kind="paragraph",
            ordinal=0,
            text="Margin uses PostgreSQL.",
            checksum=suffix * 64,
        )
    )
    session.flush()


def _output(suffix: str, *, polarity: str = "supports", unmapped: bool = False) -> dict[str, object]:
    return {
        "idea_blocks": [
            {
                "statement": "Margin uses PostgreSQL.",
                "type": "technology_choice",
                "confidence": "extracted",
                "polarity": polarity,
                "extraction_method": "manual",
                "metadata": {"verification_policy": "manual_authorized"},
                "evidence": [{"source_element_id": f"elem_{suffix}", "source_quote": "Margin uses PostgreSQL."}],
                "unmapped": (
                    [
                        {
                            "kind": "relationship",
                            "reason": "unknown_relationship_type",
                            "raw_payload": {"relation_type": "runs_near"},
                        }
                    ]
                    if unmapped
                    else []
                ),
            }
        ]
    }
