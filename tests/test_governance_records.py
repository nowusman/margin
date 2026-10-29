from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import margin_db  # noqa: F401
from app.authz import permitted_source_element_ids
from margin_auth.permissions import PermissionSnapshot
from margin_db.audit_models import AuditEvent
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.governance import detect_contradiction_candidates, governance_consistency_findings
from margin_db.governance_models import GovernanceRecord, GovernanceRecordIdeaBlock, GovernanceRecordRelation
from margin_db.governance_queries import governance_record_details, governance_record_source_element_ids
from margin_db.governance_records import add_record_relation, create_record
from margin_db.governance_transitions import (
    accept_risk,
    close_risk,
    complete_architecture_backlog,
    create_architecture_backlog,
    renew_waiver,
    resolve_technical_debt,
    transition_record,
)
from margin_db.governance_types import GovernanceError, validate_dates, validate_record_fields
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRelation, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.review_bundles import build_review_bundle, decide_review_bundle
from margin_db.review_models import ReviewTask
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_db.user_models import Domain


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(Domain(id="domain-a", name="Domain A"))
    session.commit()
    return session


def test_adr_history_is_immutable_and_transition_is_explicit() -> None:
    session = _session()
    try:
        record = create_record(
            session,
            business_key="ADR-001",
            record_type="adr",
            title="Use the canonical record",
            domain_id="domain-a",
            attributes={
                "context": "Governance needs a single lifecycle.",
                "decision": "Use GovernanceRecord.",
                "rationale": "It prevents parallel detail tables.",
                "consequences": "Legacy records are removed.",
            },
            actor="architect",
        )
        initial_revision_id = record.current_revision_id
        transition_record(
            session,
            record=record,
            target_status="accepted",
            actor="architect",
            reason="Decision reviewed and approved.",
            expected_revision_id=record.current_revision_id,
        )
        session.commit()
        assert record.lifecycle_status == "accepted"
        assert record.current_revision_id != initial_revision_id
        assert len(record.current_revision.snapshot["attributes"]) == 4
    finally:
        session.close()


def test_architecture_backlog_requires_priority_and_target_date() -> None:
    session = _session()
    try:
        attributes = {"pending_correction": "Document the corrective work.", "manual_origin": "Review finding"}
        with pytest.raises(GovernanceError, match="priority"):
            create_record(
                session,
                business_key="AB-REQUIRED-001",
                record_type="architecture_backlog",
                title="Tracked correction",
                domain_id="domain-a",
                attributes=attributes,
                actor="architect",
                target_date=date(2026, 12, 31),
            )
        with pytest.raises(GovernanceError, match="target_date"):
            create_record(
                session,
                business_key="AB-REQUIRED-002",
                record_type="architecture_backlog",
                title="Tracked correction",
                domain_id="domain-a",
                attributes=attributes,
                actor="architect",
                priority="high",
            )
    finally:
        session.close()


def test_governance_dates_reject_datetime_values() -> None:
    with pytest.raises(GovernanceError, match="effective_date must be a date"):
        validate_dates(
            effective_date=datetime(2026, 9, 12, tzinfo=UTC), review_date=None, expiry_date=None, target_date=None
        )
    with pytest.raises(GovernanceError, match="architecture_backlog requires target_date"):
        validate_record_fields(
            "architecture_backlog",
            {"pending_correction": "Document the corrective work.", "manual_origin": "Review finding"},
            priority="high",
            target_date=datetime(2026, 12, 31, tzinfo=UTC),
        )


def test_backlog_requires_explicit_origin_and_completion_evidence() -> None:
    session = _session()
    try:
        risk = create_record(
            session,
            business_key="RISK-001",
            record_type="risk",
            title="Provider capacity risk",
            domain_id="domain-a",
            attributes={"likelihood": "medium", "impact": "high", "severity": "high", "treatment": "accept"},
            actor="architect",
        )
        accept_risk(
            session,
            record=risk,
            actor="architect",
            reason="Risk owner accepted the residual risk.",
            expected_revision_id=risk.current_revision_id,
        )
        backlog = create_architecture_backlog(
            session,
            origin=risk,
            business_key="AB-001",
            title="Reduce provider dependency",
            pending_correction="Introduce a provider abstraction.",
            actor="architect",
            reason="Risk acceptance requires tracked correction.",
            priority="high",
            target_date=date(2026, 12, 31),
            expected_origin_revision_id=risk.current_revision_id,
        )
        assert backlog.attributes["originating_governance_record_id"] == risk.id
        for status in ("planned", "in_progress"):
            transition_record(
                session,
                record=backlog,
                target_status=status,
                actor="architect",
                reason="Work progressed.",
                expected_revision_id=backlog.current_revision_id,
            )
        with pytest.raises(GovernanceError, match="completion_note"):
            complete_architecture_backlog(
                session,
                record=backlog,
                completion_note="",
                actor="architect",
                reason="Attempt completion without evidence.",
                expected_revision_id=backlog.current_revision_id,
            )
    finally:
        session.close()


