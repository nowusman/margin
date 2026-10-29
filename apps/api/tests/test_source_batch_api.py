from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.source_batch_routes import router
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.user_models import Domain


@dataclass
class RecordedUpload:
    filename: str
    metadata: dict[str, Any]
    source_uri: str | None
    domain_id: str | None


@dataclass
class StubResult:
    source_document: Any
    ingestion_job: Any = None
    cached: bool = False


class StubIngestionService:
    """Records what a batch upload asked to be ingested."""

    def __init__(self) -> None:
        self.uploads: list[RecordedUpload] = []

    def upload_source_file(
        self,
        session,
        *,
        filename: str,
        media_type: str | None,
        path,
        source_uri: str | None = None,
        domain_id: str | None = None,
        category: str | None = None,
        sensitivity: str = "internal",
        actor: str | None = None,
        auto_process: bool = False,
        owner: str | None = None,
        steward: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StubResult:
        self.uploads.append(
            RecordedUpload(filename=filename, metadata=dict(metadata or {}), source_uri=source_uri, domain_id=domain_id)
        )
        return StubResult(source_document=type("Document", (), {"id": f"doc-{len(self.uploads)}"})())


def test_a_batch_records_the_folder_each_file_sits_in() -> None:
    client, service = _client()

    response = client.post(
        "/api/sources/batch",
        files=[
            ("files", ("reconciliation.md", b"content", "text/markdown")),
            ("files", ("notes.md", b"content", "text/markdown")),
        ],
        data={"paths": '["payments/ledger/reconciliation.md", "payments/notes.md"]', "domain_id": "domain-a"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["accepted"] == 2
    assert payload["rejected"] == 0
    assert [item["folder_context"] for item in payload["results"]] == ["payments > ledger", "payments"]
    assert [item["relative_path"] for item in payload["results"]] == [
        "payments/ledger/reconciliation.md",
        "payments/notes.md",
    ]

    assert [upload.metadata["folder_context"] for upload in service.uploads] == ["payments > ledger", "payments"]
    assert service.uploads[0].source_uri == "payments/ledger/reconciliation.md"


def test_a_batch_without_paths_uses_the_uploaded_filenames() -> None:
    client, service = _client()

    response = client.post(
        "/api/sources/batch",
        files=[("files", ("notes.md", b"content", "text/markdown"))],
        data={"domain_id": "domain-a"},
    )

    assert response.status_code == 201
    assert response.json()["results"][0]["folder_context"] == ""
    assert service.uploads[0].metadata["folder_context"] is None


def test_a_batch_rejects_paths_that_do_not_match_the_files() -> None:
    client, _ = _client()

    response = client.post(
        "/api/sources/batch",
        files=[("files", ("notes.md", b"content", "text/markdown"))],
        data={"paths": '["a.md", "b.md"]'},
    )

    assert response.status_code == 422


def test_a_batch_rejects_malformed_paths() -> None:
    client, _ = _client()

    response = client.post(
        "/api/sources/batch", files=[("files", ("notes.md", b"content", "text/markdown"))], data={"paths": "not json"}
    )

    assert response.status_code == 422


def _client() -> tuple[TestClient, StubIngestionService]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add(Domain(id="domain-a", name="Domain A"))
        session.add(Domain(id="ALL", name="All domains"))
        session.commit()

    service = StubIngestionService()
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.principal_id = "reader"
        request.state.permissions = PermissionSnapshot(
            principal_id="reader", domain_permissions={"domain-a": frozenset(("read", "write"))}
        )
        return await call_next(request)

    app.state.session_factory = session_factory
    app.state.source_ingestion_service = service
    app.include_router(router)
    return TestClient(app), service
