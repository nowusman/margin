from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord, GovernanceRecordRelation
from margin_db.idea_block_resolution import upsert_idea_block_relation
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision
from margin_db.relationship_consistency import run_relationship_consistency_lint
from margin_db.relationship_evidence import upsert_relationship_evidence
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_db.user_models import Domain


def test_relationship_health_reports_verified_edge_without_provenance() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Domain(id="domain-a", name="Domain A"))
        session.add_all(
            (
                _entity("entity-a", "A"),
                _entity("entity-b", "B"),
                EntityRelationship(
                    id="relationship-a-b",
                    source_entity_id="entity-a",
                    target_entity_id="entity-b",
                    relation_type="depends_on",
                    confidence="verified",
                    status="verified",
                ),
            )
        )
        session.commit()

        findings = run_relationship_consistency_lint(session)

    assert {(finding.rule_id, finding.target_id) for finding in findings} == {
        ("relationship_missing_provenance", "relationship-a-b")
    }


def test_relationship_health_accepts_valid_derived_inverse_provenance() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Domain(id="domain-a", name="Domain A"))
        session.add_all(
            (
                _entity("entity-a", "A"),
                _entity("entity-b", "B"),
                EntityRelationship(
                    id="relationship-contains",
                    source_entity_id="entity-a",
                    target_entity_id="entity-b",
                    relation_type="contains",
                    confidence="verified",
                    status="verified",
                ),
                EntityRelationship(
                    id="relationship-part-of",
                    source_entity_id="entity-b",
                    target_entity_id="entity-a",
                    relation_type="part_of",
                    confidence="verified",
                    status="verified",
                    metadata_={"derived": True, "inverse_of": "relationship-contains"},
                ),
                RelationshipProvenance(
                    id="provenance-contains",
                    relationship_id="relationship-contains",
                    idea_block_id="idea-block-1",
                    source_element_id="source-element-1",
                    confidence="verified",
                    extraction_method="test",
                    source_quote="A contains B.",
                    status="verified",
                ),
            )
        )
        session.commit()

        findings = run_relationship_consistency_lint(session)

    assert ("relationship_missing_provenance", "relationship-part-of") not in {
        (finding.rule_id, finding.target_id) for finding in findings
    }


def test_relationship_health_rejects_claim_evidence_from_stale_revision() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Domain(id="domain-a", name="Domain A"))
        session.add_all((_entity("entity-a", "A"), _entity("entity-b", "B")))
        block = IdeaBlock(id="idea-block-stale", domain_id="domain-a")
        stale_revision = IdeaBlockRevision(
            id="idea-block-revision-stale",
            idea_block_id=block.id,
            revision_number=1,
            statement="A depends on B.",
            normalized_statement="a depends on b",
            type="claim",
            confidence="verified",
            publication_status="stale",
            semantic_key="stale-a-depends-on-b",
        )
        current_revision = IdeaBlockRevision(
            id="idea-block-revision-current",
            idea_block_id=block.id,
            revision_number=2,
            statement="A depends on B.",
            normalized_statement="a depends on b",
            type="claim",
            confidence="verified",
            publication_status="verified",
            semantic_key="current-a-depends-on-b",
        )
        session.add_all((block, stale_revision, current_revision))
        session.flush()
        block.current_revision_id = current_revision.id
        session.add_all(
            (
                SourceDocument(
                    id="source-document-stale",
                    title="Architecture source",
                    object_key="source-stale.md",
                    media_type="text/markdown",
                    sha256="a" * 64,
                    size_bytes=1,
                    domain_id="domain-a",
                    category_id="entity-a",
                    status="knowledge_ready",
                ),
                SourceDocumentVersion(
                    id="source-version-stale",
                    source_document_id="source-document-stale",
                    version_sha256="b" * 64,
                    status="active",
                ),
            )
        )
        session.flush()
        session.add_all(
            (
                SourcePage(
                    id="source-page-stale",
                    source_version_id="source-version-stale",
                    page_number=1,
                    content_checksum="c" * 64,
                    classification="text_extracted",
                ),
                SourceElement(
                    id="source-element-stale",
                    source_version_id="source-version-stale",
                    source_page_id="source-page-stale",
                    kind="paragraph",
                    ordinal=0,
                    text="A depends on B.",
                ),
                ClaimEvidence(
                    id="claim-evidence-stale",
                    idea_block_id=block.id,
                    idea_block_revision_id=stale_revision.id,
                    source_element_id="source-element-stale",
                    source_quote="A depends on B.",
                    confidence="verified",
                    extraction_method="test",
                    status="active",
                ),
                EntityRelationship(
                    id="relationship-stale-evidence",
                    source_entity_id="entity-a",
                    target_entity_id="entity-b",
                    relation_type="depends_on",
                    confidence="verified",
                    status="verified",
                ),
                RelationshipProvenance(
                    id="provenance-stale-evidence",
                    relationship_id="relationship-stale-evidence",
                    idea_block_id=block.id,
                    source_element_id="source-element-stale",
                    confidence="verified",
                    extraction_method="test",
                    source_quote="A depends on B.",
                    status="verified",
                ),
            )
        )
        session.commit()

        findings = run_relationship_consistency_lint(session)

    assert any(
        finding.rule_id == "relationship_missing_provenance"
        and finding.target_id == "provenance-stale-evidence"
        and "exact ClaimEvidence" in finding.message
        for finding in findings
    )


