from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.catalog_helpers import idea_block_dict
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord, GovernanceRecordRevision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import Domain


def test_idea_block_serializer_omits_stale_source_and_governance_evidence() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    request = SimpleNamespace(
        state=SimpleNamespace(
            permissions=PermissionSnapshot(
                principal_id="reader",
                domain_permissions={"domain-a": frozenset({"read"})},
                category_permissions={"Design": frozenset({"read"}), "governance:risk": frozenset({"read"})},
                sensitivity_permissions={"internal": frozenset({"read"})},
            )
        )
    )

    with Session(engine) as session:
        session.add_all(
            (
                Domain(id="domain-a", name="Domain A"),
                Entity(
                    id="category-design",
                    preferred_label="Design",
                    normalized_label="design",
                    ontology_type="category",
                    lifecycle_status="active",
                ),
                SourceDocument(
                    id="source-1",
                    title="Architecture source",
                    object_key="source-1",
                    media_type="text/plain",
                    sha256="a" * 64,
                    domain_id="domain-a",
                    category_id="category-design",
                    sensitivity="internal",
                ),
            )
        )
        session.flush()
        session.add_all(
            (
                SourceDocumentVersion(
                    id="version-current", source_document_id="source-1", version_sha256="b" * 64, status="active"
                ),
                SourceDocumentVersion(
                    id="version-old", source_document_id="source-1", version_sha256="c" * 64, status="superseded"
                ),
                SourceElement(
                    id="element-current",
                    source_version_id="version-current",
                    kind="paragraph",
                    ordinal=1,
                    text="Current source quote",
                ),
                SourceElement(
                    id="element-old",
                    source_version_id="version-old",
                    kind="paragraph",
                    ordinal=1,
                    text="Old source quote",
                ),
            )
        )
        block = IdeaBlock(id="block-1", domain_id="domain-a")
        revision = IdeaBlockRevision(
            id="revision-current",
            idea_block_id=block.id,
            revision_number=1,
            statement="Current statement",
            normalized_statement="current statement",
            type="claim",
            confidence="verified",
            publication_status="verified",
            semantic_key="current-statement",
        )
        session.add_all((block, revision))
        session.flush()
        block.current_revision_id = revision.id
        record = GovernanceRecord(
            id="risk-1",
            business_key="RISK-1",
            record_type="risk",
            title="Risk",
            domain_id="domain-a",
            lifecycle_status="identified",
        )
        old_record_revision = GovernanceRecordRevision(
            id="risk-revision-old",
            governance_record_id=record.id,
            revision_number=1,
            snapshot={},
            transition_reason="created",
            actor="tester",
            process="test",
        )
        current_record_revision = GovernanceRecordRevision(
            id="risk-revision-current",
            governance_record_id=record.id,
            revision_number=2,
            snapshot={},
            transition_reason="updated",
            actor="tester",
            process="test",
        )
        session.add_all((record, old_record_revision, current_record_revision))
        session.flush()
        record.current_revision_id = current_record_revision.id
        session.add_all(
            (
                ClaimEvidence(
                    id="evidence-current-source",
                    idea_block_id=block.id,
                    idea_block_revision_id=revision.id,
                    source_element_id="element-current",
                    source_quote="Current source quote",
                    confidence="verified",
                    extraction_method="test",
                ),
                ClaimEvidence(
                    id="evidence-old-source",
                    idea_block_id=block.id,
                    idea_block_revision_id=revision.id,
                    source_element_id="element-old",
                    source_quote="Old source quote",
                    confidence="verified",
                    extraction_method="test",
                ),
                ClaimEvidence(
                    id="evidence-old-governance",
                    idea_block_id=block.id,
                    idea_block_revision_id=revision.id,
                    governance_record_revision_id=old_record_revision.id,
                    source_quote="Old governance quote",
                    confidence="verified",
                    extraction_method="test",
                ),
                ClaimEvidence(
                    id="evidence-current-governance",
                    idea_block_id=block.id,
                    idea_block_revision_id=revision.id,
                    governance_record_revision_id=current_record_revision.id,
                    source_quote="Current governance quote",
                    confidence="verified",
                    extraction_method="test",
                ),
            )
        )
        session.commit()

        payload = idea_block_dict(session, request, block)

    assert {item["id"] for item in payload["evidence"]} == {"evidence-current-source", "evidence-current-governance"}
    assert {item["source_quote"] for item in payload["evidence"]} == {
        "Current source quote",
        "Current governance quote",
    }