def test_waiver_renewal_and_debt_resolution_are_explicit_audited_operations() -> None:
    session = _session()
    try:
        waiver = create_record(
            session,
            business_key="WAIVER-001",
            record_type="waiver",
            title="Temporary encryption exception",
            domain_id="domain-a",
            effective_date=date(2026, 9, 1),
            expiry_date=date(2026, 10, 1),
            attributes={
                "standard_or_control": "Encryption at rest",
                "scope": "One legacy integration",
                "approver": "Security architecture",
                "reason": "Vendor remediation is in progress",
                "compensating_control": "Restricted network access",
                "expiry_date": "2026-10-01",
            },
            actor="architect",
        )
        transition_record(
            session,
            record=waiver,
            target_status="approved",
            actor="architect",
            reason="Security architecture approved the waiver.",
            expected_revision_id=waiver.current_revision_id,
        )
        approved_revision_id = waiver.current_revision_id
        renew_waiver(
            session,
            record=waiver,
            expiry_date=date(2026, 11, 1),
            actor="architect",
            reason="Vendor remediation needs one additional month.",
            expected_revision_id=waiver.current_revision_id,
        )
        assert waiver.current_revision_id != approved_revision_id
        assert waiver.expiry_date == date(2026, 11, 1)
        assert waiver.attributes["expiry_date"] == "2026-11-01"
        with pytest.raises(GovernanceError, match="extend"):
            renew_waiver(
                session,
                record=waiver,
                expiry_date=date(2026, 11, 1),
                actor="architect",
                reason="This is not an extension.",
                expected_revision_id=waiver.current_revision_id,
            )

        debt = create_record(
            session,
            business_key="DEBT-001",
            record_type="technical_debt",
            title="Replace legacy database driver",
            domain_id="domain-a",
            attributes={
                "category": "platform",
                "remediation": "Use the supported database driver.",
                "effort": "medium",
                "interest_or_impact": "Release delivery is delayed.",
            },
            actor="architect",
        )
        transition_record(
            session,
            record=debt,
            target_status="planned",
            actor="architect",
            reason="Work planned.",
            expected_revision_id=debt.current_revision_id,
        )
        transition_record(
            session,
            record=debt,
            target_status="in_progress",
            actor="architect",
            reason="Work started.",
            expected_revision_id=debt.current_revision_id,
        )
        with pytest.raises(GovernanceError, match="resolution_note"):
            resolve_technical_debt(
                session,
                record=debt,
                resolution_note="",
                actor="architect",
                reason="Work completed.",
                expected_revision_id=debt.current_revision_id,
            )
        resolve_technical_debt(
            session,
            record=debt,
            resolution_note="The supported driver is deployed.",
            actor="architect",
            reason="Work completed and verified.",
            expected_revision_id=debt.current_revision_id,
        )
        session.flush()
        actions = set(
            session.scalars(select(AuditEvent.action).where(AuditEvent.target_id.in_((waiver.id, debt.id)))).all()
        )
        assert "governance.waiver.renewed" in actions
        assert "governance.record.transitioned" in actions
    finally:
        session.close()


