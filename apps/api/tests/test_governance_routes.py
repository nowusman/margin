from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.governance_routes import router
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.user_models import Domain


def _client(*, permissions: PermissionSnapshot) -> TestClient:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add_all((Domain(id="commerce", name="Commerce"), Domain(id="operations", name="Operations")))
        session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def add_test_identity(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.permissions = permissions
        request.state.principal_id = permissions.principal_id
        return await call_next(request)

    app.state.session_factory = session_factory
    app.include_router(router)
    return TestClient(app)


def _permissions(*actions: str) -> PermissionSnapshot:
    return PermissionSnapshot(
        principal_id="architect",
        domain_permissions={"commerce": frozenset(actions)},
        category_permissions={"governance:adr": frozenset(actions)},
    )


def _all_governance_permissions(*actions: str) -> PermissionSnapshot:
    return PermissionSnapshot(
        principal_id="architect",
        domain_permissions={"commerce": frozenset(actions)},
        category_permissions={
            f"governance:{record_type}": frozenset(actions)
            for record_type in ("adr", "risk", "waiver", "technical_debt", "architecture_backlog")
        },
    )


def _adr_payload() -> dict[str, object]:
    return {
        "business_key": "ADR-HTTP-001",
        "record_type": "adr",
        "title": "Use the unified governance API",
        "domain_id": "commerce",
        "attributes": {
            "context": "Governance needs one API.",
            "decision": "Use GovernanceRecord.",
            "rationale": "It keeps history coherent.",
            "consequences": "Type-specific routes are removed.",
        },
    }


def test_governance_api_enforces_type_domain_and_stale_revision_permissions() -> None:
    client = _client(permissions=_permissions("read", "write", "review"))

    created = client.post("/api/governance/records", json=_adr_payload(), headers={"x-correlation-id": "request-001"})
    assert created.status_code == 201
    record = created.json()["record"]
    assert record["record_type"] == "adr"
    assert record["current_revision"] is not None
    assert any(item["correlation_id"] == "request-001" for item in record["audit"])

    listed = client.get("/api/governance/records?record_type=adr&domain_id=commerce")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["records"]] == [record["id"]]

    invalid_status = client.get("/api/governance/records?lifecycle_status=not-a-governance-state")
    assert invalid_status.status_code == 422
    invalid_for_type = client.get("/api/governance/records?record_type=adr&lifecycle_status=closed")
    assert invalid_for_type.status_code == 422

    denied_type = client.post(
        "/api/governance/records",
        json={
            **_adr_payload(),
            "business_key": "RISK-HTTP-001",
            "record_type": "risk",
            "attributes": {"likelihood": "low", "impact": "low", "severity": "low", "treatment": "accept"},
        },
    )
    assert denied_type.status_code == 403

    denied_domain = client.post(
        "/api/governance/records", json={**_adr_payload(), "business_key": "ADR-HTTP-002", "domain_id": "operations"}
    )
    assert denied_domain.status_code == 403

    revision_id = record["current_revision"]["id"]
    transitioned = client.post(
        f"/api/governance/records/{record['id']}/transitions",
        json={"target_status": "accepted", "reason": "Decision approved.", "expected_revision_id": revision_id},
    )
    assert transitioned.status_code == 200
    stale = client.patch(
        f"/api/governance/records/{record['id']}",
        json={"title": "Stale update", "reason": "Should not apply.", "expected_revision_id": revision_id},
    )
    assert stale.status_code == 409


def test_governance_api_returns_forbidden_without_record_read_scope() -> None:
    client = _client(permissions=_permissions())

    response = client.get("/api/governance/records?domain_id=commerce")

    assert response.status_code == 403


def test_generic_architecture_backlog_creation_rejects_unlinked_record_origin() -> None:
    client = _client(permissions=_all_governance_permissions("read", "write", "review"))
    origin_response = client.post("/api/governance/records", json=_adr_payload())
    assert origin_response.status_code == 201
    origin = origin_response.json()["record"]

    response = client.post(
        "/api/governance/records",
        json={
            "business_key": "AB-GENERIC-001",
            "record_type": "architecture_backlog",
            "title": "Track corrective work",
            "domain_id": "commerce",
            "priority": "high",
            "target_date": "2026-12-31",
            "attributes": {
                "pending_correction": "Deliver the durable correction.",
                "originating_governance_record_id": origin["id"],
            },
        },
    )

    assert response.status_code == 422
    assert "explicit backlog operation" in response.json()["detail"]


def test_architecture_backlog_creation_requires_review_authority_for_verified_origin() -> None:
    client = _client(permissions=_all_governance_permissions("read", "write"))
    origin_response = client.post("/api/governance/records", json=_adr_payload())
    assert origin_response.status_code == 201
    origin = origin_response.json()["record"]

    response = client.post(
        f"/api/governance/records/{origin['id']}/architecture-backlog",
        json={
            "business_key": "AB-REVIEW-001",
            "title": "Correct interim arrangement",
            "pending_correction": "Deliver the durable correction.",
            "reason": "Track correction.",
            "priority": "high",
            "target_date": "2026-12-31",
            "expected_origin_revision_id": origin["current_revision"]["id"],
        },
    )

    assert response.status_code == 403


