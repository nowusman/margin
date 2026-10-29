from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.export_routes import router
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.export import complete_export, request_export
from margin_db.export_models import EXPORT_STATE_READY
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_db.user_models import Domain


def test_export_carries_only_what_the_requester_may_read() -> None:
    client = _client()

    created = client.post("/api/exports", json={"domain_id": "domain-a"})
    assert created.status_code == 202
    export_id = created.json()["export_id"]
    assert created.json()["state"] == "pending"

    with _session_factory_for(client)() as session:
        bundle = complete_export(session, export_id)
        session.commit()
        assert bundle.state == EXPORT_STATE_READY

    manifest = client.get(f"/api/exports/{export_id}/manifest")
    assert manifest.status_code == 200
    body = manifest.json()["manifest"]
    assert body["domain_id"] == "domain-a"
    assert body["included_record_kinds"]
    assert body["excluded_by_construction"]

    download = client.get(f"/api/exports/{export_id}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "records/entities.json" in names
        entities = json.loads(archive.read("records/entities.json"))
        assert [row["id"] for row in entities] == ["entity-readable"]
        assert all("restricted" not in json.dumps(row) for row in entities)

        archived_manifest = json.loads(archive.read("manifest.json"))
        assert archived_manifest["record_kinds"] == body["record_kinds"]
        assert set(names) == {"manifest.json"} | {
            f"records/{kind}.json" for kind in archived_manifest["included_record_kinds"]
        }
        for kind, count in archived_manifest["record_kinds"].items():
            assert len(json.loads(archive.read(f"records/{kind}.json"))) == count

        for name in sorted(names):
            if not name.startswith("records/"):
                continue
            body_text = archive.read(name).decode("utf-8")
            assert "object_key" not in body_text
            assert "ingestion_job" not in body_text
            assert "embedding" not in body_text
            assert "personal_note" not in body_text


def test_the_export_archive_is_deterministic() -> None:
    client = _client()

    created = client.post("/api/exports", json={"domain_id": "domain-a"})
    export_id = created.json()["export_id"]
    with _session_factory_for(client)() as session:
        complete_export(session, export_id)
        session.commit()

    first = client.get(f"/api/exports/{export_id}/download").content
    second = client.get(f"/api/exports/{export_id}/download").content
    assert first == second

    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["content_hash"]


def test_an_export_of_a_domain_the_caller_cannot_read_is_refused() -> None:
    client = _client()

    denied = client.post("/api/exports", json={"domain_id": "domain-hidden"})
    assert denied.status_code == 403


def test_export_requests_carry_the_requesters_scope() -> None:
    client = _client()

    created = client.post("/api/exports", json={"domain_id": "domain-a"})
    assert created.status_code == 202

    with _session_factory_for(client)() as session:
        bundle = request_export(
            session,
            domain_id="domain-a",
            requested_by="second-reader",
            scope=__import__("margin_db.export", fromlist=["ExportScope"]).ExportScope(
                domain_ids=frozenset({"domain-a"})
            ),
        )
        session.commit()
        assert bundle.scope["domain_ids"] == ["domain-a"]


def _session_factory_for(client: TestClient) -> sessionmaker:
    app = client.app
    assert isinstance(app, FastAPI)
    return app.state.session_factory


def _client() -> TestClient:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add_all(
            (
                Domain(id="domain-a", name="Domain A"),
                Domain(id="domain-hidden", name="Hidden"),
                Entity(
                    id="entity-readable",
                    preferred_label="Payment API",
                    normalized_label="payment api",
                    ontology_type="service",
                    domain_id="domain-a",
                    lifecycle_status="active",
                ),
                Entity(
                    id="entity-restricted",
                    preferred_label="Secret Ledger",
                    normalized_label="secret ledger",
                    ontology_type="service",
                    domain_id="domain-a",
                    lifecycle_status="active",
                ),
                SourceDocument(
                    id="source-readable",
                    title="Architecture",
                    object_key="architecture.md",
                    media_type="text/markdown",
                    sha256="a" * 64,
                    size_bytes=10,
                    domain_id="domain-a",
                    category_id="category-design",
                    sensitivity="internal",
                    status="knowledge_ready",
                ),
                SourceDocumentVersion(
                    id="source-version-readable",
                    source_document_id="source-readable",
                    version_sha256="b" * 64,
                    status="active",
                ),
                SourcePage(
                    id="source-page-readable",
                    source_version_id="source-version-readable",
                    page_number=1,
                    content_checksum="c" * 64,
                    classification="text_extracted",
                ),
                SourceElement(
                    id="element-readable",
                    source_version_id="source-version-readable",
                    source_page_id="source-page-readable",
                    native_element_id="paragraph-1",
                    kind="paragraph",
                    ordinal=0,
                    text="The payment API writes settled payments to the ledger.",
                ),
                IdeaBlock(id="block-readable", domain_id="domain-a"),
                IdeaBlockRevision(
                    id="revision-readable",
                    idea_block_id="block-readable",
                    revision_number=1,
                    statement="The payment API writes settled payments to the ledger.",
                    normalized_statement="the payment api writes settled payments to the ledger",
                    type="claim",
                    confidence="verified",
                    publication_status="verified",
                    semantic_key="payment-api-writes-settled-payments",
                ),
                SourceDocument(
                    id="source-restricted",
                    title="Restricted architecture",
                    object_key="restricted.md",
                    media_type="text/markdown",
                    sha256="d" * 64,
                    size_bytes=10,
                    domain_id="domain-a",
                    category_id="category-restricted",
                    sensitivity="restricted",
                    status="knowledge_ready",
                ),
                SourceDocumentVersion(
                    id="source-version-restricted",
                    source_document_id="source-restricted",
                    version_sha256="e" * 64,
                    status="active",
                ),
                SourcePage(
                    id="source-page-restricted",
                    source_version_id="source-version-restricted",
                    page_number=1,
                    content_checksum="f" * 64,
                    classification="text_extracted",
                ),
                SourceElement(
                    id="element-restricted",
                    source_version_id="source-version-restricted",
                    source_page_id="source-page-restricted",
                    native_element_id="paragraph-1",
                    kind="paragraph",
                    ordinal=0,
                    text="The secret ledger holds settled payments.",
                ),
                IdeaBlock(id="block-restricted", domain_id="domain-a"),
                IdeaBlockRevision(
                    id="revision-restricted",
                    idea_block_id="block-restricted",
                    revision_number=1,
                    statement="The secret ledger holds settled payments.",
                    normalized_statement="the secret ledger holds settled payments",
                    type="claim",
                    confidence="verified",
                    publication_status="verified",
                    semantic_key="secret-ledger-holds-settled-payments",
                ),
                ClaimEvidence(
                    id="evidence-restricted",
                    idea_block_id="block-restricted",
                    idea_block_revision_id="revision-restricted",
                    source_element_id="element-restricted",
                    source_quote="The secret ledger holds settled payments.",
                    confidence="verified",
                    extraction_method="test",
                    status="active",
                ),
                IdeaBlockEntity(idea_block_id="block-restricted", entity_id="entity-restricted", role="mentions"),
                ClaimEvidence(
                    id="evidence-readable",
                    idea_block_id="block-readable",
                    idea_block_revision_id="revision-readable",
                    source_element_id="element-readable",
                    source_quote="The payment API writes settled payments to the ledger.",
                    confidence="verified",
                    extraction_method="test",
                    status="active",
                ),
            )
        )
        for block_id, revision_id in (
            ("block-readable", "revision-readable"),
            ("block-restricted", "revision-restricted"),
        ):
            block = session.get(IdeaBlock, block_id)
            assert block is not None
            block.current_revision_id = revision_id
        session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.principal_id = "reader"
        request.state.permissions = PermissionSnapshot(
            principal_id="reader",
            domain_permissions={"domain-a": frozenset(("read",))},
            category_permissions={"category-design": frozenset(("read",))},
            sensitivity_permissions={"internal": frozenset(("read",))},
        )
        return await call_next(request)

    app.state.session_factory = session_factory
    app.state.enqueue_export = lambda _export_id: None
    app.include_router(router)
    return TestClient(app)