def test_waiver_expiry_scalar_matches_typed_attribute() -> None:
    session = _session()
    try:
        attributes = {
            "standard_or_control": "Encryption at rest",
            "scope": "One legacy service",
            "approver": "Security architecture",
            "reason": "Remediation is in progress",
            "compensating_control": "Restricted network access",
            "expiry_date": "2026-10-01",
        }
        waiver = create_record(
            session,
            business_key="WAIVER-DERIVED-001",
            record_type="waiver",
            title="Derived expiry",
            domain_id="domain-a",
            attributes=attributes,
            actor="architect",
        )
        assert waiver.expiry_date == date(2026, 10, 1)
        with pytest.raises(GovernanceError, match="match the typed attribute"):
            create_record(
                session,
                business_key="WAIVER-MISMATCH-001",
                record_type="waiver",
                title="Mismatched expiry",
                domain_id="domain-a",
                expiry_date=date(2026, 11, 1),
                attributes=attributes,
                actor="architect",
            )
    finally:
        session.close()


def test_database_enforces_governance_attribute_and_waiver_expiry_shapes() -> None:
    session = _session()
    try:
        common = {
            "id": "waiver-db-invalid",
            "business_key": "WAIVER-DB-INVALID",
            "record_type": "waiver",
            "title": "Invalid waiver",
            "domain_id": "domain-a",
            "lifecycle_status": "proposed",
            "attributes": {
                "standard_or_control": "Encryption",
                "scope": "One service",
                "approver": "Security",
                "reason": "Temporary exception",
                "compensating_control": "Restricted network",
                "expiry_date": "2026-10-01",
            },
        }
        session.add(GovernanceRecord(**common))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

        common["id"] = "waiver-db-mismatch"
        common["business_key"] = "WAIVER-DB-MISMATCH"
        common["expiry_date"] = date(2026, 11, 1)
        session.add(GovernanceRecord(**common))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

        common["id"] = "waiver-db-invalid-calendar-date"
        common["business_key"] = "WAIVER-DB-INVALID-CALENDAR"
        common["expiry_date"] = date(2024, 3, 2)
        common["attributes"] = {**common["attributes"], "expiry_date": "2024-02-31"}
        session.add(GovernanceRecord(**common))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

        common["id"] = "governance-db-array"
        common["business_key"] = "GOVERNANCE-DB-ARRAY"
        common["record_type"] = "adr"
        common["lifecycle_status"] = "proposed"
        common["expiry_date"] = None
        common["attributes"] = []
        session.add(GovernanceRecord(**common))
        with pytest.raises(IntegrityError):
            session.flush()
    finally:
        session.close()


def test_backlog_origin_findings_require_ontology_and_provenance() -> None:
    session = _session()
    try:
        risk = create_record(
            session,
            business_key="RISK-PROVENANCE-001",
            record_type="risk",
            title="Provider capacity risk",
            domain_id="domain-a",
            attributes={"likelihood": "medium", "impact": "high", "severity": "high", "treatment": "accept"},
            actor="architect",
        )
        accept_risk(
            session,
            record=risk,
            actor="architect",
            reason="Risk accepted.",
            expected_revision_id=risk.current_revision_id,
        )
        backlog = create_architecture_backlog(
            session,
            origin=risk,
            business_key="AB-PROVENANCE-001",
            title="Track corrective work",
            pending_correction="Deliver the durable correction.",
            actor="architect",
            reason="Track the correction.",
            priority="high",
            target_date=date(2026, 12, 31),
            expected_origin_revision_id=risk.current_revision_id,
        )
        relation = session.scalar(
            select(GovernanceRecordRelation).where(
                GovernanceRecordRelation.source_record_id == backlog.id,
                GovernanceRecordRelation.target_record_id == risk.id,
                GovernanceRecordRelation.relation_type == "corrects",
            )
        )
        assert relation is not None
        relation.provenance = {}
        findings = governance_consistency_findings(session)
        assert any(
            finding.rule_id == "governance_backlog_missing_valid_origin" and "provenance" in finding.message
            for finding in findings
        )
    finally:
        session.close()


