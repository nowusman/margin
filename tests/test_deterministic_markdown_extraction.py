from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from margin_db.object_storage import FilesystemObjectStore
from margin_db.source_ingestion import SourceIndex, SourceIngestionService
from margin_db.source_models import SourceElement
from margin_schema.extraction import primary_claim_evidence
from tests.helpers.converters import TestTextDocumentConverter
from tests.helpers.source_scope import seed_source_scope, upload_scoped_source
from workers.extract.markdown import DeterministicMarkdownExtractor
from workers.extract.markdown_types import ElementLike


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _service(tmp_path: Path) -> SourceIngestionService:
    return SourceIngestionService(
        FilesystemObjectStore(tmp_path / "objects"), converter=TestTextDocumentConverter(), source_index=SourceIndex()
    )


def test_deterministic_markdown_extracts_table_rows_and_relationships(tmp_path: Path, session_factory) -> None:
    service = _service(tmp_path)
    data = Path("tests/fixtures/source_ingestion/architecture.md").read_bytes()

    with session_factory() as session:
        result = upload_scoped_source(
            service, session, filename="architecture.md", media_type="text/markdown", data=data
        )
        version = service.latest_version_for_source(session, result.source_document.id)
        assert version is not None
        elements = session.scalars(
            select(SourceElement).where(SourceElement.source_version_id == version.id).order_by(SourceElement.ordinal)
        ).all()
        output = DeterministicMarkdownExtractor().extract(elements=elements)

    statements = [block.statement for block in output.idea_blocks]
    assert any("Order API is responsible for Accept customer orders" in item for item in statements)
    assert any(block.relationships for block in output.idea_blocks)


def test_an_ownership_table_row_produces_an_evidenced_ownership_relationship() -> None:
    """An ownership table is how the organisation states who owns what."""

    elements = [
        ElementLike(
            id="elem_table",
            kind="table",
            text_content=(
                "| System            | Owner               | Status |\n"
                "| ----------------- | ------------------- | ------ |\n"
                "| Customer Platform | Platform Team       | active |\n"
            ),
        )
    ]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    ownership = [
        block
        for block in output.idea_blocks
        if any(relationship.relation_type == "owned_by" for relationship in block.relationships)
    ]
    assert len(ownership) == 1
    block = ownership[0]
    relationship = block.relationships[0]
    assert relationship.source == "Customer Platform"
    assert relationship.target == "Platform Team"
    assert relationship.evidence
    assert relationship.evidence[0].source_quote
    kinds = {entity.name: entity.kind for entity in block.entities}
    assert kinds["Platform Team"] == "team"


def test_a_relationship_names_the_record_it_connects_and_not_the_rest_of_the_sentence() -> None:
    """A qualifier after the record is a qualifier, not part of its name."""

    elements = [
        ElementLike(
            id="elem_dependency",
            kind="paragraph",
            text_content="Customer Platform depends on PostgreSQL for operational customer records.",
        )
    ]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    relationships = [
        relationship
        for block in output.idea_blocks
        for relationship in block.relationships
        if relationship.relation_type == "depends_on"
    ]
    assert len(relationships) == 1
    assert relationships[0].source == "Customer Platform"
    assert relationships[0].target == "PostgreSQL"


def test_an_ownership_statement_in_the_active_voice_yields_an_ownership_relationship() -> None:
    elements = [ElementLike(id="elem_ownership", kind="paragraph", text_content="Platform Team owns Billing API.")]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    relationships = [
        relationship
        for block in output.idea_blocks
        for relationship in block.relationships
        if relationship.relation_type == "owns"
    ]
    assert len(relationships) == 1
    assert relationships[0].source == "Platform Team"
    assert relationships[0].target == "Billing API"


def test_deterministic_extractors_cover_structurizr_mermaid_and_plantuml_references() -> None:
    elements = [
        ElementLike(
            id="elem_mermaid",
            kind="code_block",
            text_content="flowchart LR\n  Web --> API",
            structural_path="Diagram",
            heading="Diagram",
        ),
        ElementLike(
            id="elem_puml",
            kind="code_block",
            text_content="@startuml\nService -> Database\n@enduml",
            structural_path="Diagram",
            heading="Diagram",
        ),
        ElementLike(
            id="elem_dsl",
            kind="code_block",
            text_content='softwareSystem "Billing System"\ncontainer "Billing API"',
            structural_path="Diagram",
            heading="Diagram",
        ),
    ]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    entity_blocks = [block for block in output.idea_blocks if block.type == "c4_entity"]
    relationship_blocks = [block for block in output.idea_blocks if block.type == "c4_relationship"]
    assert len(output.idea_blocks) == 8
    assert len(entity_blocks) == 6
    assert len(relationship_blocks) == 2
    assert all(len(block.entities) == 1 and not block.relationships for block in entity_blocks)
    assert all(len(block.entities) == 2 and len(block.relationships) == 1 for block in relationship_blocks)
    assert any(entity.name == "Billing System" for block in output.idea_blocks for entity in block.entities)