def test_relationship_writer_rejects_stale_claim_evidence() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Domain(id="domain-a", name="Domain A"))
        session.add_all((_entity("entity-a", "A"), _entity("entity-b", "B")))
        block = IdeaBlock(id="idea-block-writer", domain_id="domain-a")
        stale_revision = IdeaBlockRevision(
            id="idea-block-writer-r1",
            idea_block_id=block.id,
            revision_number=1,
            statement="A depends on B.",
            normalized_statement="a depends on b",
            type="claim",
            confidence="verified",
            publication_status="stale",
            semantic_key="writer-stale-a-depends-on-b",
        )
        current_revision = IdeaBlockRevision(
            id="idea-block-writer-r2",
            idea_block_id=block.id,
            revision_number=2,
            statement="A depends on B.",
            normalized_statement="a depends on b",
            type="claim",
            confidence="verified",
            publication_status="verified",
            semantic_key="writer-current-a-depends-on-b",
        )
        session.add_all((block, stale_revision, current_revision))
        session.flush()
        block.current_revision_id = current_revision.id
        session.add_all(
            (
                SourceDocument(
                    id="source-document-writer",
                    title="Architecture source",
                    object_key="source-writer.md",
                    media_type="text/markdown",
                    sha256="d" * 64,
                    size_bytes=1,
                    domain_id="domain-a",
                    category_id="entity-a",
                    status="knowledge_ready",
                ),
                SourceDocumentVersion(
                    id="source-version-writer",
                    source_document_id="source-document-writer",
                    version_sha256="e" * 64,
                    status="active",
                ),
            )
        )
        session.flush()
        session.add(
            SourcePage(
                id="source-page-writer",
                source_version_id="source-version-writer",
                page_number=1,
                content_checksum="f" * 64,
                classification="text_extracted",
            )
        )
        session.add(
            SourceElement(
                id="source-element-writer",
                source_version_id="source-version-writer",
                source_page_id="source-page-writer",
                kind="paragraph",
                ordinal=0,
                text="A depends on B.",
            )
        )
        session.add(
            ClaimEvidence(
                id="claim-evidence-writer-stale",
                idea_block_id=block.id,
                idea_block_revision_id=stale_revision.id,
                source_element_id="source-element-writer",
                source_quote="A depends on B.",
                confidence="verified",
                extraction_method="test",
                status="active",
            )
        )
        session.flush()

        with pytest.raises(ValueError, match="active exact ClaimEvidence"):
            upsert_relationship_evidence(
                session,
                source_entity_id="entity-a",
                target_entity_id="entity-b",
                relation_type="depends_on",
                confidence="verified",
                status="verified",
                idea_block_id=block.id,
                source_element_id="source-element-writer",
                source_quote="A depends on B.",
            )

        block.archived_at = datetime.now(UTC)
        with pytest.raises(ValueError, match="active IdeaBlock"):
            upsert_relationship_evidence(
                session,
                source_entity_id="entity-a",
                target_entity_id="entity-b",
                relation_type="depends_on",
                confidence="verified",
                status="verified",
                idea_block_id=block.id,
                source_element_id="source-element-writer",
                source_quote="A depends on B.",
            )