def test_special_lifecycle_operations_reject_generic_supersession_and_relation_writes() -> None:
    session = _session()
    try:
        adr = create_record(
            session,
            business_key="ADR-002",
            record_type="adr",
            title="Primary ADR",
            domain_id="domain-a",
            attributes={
                "context": "Context",
                "decision": "Decision",
                "rationale": "Rationale",
                "consequences": "Consequences",
            },
            actor="architect",
        )
        transition_record(
            session,
            record=adr,
            target_status="accepted",
            actor="architect",
            reason="Accepted.",
            expected_revision_id=adr.current_revision_id,
        )
        with pytest.raises(GovernanceError, match="explicit lifecycle operation"):
            transition_record(
                session,
                record=adr,
                target_status="superseded",
                actor="architect",
                reason="Invalid.",
                expected_revision_id=adr.current_revision_id,
            )

        successor = create_record(
            session,
            business_key="ADR-003",
            record_type="adr",
            title="Successor ADR",
            domain_id="domain-a",
            attributes={
                "context": "Context",
                "decision": "Decision",
                "rationale": "Rationale",
                "consequences": "Consequences",
            },
            actor="architect",
        )
        with pytest.raises(GovernanceError, match="explicit ADR supersession"):
            add_record_relation(session, source=successor, target=adr, relation_type="supersedes", actor="architect")

        risk = create_record(
            session,
            business_key="RISK-002",
            record_type="risk",
            title="Capacity risk",
            domain_id="domain-a",
            attributes={"likelihood": "low", "impact": "low", "severity": "low", "treatment": "accept"},
            actor="architect",
        )
        accept_risk(
            session,
            record=risk,
            actor="architect",
            reason="Residual risk accepted.",
            expected_revision_id=risk.current_revision_id,
        )
        close_risk(
            session,
            record=risk,
            actor="architect",
            reason="Risk retired.",
            expected_revision_id=risk.current_revision_id,
        )
        assert risk.lifecycle_status == "closed"

        backlog = create_architecture_backlog(
            session,
            origin=adr,
            business_key="AB-002",
            title="Correct the ADR follow-up",
            pending_correction="Make the corrective change.",
            actor="architect",
            reason="Track correction.",
            priority="medium",
            target_date=date(2026, 12, 31),
            expected_origin_revision_id=adr.current_revision_id,
        )
        transition_record(
            session,
            record=backlog,
            target_status="planned",
            actor="architect",
            reason="Planned.",
            expected_revision_id=backlog.current_revision_id,
        )
        transition_record(
            session,
            record=backlog,
            target_status="in_progress",
            actor="architect",
            reason="Started.",
            expected_revision_id=backlog.current_revision_id,
        )
        complete_architecture_backlog(
            session,
            record=backlog,
            completion_note="Corrective work shipped.",
            actor="architect",
            reason="Completion verified.",
            expected_revision_id=backlog.current_revision_id,
        )
        assert backlog.lifecycle_status == "completed"
    finally:
        session.close()