def test_deterministic_markdown_extracts_frontmatter_and_named_structurizr_semantics() -> None:
    elements = [
        ElementLike(
            id="elem_frontmatter",
            kind="paragraph",
            text_content=(
                "---\n"
                "title: Use Kafka\n"
                "status: accepted\n"
                "owner: Platform Team\n"
                "tags: adr, kafka\n"
                "---\n\n"
                "## Decision\n"
                "Use Kafka for eventing."
            ),
            structural_path="Architecture Decision Record",
            heading="ADR",
        ),
        ElementLike(
            id="elem_structurizr",
            kind="code_block",
            text_content=(
                "workspace {\n"
                "model {\n"
                'orders = softwareSystem "Order Platform"\n'
                'api = container "Order API"\n'
                'orders -> api "contains"\n'
                "}\n"
                "}"
            ),
            structural_path="Architecture Decision Record",
            heading="ADR",
        ),
    ]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    statements = [block.statement for block in output.idea_blocks]
    assert "Document title is Use Kafka." in statements
    assert "ADR status is accepted." in statements
    assert any(entity.name == "Order Platform" for block in output.idea_blocks for entity in block.entities)
    assert any(
        relationship.source == "Order Platform" and relationship.target == "Order API"
        for block in output.idea_blocks
        for relationship in block.relationships
    )


def test_deterministic_markdown_groups_architecture_principle_subfields() -> None:
    elements = [
        ElementLike(
            id="elem_statement",
            kind="paragraph",
            text_content="Statement: Systems should use approved managed platform services.",
            structural_path="Architecture Principles / AP-01 Prefer managed services",
            heading="AP-01 Prefer managed services",
        ),
        ElementLike(
            id="elem_rationale",
            kind="paragraph",
            text_content="Rationale: Managed services reduce operational burden.",
            structural_path="Architecture Principles / AP-01 Prefer managed services",
            heading="AP-01 Prefer managed services",
        ),
        ElementLike(
            id="elem_implications",
            kind="paragraph",
            text_content="Implications: Teams must document exceptions.",
            structural_path="Architecture Principles / AP-01 Prefer managed services",
            heading="AP-01 Prefer managed services",
        ),
    ]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    assert len(output.idea_blocks) == 1
    block = output.idea_blocks[0]
    assert block.type == "architecture_principle"
    assert primary_claim_evidence(block).source_element_id == "elem_statement"
    assert primary_claim_evidence(block).source_quote == (
        "Statement: Systems should use approved managed platform services."
    )
    assert "managed platform services" in block.statement
    assert block.metadata["record_type"] == "architecture_principle"
    assert block.metadata["rationale"] == "Managed services reduce operational burden."
    assert block.metadata["implications"] == "Teams must document exceptions."


def test_deterministic_markdown_groups_multiline_principle_fields() -> None:
    elements = [
        ElementLike(
            id="elem_record",
            kind="paragraph",
            text_content=(
                "Statement: Services must deny access by default.\n"
                "Rational: Default denial reduces accidental exposure.\n"
                "Implications: Teams must document access grants."
            ),
            structural_path="Architecture Principles / AP-02 Secure by default",
            heading="AP-02 Secure by default",
        )
    ]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    assert len(output.idea_blocks) == 1
    block = output.idea_blocks[0]
    assert block.statement == "AP-02 Secure by default principle states Services must deny access by default."
    assert primary_claim_evidence(block).source_quote == "Statement: Services must deny access by default."
    assert block.metadata["rationale"] == "Default denial reduces accidental exposure."
    assert block.metadata["implications"] == "Teams must document access grants."


def test_deterministic_markdown_reads_the_section_path_from_the_element_metadata() -> None:
    """The indexer records the section path in metadata; the extractor must read it."""

    elements = [
        SourceElement(
            id="elem_decision",
            kind="paragraph",
            text="Use PostgreSQL as the operational system of record.",
            metadata_={"structural_path": "ADR-0001: Operational Records/Decision"},
        ),
        SourceElement(
            id="elem_consequences",
            kind="paragraph",
            text="Git exports are generated mirrors, not authoritative runtime state.",
            metadata_={"structural_path": "ADR-0001: Operational Records/Consequences"},
        ),
    ]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    assert len(output.idea_blocks) == 1
    block = output.idea_blocks[0]
    assert block.type == "decision"
    assert "Use PostgreSQL as the operational system of record" in block.statement


def test_deterministic_markdown_does_not_emit_support_only_principle_fragments() -> None:
    elements = [
        ElementLike(
            id="elem_rationale",
            kind="paragraph",
            text_content="Rationale: This explains why a missing statement matters.",
            structural_path="Architecture Principles / AP-03",
            heading="AP-03",
        )
    ]

    output = DeterministicMarkdownExtractor().extract(elements=elements)

    assert output.idea_blocks == []
