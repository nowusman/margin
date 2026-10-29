from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import create_app
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.model_catalog_models import ModelConfiguration
from margin_db.object_storage import FilesystemObjectStore
from margin_db.source_models import IngestionJob, SourceDocument
from margin_db.user_models import CategoryPermission, Domain, DomainPermission
from margin_retrieval.service import RetrievalService

FRONTEND_ROUTE_CONTRACTS: set[tuple[str, str]] = {
    ("POST", "/api/sources"),
    ("GET", "/api/dashboard/summary"),
    ("GET", "/api/jobs"),
    ("GET", "/api/jobs/{job_id}"),
    ("POST", "/api/jobs/{job_id}/retry"),
    ("POST", "/api/jobs/{job_id}/cancel"),
    ("GET", "/api/sources/{source_document_id}/versions"),
    ("GET", "/api/sources/{source_document_id}/elements"),
    ("GET", "/api/sources/{source_document_id}/extraction-report"),
    ("PATCH", "/api/sources/{source_document_id}"),
    ("POST", "/api/sources/{source_document_id}/reprocess"),
    ("GET", "/api/evidence/{evidence_id}"),
    ("GET", "/api/c4/tree"),
    ("GET", "/api/c4/overlays"),
    ("GET", "/api/relationships/neighbors/{entity_id}"),
    ("POST", "/api/relationships/path"),
    ("GET", "/api/relationships/impact/{entity_id}"),
    ("GET", "/api/relationships/governed-entity/{entity_id}"),
    ("GET", "/api/relationships/related-idea-block/{idea_block_id}"),
    ("GET", "/api/entities"),
    ("POST", "/api/entities"),
    ("GET", "/api/entities/catalog/ontology"),
    ("GET", "/api/entities/mentions"),
    ("POST", "/api/entities/mentions/{mention_id}/resolve"),
    ("GET", "/api/entities/{entity_id}"),
    ("PATCH", "/api/entities/{entity_id}"),
    ("POST", "/api/entities/{entity_id}/approve"),
    ("POST", "/api/entities/{entity_id}/aliases"),
    ("POST", "/api/entities/{entity_id}/aliases/{alias_id}/deprecate"),
    ("POST", "/api/entities/{entity_id}/merge"),
    ("POST", "/api/entities/{entity_id}/supersede"),
    ("POST", "/api/entities/{entity_id}/retire"),
    ("GET", "/api/diagrams/{diagram_id}"),
    ("POST", "/api/ask"),
    ("POST", "/api/search"),
    ("POST", "/api/search/source-discovery"),
    ("GET", "/api/retrieval/filter-options"),
    ("GET", "/api/domains"),
    ("POST", "/api/domains"),
    ("DELETE", "/api/domains/{domain_id}"),
    ("GET", "/api/evaluations/runs"),
    ("GET", "/api/evaluations/runs/{run_id}"),
    ("GET", "/api/governance/review/tasks"),
    ("GET", "/api/governance/review/tasks/{task_id}"),
    ("PATCH", "/api/governance/review/tasks/{task_id}"),
    ("GET", "/api/admin/models"),
    ("POST", "/api/admin/models"),
    ("PATCH", "/api/admin/models/{model_id}"),
    ("DELETE", "/api/admin/models/{model_id}"),
    ("POST", "/api/admin/models/{model_id}/test"),
    ("GET", "/api/admin/models/health"),
    ("GET", "/api/admin/models/configuration-checks"),
    ("GET", "/api/admin/system-health"),
    ("GET", "/api/admin/access"),
    ("POST", "/api/admin/access"),
    ("PATCH", "/api/admin/access/{user}"),
    ("DELETE", "/api/admin/access/{user}"),
    ("POST", "/api/admin/access/check"),
    ("GET", "/api/admin/audit-events"),
    ("GET", "/api/audit-events"),
}


def _client() -> TestClient:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    app: FastAPI = create_app()
    app.state.session_factory = session_factory
    app.state.retrieval_service = RetrievalService()
    with session_factory() as session:
        session.add(Domain(id="OSS", name="OSS"))
        session.add(
            ModelConfiguration(
                id="test-extraction-model",
                display_name="Test extraction model",
                purpose="extraction",
                runtime="test",
                model_name="test-model",
                endpoint_url="http://127.0.0.1:9/v1",
                status="active",
            )
        )
        for permission in ("read", "write", "review"):
            session.add(
                DomainPermission(domain_id="OSS", principal_type="user", principal_id="alice", permission=permission)
            )
        session.commit()
    return TestClient(app)


