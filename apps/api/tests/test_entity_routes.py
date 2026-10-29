from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.entity_resolution_routes import router, source_entity_router
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.user_models import Domain, User


def _client(*, permissions: PermissionSnapshot) -> TestClient:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add_all((Domain(id="commerce", name="Commerce"), User(id="reviewer", display_name="Reviewer")))
        session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def add_test_identity(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.permissions = permissions
        request.state.principal_id = permissions.principal_id
        return await call_next(request)

    app.state.session_factory = session_factory
    app.state.enqueue_entity_reresolution = lambda job_id: job_id
    app.include_router(router)
    app.include_router(source_entity_router)
    return TestClient(app)


def _permissions(*permissions: str) -> PermissionSnapshot:
    return PermissionSnapshot(principal_id="reviewer", domain_permissions={"commerce": frozenset(permissions)})


def test_entity_catalog_api_governs_the_canonical_lifecycle() -> None:
    client = _client(permissions=_permissions("read", "write", "review"))

    ontology = client.get("/api/entities/catalog/ontology")
    assert ontology.status_code == 200
    assert "api" in ontology.json()["entity_types"]

    created = client.post(
        "/api/entities", json={"preferred_label": "Order API", "ontology_type": "api", "domain_id": "commerce"}
    )
    assert created.status_code == 201
    entity_id = created.json()["id"]
    assert created.json()["lifecycle_status"] == "draft"

    draft_list = client.get("/api/entities?domain_id=commerce&lifecycle_status=draft&unresolved=true")
    assert draft_list.status_code == 200
    assert [item["id"] for item in draft_list.json()["items"]] == [entity_id]
    assert draft_list.json()["unresolved_mentions"] == []

    approved = client.post(f"/api/entities/{entity_id}/approve", json={"reason": "reviewed source evidence"})
    assert approved.status_code == 200
    assert approved.json()["lifecycle_status"] == "active"

    alias = client.post(
        f"/api/entities/{entity_id}/aliases", json={"alias": "Orders API", "reason": "source-established alias"}
    )
    assert alias.status_code == 201
    filtered = client.get("/api/entities?domain_id=commerce&alias=Orders%20API")
    assert [item["id"] for item in filtered.json()["items"]] == [entity_id]

    deprecated = client.post(
        f"/api/entities/{entity_id}/aliases/{alias.json()['id']}/deprecate", json={"reason": "source label retired"}
    )
    assert deprecated.status_code == 200
    assert deprecated.json()["lifecycle_status"] == "deprecated"

    detail = client.get(f"/api/entities/{entity_id}")
    assert detail.status_code == 200
    assert {item["alias"] for item in detail.json()["aliases"]} == {"Order API", "Orders API"}

    retired = client.post(f"/api/entities/{entity_id}/retire", json={"reason": "superseded source catalog"})
    assert retired.status_code == 200
    assert retired.json()["lifecycle_status"] == "retired"
    immutable = client.patch(
        f"/api/entities/{entity_id}", json={"preferred_label": "Replacement API", "reason": "must be rejected"}
    )
    assert immutable.status_code == 409
    assert "cannot update" in immutable.json()["detail"]


def test_entity_catalog_api_denies_direct_access_without_domain_permission() -> None:
    reader = _client(permissions=_permissions())
    denied = reader.get("/api/entities?domain_id=commerce")
    assert denied.status_code == 403
