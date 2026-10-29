from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.charter_routes import router
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.user_models import Domain


def test_charter_api_authors_versions_and_composes_a_briefing() -> None:
    client = _client()

    empty = client.get("/api/domains/domain-a/charter")
    assert empty.status_code == 200
    assert empty.json()["active"] is None
    assert empty.json()["history"] == []

    authored = client.put(
        "/api/domains/domain-a/charter",
        json={
            "purpose": "Core platform engineering.",
            "scope_inclusions": "Operating systems and runtimes.",
            "scope_exclusions": "Product analytics.",
            "terminology": "OS means operating system.",
            "activate": True,
        },
    )
    assert authored.status_code == 200
    first = authored.json()["charter"]
    assert first["version"] == 1
    assert first["status"] == "active"
    assert first["author_principal_id"] == "steward"
    assert first["purpose"] == "Core platform engineering."

    revised = client.put(
        "/api/domains/domain-a/charter",
        json={
            "purpose": "Core platform engineering and developer tooling.",
            "scope_inclusions": "",
            "scope_exclusions": "",
            "terminology": "",
            "activate": True,
        },
    )
    assert revised.status_code == 200
    second = revised.json()["charter"]
    assert second["version"] == 2

    active = client.get("/api/domains/domain-a/charter")
    assert active.json()["active"]["version"] == 2
    assert [entry["version"] for entry in active.json()["history"]] == [2, 1]
    assert [entry["status"] for entry in active.json()["history"]] == ["active", "superseded"]


def test_charter_briefing_describes_the_composed_corpus() -> None:
    client = _client()

    client.put("/api/domains/domain-a/charter", json={"purpose": "Core platform engineering.", "activate": True})

    briefing = client.get("/api/domains/domain-a/briefing")
    assert briefing.status_code == 200
    payload = briefing.json()
    assert payload["domain_id"] == "domain-a"
    assert payload["charter_version"] == 1
    assert "Core platform engineering." in payload["briefing"]
    assert payload["entity_count"] == 1
    assert payload["truncated"] is False


def test_charter_api_refuses_domains_the_caller_cannot_write() -> None:
    client = _client()

    denied = client.put("/api/domains/domain-restricted/charter", json={"purpose": "Not allowed.", "activate": True})
    assert denied.status_code == 403

    unknown = client.get("/api/domains/domain-unknown/charter")
    assert unknown.status_code == 404


def _client() -> TestClient:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add_all(
            (
                Domain(id="domain-a", name="Domain A"),
                Domain(id="domain-restricted", name="Restricted"),
                Entity(
                    id="entity-a",
                    preferred_label="Payment API",
                    normalized_label="payment api",
                    ontology_type="service",
                    domain_id="domain-a",
                    lifecycle_status="active",
                ),
            )
        )
        session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.principal_id = "steward"
        request.state.permissions = PermissionSnapshot(
            principal_id="steward",
            domain_permissions={
                "domain-a": frozenset(("read", "write")),
                "domain-unknown": frozenset(("read", "write")),
            },
        )
        return await call_next(request)

    app.state.session_factory = session_factory
    app.include_router(router)
    return TestClient(app)