def test_frontend_contract_routes_are_mounted() -> None:
    app = create_app()
    mounted = {(method, route.path) for route in app.routes if isinstance(route, APIRoute) for method in route.methods}

    assert FRONTEND_ROUTE_CONTRACTS <= mounted


def test_frontend_contract_response_fields_for_shared_admin_and_dashboard_calls() -> None:
    client = _client()
    headers = {"x-margin-user": "alice", "x-margin-groups": "margin-admin"}

    dashboard = client.get("/api/dashboard/summary", headers=headers)
    assert dashboard.status_code == 200
    assert {"review_tasks_assigned", "stale_claims", "expired_waivers", "recent_adrs", "top_risks", "top_debt"} <= set(
        dashboard.json()
    )

    model = client.get("/api/admin/models", headers=headers).json()[0]
    assert {
        "id",
        "displayName",
        "purpose",
        "runtime",
        "endpointUrl",
        "contextLength",
        "maxTokens",
        "temperatureDefault",
        "license",
        "hardwareProfile",
        "status",
        "defaultScopes",
    } <= set(model)

    evaluations = client.get("/api/evaluations/runs", headers=headers)
    assert evaluations.status_code == 200
    assert "runs" in evaluations.json()

    audit_events = client.get("/api/audit-events", headers=headers)
    assert audit_events.status_code == 200
    assert "audit_events" in audit_events.json()


def test_frontend_jobs_endpoint_allows_read_without_write_and_filters_by_source_access(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    app: FastAPI = create_app()
    app.state.session_factory = session_factory
    app.state.retrieval_service = RetrievalService()
    app.state.object_store = FilesystemObjectStore(tmp_path / "raw")
    with session_factory() as session:
        session.add_all(
            [
                Domain(id="OSS", name="OSS"),
                Domain(id="BSS", name="BSS"),
                DomainPermission(domain_id="OSS", principal_type="user", principal_id="reader", permission="read"),
                CategoryPermission(
                    category="Design Document", principal_type="user", principal_id="reader", permission="read"
                ),
                CategoryPermission(
                    category="sensitivity:internal", principal_type="user", principal_id="reader", permission="read"
                ),
                Entity(
                    id="ent_design_document",
                    preferred_label="Design Document",
                    normalized_label="design document",
                    ontology_type="category",
                    lifecycle_status="active",
                ),
                SourceDocument(
                    id="src_visible",
                    title="Visible",
                    object_key="visible",
                    media_type="text/markdown",
                    sha256="a" * 64,
                    domain_id="OSS",
                    category_id="ent_design_document",
                    sensitivity="internal",
                ),
                SourceDocument(
                    id="src_hidden",
                    title="Hidden",
                    object_key="hidden",
                    media_type="text/markdown",
                    sha256="b" * 64,
                    domain_id="BSS",
                    category_id="ent_design_document",
                    sensitivity="internal",
                ),
                IngestionJob(id="job_visible", source_document_id="src_visible", status="failed"),
                IngestionJob(id="job_hidden", source_document_id="src_hidden", status="failed"),
            ]
        )
        session.commit()

    client = TestClient(app)
    headers = {"x-margin-user": "reader"}

    response = client.get("/api/jobs", headers=headers)
    assert response.status_code == 200
    assert [job["id"] for job in response.json()["jobs"]] == ["job_visible"]

    detail = client.get("/api/jobs/job_visible", headers=headers)
    assert detail.status_code == 200

    hidden = client.get("/api/jobs/job_hidden", headers=headers)
    assert hidden.status_code == 403

    visible_versions = client.get("/api/sources/src_visible/versions", headers=headers)
    assert visible_versions.status_code == 200

    hidden_versions = client.get("/api/sources/src_hidden/versions", headers=headers)
    assert hidden_versions.status_code == 403

    retry = client.post("/api/jobs/job_visible/retry", headers=headers)
    assert retry.status_code == 403