def test_relationship_health_uses_governance_registry_for_record_relations() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Domain(id="domain-a", name="Domain A"))
        session.add_all(
            (
                GovernanceRecord(
                    id="adr-001",
                    business_key="ADR-001",
                    record_type="adr",
                    title="Architecture decision",
                    domain_id="domain-a",
                    lifecycle_status="accepted",
                    attributes={},
                ),
                GovernanceRecord(
                    id="risk-001",
                    business_key="RISK-001",
                    record_type="risk",
                    title="Architecture risk",
                    domain_id="domain-a",
                    lifecycle_status="identified",
                    attributes={},
                ),
                GovernanceRecord(
                    id="adr-002",
                    business_key="ADR-002",
                    record_type="adr",
                    title="Another decision",
                    domain_id="domain-a",
                    lifecycle_status="accepted",
                    attributes={},
                ),
                GovernanceRecordRelation(
                    id="governance-mitigates",
                    source_record_id="adr-001",
                    target_record_id="risk-001",
                    relation_type="mitigates",
                    status="verified",
                    provenance={"reason": "reviewed"},
                ),
                GovernanceRecordRelation(
                    id="governance-incurs-invalid",
                    source_record_id="risk-001",
                    target_record_id="adr-002",
                    relation_type="incurs",
                    status="verified",
                    provenance={"reason": "reviewed"},
                ),
            )
        )
        session.commit()

        findings = run_relationship_consistency_lint(session)

    finding_keys = {(finding.rule_id, finding.target_id) for finding in findings}
    assert ("governance_relation_invalid_ontology_direction", "governance-incurs-invalid") in finding_keys
    assert not any(target_id == "governance-mitigates" for _rule_id, target_id in finding_keys)


def test_idea_block_relation_writer_enforces_endpoint_scope_and_provenance() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            (
                Domain(id="domain-a", name="Domain A"),
                Domain(id="domain-b", name="Domain B"),
                IdeaBlock(id="idea-a", domain_id="domain-a"),
                IdeaBlock(id="idea-b", domain_id="domain-a"),
                IdeaBlock(id="idea-c", domain_id="domain-b"),
            )
        )
        session.flush()

        with pytest.raises(ValueError, match="endpoints must exist"):
            upsert_idea_block_relation(
                session, source_idea_block_id="idea-a", target_idea_block_id="missing", relation_type="contradicts"
            )
        with pytest.raises(ValueError, match="cannot cross domains"):
            upsert_idea_block_relation(
                session, source_idea_block_id="idea-a", target_idea_block_id="idea-c", relation_type="contradicts"
            )
        with pytest.raises(ValueError, match="require decision provenance"):
            upsert_idea_block_relation(
                session,
                source_idea_block_id="idea-a",
                target_idea_block_id="idea-b",
                relation_type="contradicts",
                status="verified",
            )


def _entity(identifier: str, label: str) -> Entity:
    return Entity(
        id=identifier,
        preferred_label=label,
        normalized_label=label.lower(),
        ontology_type="service",
        domain_id="domain-a",
        lifecycle_status="active",
    )