def test_governance_relation_is_revision_checked_and_audited() -> None:
    client = _client(permissions=_all_governance_permissions("read", "write", "review"))
    source_response = client.post("/api/governance/records", json=_adr_payload())
    assert source_response.status_code == 201
    source = source_response.json()["record"]
    target_response = client.post(
        "/api/governance/records",
        json={**_adr_payload(), "business_key": "ADR-HTTP-RELATION-002", "title": "Related decision"},
    )
    assert target_response.status_code == 201
    target = target_response.json()["record"]

    linked = client.post(
        f"/api/governance/records/{source['id']}/relations",
        json={
            "target_record_id": target["id"],
            "relation_type": "mitigates",
            "status": "candidate",
            "valid_from": "2026-09-01",
            "reason": "Relationship awaits governance review.",
            "expected_revision_id": source["current_revision"]["id"],
            "expected_target_revision_id": target["current_revision"]["id"],
        },
    )
    assert linked.status_code == 200

    stale = client.post(
        f"/api/governance/records/{source['id']}/relations",
        json={
            "target_record_id": target["id"],
            "relation_type": "mitigates",
            "reason": "This stale write must be rejected.",
            "expected_revision_id": source["current_revision"]["id"],
            "expected_target_revision_id": target["current_revision"]["id"],
        },
    )
    assert stale.status_code == 409


def test_governance_api_uses_explicit_operations_for_all_lifecycle_exceptions() -> None:
    client = _client(permissions=_all_governance_permissions("read", "write", "review"))

    risk = client.post(
        "/api/governance/records",
        json={
            "business_key": "RISK-HTTP-001",
            "record_type": "risk",
            "title": "Capacity risk",
            "domain_id": "commerce",
            "attributes": {"likelihood": "low", "impact": "low", "severity": "low", "treatment": "accept"},
        },
    ).json()["record"]
    accepted = client.post(
        f"/api/governance/records/{risk['id']}/risk-acceptances",
        json={"reason": "Residual risk accepted.", "expected_revision_id": risk["current_revision"]["id"]},
    )
    assert accepted.status_code == 200
    closed = client.post(
        f"/api/governance/records/{risk['id']}/risk-closures",
        json={"reason": "Risk retired.", "expected_revision_id": accepted.json()["record"]["current_revision"]["id"]},
    )
    assert closed.status_code == 200

    waiver = client.post(
        "/api/governance/records",
        json={
            "business_key": "WAIVER-HTTP-001",
            "record_type": "waiver",
            "title": "Temporary exception",
            "domain_id": "commerce",
            "expiry_date": "2026-12-01",
            "attributes": {
                "standard_or_control": "Encryption",
                "scope": "Legacy connector",
                "approver": "Security",
                "reason": "Vendor remediation",
                "compensating_control": "Restricted network",
                "expiry_date": "2026-12-01",
            },
        },
    ).json()["record"]
    approved = client.post(
        f"/api/governance/records/{waiver['id']}/transitions",
        json={
            "target_status": "approved",
            "reason": "Approved.",
            "expected_revision_id": waiver["current_revision"]["id"],
        },
    )
    assert approved.status_code == 200
    revoked = client.post(
        f"/api/governance/records/{waiver['id']}/waiver-revocations",
        json={
            "reason": "Control restored.",
            "expected_revision_id": approved.json()["record"]["current_revision"]["id"],
        },
    )
    assert revoked.status_code == 200

    debt = client.post(
        "/api/governance/records",
        json={
            "business_key": "DEBT-HTTP-001",
            "record_type": "technical_debt",
            "title": "Replace driver",
            "domain_id": "commerce",
            "attributes": {
                "category": "platform",
                "remediation": "Use the supported driver.",
                "effort": "medium",
                "interest_or_impact": "Release risk.",
            },
        },
    ).json()["record"]
    planned = client.post(
        f"/api/governance/records/{debt['id']}/transitions",
        json={"target_status": "planned", "reason": "Planned.", "expected_revision_id": debt["current_revision"]["id"]},
    ).json()["record"]
    in_progress = client.post(
        f"/api/governance/records/{debt['id']}/transitions",
        json={
            "target_status": "in_progress",
            "reason": "Started.",
            "expected_revision_id": planned["current_revision"]["id"],
        },
    ).json()["record"]
    resolved = client.post(
        f"/api/governance/records/{debt['id']}/technical-debt-resolutions",
        json={
            "resolution_note": "Supported driver deployed.",
            "reason": "Verified.",
            "expected_revision_id": in_progress["current_revision"]["id"],
        },
    )
    assert resolved.status_code == 200

    adr = client.post("/api/governance/records", json=_adr_payload()).json()["record"]
    backlog = client.post(
        f"/api/governance/records/{adr['id']}/architecture-backlog",
        json={
            "business_key": "AB-HTTP-001",
            "title": "Correct interim arrangement",
            "pending_correction": "Deliver the durable correction.",
            "reason": "Track correction.",
            "priority": "high",
            "target_date": "2026-12-31",
            "expected_origin_revision_id": adr["current_revision"]["id"],
        },
    ).json()["record"]
    planned = client.post(
        f"/api/governance/records/{backlog['id']}/transitions",
        json={
            "target_status": "planned",
            "reason": "Planned.",
            "expected_revision_id": backlog["current_revision"]["id"],
        },
    ).json()["record"]
    in_progress = client.post(
        f"/api/governance/records/{backlog['id']}/transitions",
        json={
            "target_status": "in_progress",
            "reason": "Started.",
            "expected_revision_id": planned["current_revision"]["id"],
        },
    ).json()["record"]
    completed = client.post(
        f"/api/governance/records/{backlog['id']}/architecture-backlog-completions",
        json={
            "completion_note": "Correction delivered.",
            "reason": "Verified.",
            "expected_revision_id": in_progress["current_revision"]["id"],
        },
    )
    assert completed.status_code == 200
