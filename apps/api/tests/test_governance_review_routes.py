from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.governance_routes import router
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord, GovernanceRecordIdeaBlock, GovernanceRecordRevision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRelation, IdeaBlockRevision
from margin_db.review_models import ReviewTask
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import Domain


def _client(*, permissions: PermissionSnapshot) -> tuple[TestClient, sessionmaker[Session], str]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add(Domain(id="domain-a", name="Domain A"))
        first = IdeaBlock(id="review-idea-a", domain_id="domain-a")
        second = IdeaBlock(id="review-idea-b", domain_id="domain-a")
        session.add_all(
            (
                first,
                second,
                IdeaBlockRevision(
                    id="review-idea-a-r1",
                    idea_block_id=first.id,
                    revision_number=1,
                    statement="First statement",
                    normalized_statement="first statement",
                    semantic_key="review-key-a",
                    type="assertion",
                    confidence="verified",
                    publication_status="verified",
                ),
                IdeaBlockRevision(
                    id="review-idea-b-r1",
                    idea_block_id=second.id,
                    revision_number=1,
                    statement="Second statement",
                    normalized_statement="second statement",
                    semantic_key="review-key-b",
                    type="assertion",
                    confidence="verified",
                    publication_status="verified",
                ),
                GovernanceRecord(
                    id="review-governance-record",
                    business_key="ADR-REVIEW-001",
                    record_type="adr",
                    title="Review scope",
                    domain_id="domain-a",
                    lifecycle_status="accepted",
                    attributes={},
                ),
                IdeaBlockRelation(
                    id="review-contradiction",
                    source_idea_block_id=first.id,
                    target_idea_block_id=second.id,
                    relation_type="contradicts",
                    direction="symmetric",
                    status="verified",
                    confidence=1,
                ),
            )
        )
        session.flush()
        first.current_revision_id = "review-idea-a-r1"
        second.current_revision_id = "review-idea-b-r1"
        session.add_all(
            (
                GovernanceRecordIdeaBlock(
                    governance_record_id="review-governance-record",
                    idea_block_id=first.id,
                    role="supporting",
                    display_order=0,
                ),
                GovernanceRecordIdeaBlock(
                    governance_record_id="review-governance-record",
                    idea_block_id=second.id,
                    role="supporting",
                    display_order=1,
                ),
            )
        )
        task = ReviewTask(
            task_type="governance_contradiction_review",
            target_type="idea_block_relation",
            target_id="review-contradiction",
            reason="Contradiction requires review.",
            recommendation="Confirm or dismiss the contradiction.",
            metadata_={"idea_block_ids": [first.id, second.id], "governance_record_ids": ["review-governance-record"]},
        )
        session.add(task)
        session.commit()
        task_id = str(task.id)

    app = FastAPI()

    @app.middleware("http")
    async def add_test_identity(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.permissions = permissions
        request.state.principal_id = permissions.principal_id
        return await call_next(request)

    app.state.session_factory = session_factory
    app.include_router(router)
    return TestClient(app), session_factory, task_id


def test_contradiction_bundle_requires_governance_scope_and_exposes_bounded_context() -> None:
    client, _session_factory, task_id = _client(
        permissions=PermissionSnapshot(
            principal_id="reviewer",
            domain_permissions={"domain-a": frozenset({"review"})},
            category_permissions={"governance:adr": frozenset({"review"})},
        )
    )

    response = client.get(f"/api/governance/review/tasks/{task_id}/bundle")

    assert response.status_code == 200
    bundle = response.json()["bundle"]
    assert {claim["id"] for claim in bundle["claims"]} == {"review-idea-a", "review-idea-b"}
    assert bundle["idea_block_relations"][0]["id"] == "review-contradiction"
    assert bundle["governance_records"][0]["id"] == "review-governance-record"


def test_contradiction_bundle_uses_detector_governance_scope() -> None:
    client, session_factory, task_id = _client(
        permissions=PermissionSnapshot(
            principal_id="reviewer",
            domain_permissions={"domain-a": frozenset({"review"})},
            category_permissions={"governance:adr": frozenset({"review"})},
        )
    )
    with session_factory() as session:
        risk = GovernanceRecord(
            id="review-unpermitted-risk",
            business_key="RISK-REVIEW-001",
            record_type="risk",
            title="Unpermitted risk context",
            domain_id="domain-a",
            lifecycle_status="identified",
            attributes={},
        )
        session.add(risk)
        session.flush()
        session.add(
            GovernanceRecordRevision(
                id="review-unpermitted-risk-r1",
                governance_record_id=risk.id,
                revision_number=1,
                snapshot={},
                transition_reason="test",
                actor="test",
                process="test",
            )
        )
        session.flush()
        risk.current_revision_id = "review-unpermitted-risk-r1"
        session.add(
            GovernanceRecordIdeaBlock(
                governance_record_id="review-unpermitted-risk",
                idea_block_id="review-idea-a",
                role="supporting",
                display_order=0,
            )
        )
        session.add(
            ClaimEvidence(
                id="review-unpermitted-governance-evidence",
                idea_block_id="review-idea-a",
                idea_block_revision_id="review-idea-a-r1",
                governance_record_revision_id="review-unpermitted-risk-r1",
                source_quote="Restricted governance evidence.",
                confidence="verified",
                extraction_method="test",
            )
        )
        session.commit()

    response = client.get(f"/api/governance/review/tasks/{task_id}/bundle")

    assert response.status_code == 200
    bundle = response.json()["bundle"]
    assert [record["id"] for record in bundle["governance_records"]] == ["review-governance-record"]
    assert "Restricted governance evidence." not in response.text


def test_contradiction_bundle_denies_missing_governance_review_scope() -> None:
    client, _session_factory, task_id = _client(
        permissions=PermissionSnapshot(
            principal_id="reviewer", domain_permissions={"domain-a": frozenset({"review"})}, category_permissions={}
        )
    )

    response = client.get(f"/api/governance/review/tasks/{task_id}/bundle")

    assert response.status_code == 403


def test_contradiction_task_cannot_close_without_relation_scoped_decision() -> None:
    client, session_factory, task_id = _client(
        permissions=PermissionSnapshot(
            principal_id="reviewer",
            domain_permissions={"domain-a": frozenset({"review"})},
            category_permissions={"governance:adr": frozenset({"review"})},
        )
    )

    response = client.patch(
        f"/api/governance/review/tasks/{task_id}", json={"status": "rejected", "resolution": "bypass"}
    )

    assert response.status_code == 409
    with session_factory() as session:
        task = session.get(ReviewTask, UUID(task_id))
        relation = session.get(IdeaBlockRelation, "review-contradiction")
        assert task is not None and task.status == "open"
        assert relation is not None and relation.status == "verified"


def test_contradiction_bundle_filters_source_evidence_without_read_access() -> None:
    client, session_factory, task_id = _client(
        permissions=PermissionSnapshot(
            principal_id="reviewer",
            domain_permissions={"domain-a": frozenset({"review"})},
            category_permissions={"governance:adr": frozenset({"review"})},
        )
    )
    with session_factory() as session:
        session.add(
            Entity(
                id="restricted-source-category",
                preferred_label="Restricted",
                normalized_label="restricted",
                ontology_type="category",
                domain_id="domain-a",
                lifecycle_status="active",
            )
        )
        session.add(
            SourceDocument(
                id="restricted-source",
                title="Restricted source",
                object_key="restricted-source",
                media_type="text/plain",
                sha256="r" * 64,
                domain_id="domain-a",
                category_id="restricted-source-category",
                sensitivity="restricted",
            )
        )
        session.add(
            SourceDocumentVersion(
                id="restricted-source-v1", source_document_id="restricted-source", version_sha256="v" * 64
            )
        )
        session.add(
            SourceElement(
                id="restricted-source-element",
                source_version_id="restricted-source-v1",
                native_element_id="paragraph:1",
                kind="paragraph",
                ordinal=0,
                text="Restricted contradiction evidence.",
                char_start=0,
                char_end=35,
            )
        )
        session.add_all(
            (
                ClaimEvidence(
                    id="restricted-evidence-a",
                    idea_block_id="review-idea-a",
                    idea_block_revision_id="review-idea-a-r1",
                    source_element_id="restricted-source-element",
                    source_quote="Restricted contradiction evidence.",
                    confidence="verified",
                    extraction_method="test",
                ),
                ClaimEvidence(
                    id="restricted-evidence-b",
                    idea_block_id="review-idea-b",
                    idea_block_revision_id="review-idea-b-r1",
                    source_element_id="restricted-source-element",
                    source_quote="Restricted contradiction evidence.",
                    confidence="verified",
                    extraction_method="test",
                ),
            )
        )
        session.commit()

    response = client.get(f"/api/governance/review/tasks/{task_id}/bundle")

    assert response.status_code == 200
    bundle = response.json()["bundle"]
    assert all(not claim["evidence"] for claim in bundle["claims"])
    assert "Restricted contradiction evidence." not in response.text