def test_governance_detail_hydrates_only_source_evidence_the_reader_can_access() -> None:
    session = _session()
    try:
        category = Entity(
            id="category-architecture",
            preferred_label="architecture",
            normalized_label="architecture",
            ontology_type="category",
            domain_id="domain-a",
        )
        source = SourceDocument(
            id="source-restricted",
            title="Restricted architecture decision",
            object_key="sources/restricted-adr.md",
            media_type="text/markdown",
            sha256="source-sha",
            domain_id="domain-a",
            category_id=category.id,
            sensitivity="restricted",
        )
        version = SourceDocumentVersion(
            id="version-restricted", source_document_id=source.id, version_sha256="version-sha"
        )
        page = SourcePage(
            id="page-restricted",
            source_version_id=version.id,
            page_number=1,
            content_checksum="page-sha",
            classification="text_extracted",
        )
        element = SourceElement(
            id="element-restricted",
            source_version_id=version.id,
            source_page_id=page.id,
            native_element_id="paragraph:1",
            kind="paragraph",
            ordinal=0,
            text="This quote must not leak.",
            checksum="element-sha",
            page_number=1,
            char_start=0,
            char_end=27,
        )
        block = IdeaBlock(id="idea-restricted", domain_id="domain-a")
        revision = IdeaBlockRevision(
            id="idea-restricted-r1",
            idea_block_id=block.id,
            revision_number=1,
            statement="The restricted record is supported.",
            normalized_statement="the restricted record is supported",
            type="decision",
            confidence="verified",
        )
        record = create_record(
            session,
            business_key="ADR-RESTRICTED",
            record_type="adr",
            title="Restricted decision",
            domain_id="domain-a",
            attributes={
                "context": "Restricted context",
                "decision": "Keep the decision private.",
                "rationale": "It is sensitive.",
                "consequences": "Only authorized readers may see evidence.",
            },
            actor="architect",
        )
        session.add_all((category, source, version, page, element, block, revision))
        session.flush()
        session.add(
            GovernanceRecordIdeaBlock(
                governance_record_id=record.id, idea_block_id=block.id, role="supporting", display_order=0
            )
        )
        session.add(
            ClaimEvidence(
                id="evidence-restricted",
                idea_block_id=block.id,
                idea_block_revision_id=revision.id,
                source_element_id=element.id,
                source_quote="This quote must not leak.",
                confidence="verified",
                extraction_method="manual",
            )
        )
        session.flush()

        source_ids = governance_record_source_element_ids(session, [record])
        restricted_reader = PermissionSnapshot(
            principal_id="reader",
            domain_permissions={"domain-a": frozenset({"read"})},
            category_permissions={"architecture": frozenset({"read"})},
            sensitivity_permissions={"internal": frozenset({"read"})},
        )
        readable_ids = permitted_source_element_ids(session, source_ids, permissions=restricted_reader, action="read")
        assert readable_ids == frozenset()
        detail = governance_record_details(
            session, [record], allowed_source_element_ids=readable_ids, allowed_idea_block_ids=frozenset()
        )[0]
        assert detail is not None
        assert detail["evidence"] == []
        assert detail["idea_blocks"] == []
        assert any(item["action"] == "governance.record.created" for item in detail["audit"])

        authorized_reader = PermissionSnapshot(
            principal_id="authorized-reader",
            domain_permissions={"domain-a": frozenset({"read"})},
            category_permissions={"architecture": frozenset({"read"})},
            sensitivity_permissions={"restricted": frozenset({"read"})},
        )
        readable_ids = permitted_source_element_ids(session, source_ids, permissions=authorized_reader, action="read")
        detail = governance_record_details(
            session, [record], allowed_source_element_ids=readable_ids, allowed_idea_block_ids=frozenset({block.id})
        )[0]
        assert detail is not None
        assert [item["source_quote"] for item in detail["evidence"]] == ["This quote must not leak."]
        assert [item["id"] for item in detail["idea_blocks"]] == [block.id]
    finally:
        session.close()


