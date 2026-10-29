from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import create_app
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import CategoryPermission, Domain, DomainPermission
from margin_retrieval.service import RetrievalService


def _client() -> TestClient:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app = create_app()
    app.state.session_factory = sessionmaker(bind=engine)
    app.state.retrieval_service = RetrievalService()
    with app.state.session_factory() as session:
        session.add_all(
            [
                Domain(id="OSS", name="OSS"),
                Entity(
                    id="category-design",
                    preferred_label="Design Document",
                    normalized_label="design document",
                    ontology_type="category",
                    lifecycle_status="active",
                ),
                DomainPermission(domain_id="OSS", principal_type="user", principal_id="reader", permission="read"),
                CategoryPermission(
                    category="Design Document", principal_type="user", principal_id="reader", permission="read"
                ),
                SourceDocument(
                    id="source-1",
                    title="Gateway design",
                    object_key="source-1",
                    media_type="text/markdown",
                    sha256="a" * 64,
                    domain_id="OSS",
                    category_id="category-design",
                    sensitivity="internal",
                ),
                SourceDocument(
                    id="source-confidential",
                    title="Confidential gateway design",
                    object_key="source-confidential",
                    media_type="text/markdown",
                    sha256="c" * 64,
                    domain_id="OSS",
                    category_id="category-design",
                    sensitivity="confidential",
                ),
                SourceDocumentVersion(
                    id="source-version-1", source_document_id="source-1", version_sha256="b" * 64, status="active"
                ),
                SourceElement(
                    id="element-1",
                    source_version_id="source-version-1",
                    kind="paragraph",
                    ordinal=1,
                    text="The gateway requires mutual TLS.",
                    page_number=1,
                ),
                CategoryPermission(
                    category="sensitivity:internal", principal_type="user", principal_id="reader", permission="read"
                ),
            ]
        )
        session.commit()
    return TestClient(app)


def _headers(user: str = "reader") -> dict[str, str]:
    return {"x-margin-user": user}


def _request() -> dict[str, object]:
    return {
        "query": "gateway mutual tls",
        "filters": {"domain_ids": ["OSS"], "category_ids": ["category-design"]},
        "top_k": 5,
        "expand": {"relation_types": [], "depth": 0, "limit": 0},
    }


def test_search_and_ask_share_the_versioned_contract() -> None:
    client = _client()

    search = client.post("/api/search", headers=_headers(), json=_request())
    ask = client.post("/api/ask", headers=_headers(), json=_request())

    assert search.status_code == 200
    assert ask.status_code == 200
    for payload in (search.json(), ask.json()):
        assert payload["contract_version"] == "v1"
        assert payload["response_mode"] in {"hybrid", "lexical_only", "search_only"}
        assert {"results", "applied_filters", "ambiguity", "operational_warnings", "stage_timings"} <= set(payload)
        assert payload["applied_filters"]["category_ids"] == ["category-design"]
    assert search.json()["response_mode"] == "lexical_only"
    assert ask.json()["response_mode"] == "search_only"
    assert {"answer", "answer_citation_ids", "answer_unavailable_reason", "model"} <= set(ask.json())


def test_filter_options_expose_only_authorized_canonical_categories() -> None:
    response = _client().get("/api/retrieval/filter-options", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["categories"] == [{"id": "category-design", "label": "Design Document"}]
    assert "expansion_relationship_types" in payload
    assert "architecture_backlog" in payload["governance_statuses"] or not payload["governance_statuses"]


def test_search_rejects_unknown_filter_values_and_denied_users() -> None:
    client = _client()
    invalid = _request()
    invalid["filters"] = {"entity_types": ["not-an-ontology-type"]}

    unknown = client.post("/api/search", headers=_headers(), json=invalid)
    denied = client.post("/api/search", headers=_headers("denied"), json=_request())

    assert unknown.status_code == 422
    assert denied.status_code == 403


def test_search_rejects_explicit_domain_outside_permissions_as_forbidden() -> None:
    client = _client()
    request = _request()
    request["filters"] = {"domain_ids": ["restricted-domain"]}

    response = client.post("/api/search", headers=_headers(), json=request)

    assert response.status_code == 403


def test_search_validates_explicit_source_filters() -> None:
    client = _client()

    unknown_request = _request()
    unknown_request["filters"] = {"source_document_ids": ["source-missing"]}
    unknown = client.post("/api/search", headers=_headers(), json=unknown_request)

    restricted_request = _request()
    restricted_request["filters"] = {"source_document_ids": ["source-confidential"]}
    restricted = client.post("/api/search", headers=_headers(), json=restricted_request)

    assert unknown.status_code == 422
    assert "unknown source document ID" in unknown.json()["detail"]
    assert restricted.status_code == 403
    assert "outside caller permissions" in restricted.json()["detail"]


def test_filter_options_hide_governance_states_without_governance_access() -> None:
    response = _client().get("/api/retrieval/filter-options", headers=_headers())

    assert response.status_code == 200
    assert response.json()["governance_record_types"] == []
    assert response.json()["architecture_backlog_states"] == []


def test_exact_source_element_requires_the_owning_source_permission() -> None:
    client = _client()

    allowed = client.get("/api/sources/source-1/elements", headers=_headers())
    denied = client.get("/api/sources/source-1/elements", headers=_headers("denied"))

    assert allowed.status_code == 200
    assert allowed.json()["elements"][0]["id"] == "element-1"
    assert denied.status_code == 403