def test_contradiction_review_bundle_and_decision_are_relation_scoped() -> None:
    session = _session()
    try:
        category = Entity(
            id="category-architecture",
            preferred_label="architecture",
            normalized_label="architecture",
            ontology_type="category",
            domain_id="domain-a",
        )
        source = SourceDocument(
            id="source-contradiction",
            title="Contradictory architecture claims",
            object_key="sources/contradiction.md",
            media_type="text/markdown",
            sha256="contradiction-source-sha",
            domain_id="domain-a",
            category_id=category.id,
        )
        version = SourceDocumentVersion(
            id="version-contradiction", source_document_id=source.id, version_sha256="contradiction-version-sha"
        )
        page = SourcePage(
            id="page-contradiction",
            source_version_id=version.id,
            page_number=1,
            content_checksum="contradiction-page-sha",
            classification="text_extracted",
        )
        element = SourceElement(
            id="element-contradiction",
            source_version_id=version.id,
            source_page_id=page.id,
            native_element_id="paragraph:1",
            kind="paragraph",
            ordinal=0,
            text="The service is both available and unavailable.",
            checksum="contradiction-element-sha",
            page_number=1,
            char_start=0,
            char_end=47,
        )
        first = IdeaBlock(id="idea-contradiction-a", domain_id="domain-a")
        second = IdeaBlock(id="idea-contradiction-b", domain_id="domain-a")
        first_revision = IdeaBlockRevision(
            id="idea-contradiction-a-r1",
            idea_block_id=first.id,
            revision_number=1,
            statement="The service is available.",
            normalized_statement="the service is available",
            type="assertion",
            confidence="verified",
            publication_status="verified",
            semantic_key="contradiction-key-a",
        )
        second_revision = IdeaBlockRevision(
            id="idea-contradiction-b-r1",
            idea_block_id=second.id,
            revision_number=1,
            statement="The service is unavailable.",
            normalized_statement="the service is unavailable",
            type="assertion",
            confidence="verified",
            publication_status="verified",
            semantic_key="contradiction-key-b",
        )
        session.add_all((category, source, version, page, element, first, second, first_revision, second_revision))
        session.flush()
        first.current_revision_id = first_revision.id
        second.current_revision_id = second_revision.id
        record = create_record(
            session,
            business_key="ADR-CONTRADICTION-001",
            record_type="adr",
            title="Service availability decision",
            domain_id="domain-a",
            attributes={
                "context": "The availability requirement is disputed.",
                "decision": "Review the conflicting claims.",
                "rationale": "Both source assertions must remain visible.",
                "consequences": "A reviewer decides the governed outcome.",
            },
            actor="architect",
        )
        transition_record(
            session,
            record=record,
            target_status="accepted",
            actor="architect",
            reason="Accepted.",
            expected_revision_id=record.current_revision_id,
        )
        session.add_all(
            (
                GovernanceRecordIdeaBlock(
                    governance_record_id=record.id, idea_block_id=first.id, role="supporting", display_order=0
                ),
                GovernanceRecordIdeaBlock(
                    governance_record_id=record.id, idea_block_id=second.id, role="supporting", display_order=1
                ),
                IdeaBlockRelation(
                    id="idea-relation-contradiction",
                    source_idea_block_id=first.id,
                    target_idea_block_id=second.id,
                    relation_type="contradicts",
                    direction="symmetric",
                    status="verified",
                    confidence=1,
                    decision_provenance={"action": "semantic_review"},
                ),
                Entity(
                    id="service-contradiction-a",
                    preferred_label="Service A",
                    normalized_label="service a",
                    ontology_type="service",
                    domain_id="domain-a",
                ),
                Entity(
                    id="service-contradiction-b",
                    preferred_label="Service B",
                    normalized_label="service b",
                    ontology_type="service",
                    domain_id="domain-a",
                ),
                EntityRelationship(
                    id="entity-relationship-unrelated",
                    source_entity_id="service-contradiction-a",
                    target_entity_id="service-contradiction-b",
                    relation_type="depends_on",
                    confidence="verified",
                    status="verified",
                ),
                RelationshipProvenance(
                    id="entity-provenance-unrelated",
                    relationship_id="entity-relationship-unrelated",
                    idea_block_id=first.id,
                    source_element_id=element.id,
                    confidence="verified",
                    extraction_method="test",
                    source_quote=element.text,
                    status="verified",
                ),
                ClaimEvidence(
                    id="claim-evidence-contradiction-a",
                    idea_block_id=first.id,
                    idea_block_revision_id=first_revision.id,
                    source_element_id=element.id,
                    source_quote="The service is available.",
                    confidence="verified",
                    extraction_method="test",
                ),
                ClaimEvidence(
                    id="claim-evidence-contradiction-b",
                    idea_block_id=second.id,
                    idea_block_revision_id=second_revision.id,
                    source_element_id=element.id,
                    source_quote="The service is unavailable.",
                    confidence="verified",
                    extraction_method="test",
                ),
            )
        )
        session.commit()

        tasks = detect_contradiction_candidates(session)
        assert len(tasks) == 1
        task = tasks[0]
        session.flush()
        bundle = build_review_bundle(session, task.id)

        assert {claim["id"] for claim in bundle["claims"]} == {first.id, second.id}
        assert bundle["idea_block_relations"][0]["id"] == "idea-relation-contradiction"
        assert bundle["governance_records"][0]["id"] == record.id
        assert bundle["relationships"] == []
        assert bundle["contradictions"] == ["idea-relation-contradiction"]
        assert {claim["id"] for claim in bundle["claims"]} == {first.id, second.id}

        decide_review_bundle(
            session,
            task_id=task.id,
            decision="rejected",
            actor_id="reviewer",
            resolution="The contradiction relation is not actionable.",
        )
        session.flush()

        assert session.get(IdeaBlockRelation, "idea-relation-contradiction").status == "rejected"
        assert session.get(EntityRelationship, "entity-relationship-unrelated").status == "verified"
        assert session.get(RelationshipProvenance, "entity-provenance-unrelated").status == "verified"
        assert session.get(IdeaBlock, first.id).current_revision_id == first_revision.id
        assert session.get(IdeaBlock, second.id).current_revision_id == second_revision.id
        assert session.get(GovernanceRecord, record.id).lifecycle_status == "accepted"
        assert session.get(ReviewTask, task.id).status == "rejected"
        assert detect_contradiction_candidates(session) == []
    finally:
        session.close()
