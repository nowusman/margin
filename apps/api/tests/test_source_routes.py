from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.extraction_routes import router as extraction_router
from app.source_routes import router as source_router
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.entity_catalog import create_entity
from margin_db.entity_models import Entity, EntityMention, EntityResolutionDecision
from margin_db.idea_block_resolution import create_idea_block_revision
from margin_db.knowledge_models import ClaimEvidence, EmbeddingJob, EmbeddingRecord, IdeaBlock, IdeaBlockEntity
from margin_db.object_storage import FilesystemObjectStore
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.review_models import ReviewTask
from margin_db.source_ingestion import SourceIngestionService, get_job_status
from margin_db.source_models import IngestionJob, SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import Domain
from margin_embedding.interfaces import EMBEDDING_DIMENSIONS
from margin_ingest.contracts import ConversionResult
from margin_ingest.indexing import construct_normalized_document


@dataclass
class CountingConverter:
    calls: int = 0
    filenames: list[str] | None = None

    def convert(self, *, filename: str, media_type: str, data: bytes) -> ConversionResult:
        self.calls += 1
        if self.filenames is None:
            self.filenames = []
        self.filenames.append(filename)
        text = data.decode("utf-8")
        return ConversionResult(
            normalized_document=construct_normalized_document(
                text, source_name=filename, format=media_type, metadata={"filename": filename}
            ),
            converter="route-counting-converter",
            converter_version="test",
        )


class FlakyConverter:
    def __init__(self) -> None:
        self.calls = 0

    def convert(self, *, filename: str, media_type: str, data: bytes) -> ConversionResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("route conversion failed once")
        text = data.decode("utf-8")
        return ConversionResult(
            normalized_document=construct_normalized_document(text, source_name=filename, format=media_type),
            converter="route-flaky-converter",
            converter_version="test",
        )


@pytest.fixture()
def route_app(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(source_router)
    app.include_router(extraction_router)
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.object_store = FilesystemObjectStore(tmp_path / "objects")
    with app.state.session_factory() as session:
        create_entity(session, preferred_label="Design Document", ontology_type="category", domain_id=None)
        session.commit()

    def _enqueue(job_id: str) -> str:
        with app.state.session_factory() as session:
            service = SourceIngestionService(
                app.state.object_store, converter=getattr(app.state, "source_converter", None)
            )
            service.process_job(session, job_id)
            session.commit()
        return f"local-task-{job_id}"

    app.state.enqueue_ingestion_job = _enqueue
    return app


def _seed_upload_scope(route_app, domain_id: str) -> None:
    with route_app.state.session_factory() as session:
        if not session.get(Domain, domain_id):
            session.add(Domain(id=domain_id, name=domain_id))
        category = session.scalar(
            select(Entity).where(Entity.ontology_type == "category", Entity.normalized_label == "design document")
        )
        if category is None:
            create_entity(session, preferred_label="Design Document", ontology_type="category", domain_id=None)
        session.commit()


def _seed_source_metadata(route_app, domain_id: str = "BSS") -> None:
    _seed_upload_scope(route_app, domain_id)


def test_upload_route_supports_markdown_preview_and_content_hash_cache(route_app) -> None:
    _seed_upload_scope(route_app, "BSS")
    converter = CountingConverter()
    route_app.state.source_converter = converter
    client = TestClient(route_app)
    files = {"file": ("design.md", b"# Overview\n\nMargin evidence.\n", "text/markdown")}

    first = client.post(
        "/api/sources",
        data={
            "domain_id": "BSS",
            "category": "Design Document",
            "sensitivity": "internal",
            "source_uri": "https://example.test/design.md",
        },
        files=files,
    )
    second = client.post(
        "/api/sources",
        data={
            "domain_id": "BSS",
            "category": "Design Document",
            "sensitivity": "internal",
            "source_uri": "https://example.test/design.md",
        },
        files=files,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    first_body = first.json()
    second_body = second.json()
    assert first_body["cached"] is False
    assert second_body["cached"] is True
    assert first_body["source_document"]["id"] == second_body["source_document"]["id"]
    assert first_body["ingestion_job"]["state"] == "extracting"
    assert converter.calls == 1

    preview = client.get(f"/api/sources/{first_body['source_document']['id']}/preview")
    assert preview.status_code == 200
    assert preview.json()["raw_extracted_text"] == "# Overview\n\nMargin evidence.\n"
    assert preview.json()["elements"][0]["text"] == "# Overview"

    original = client.get(f"/api/sources/{first_body['source_document']['id']}/file")
    assert original.status_code == 200
    assert original.content == b"# Overview\n\nMargin evidence.\n"
    assert original.headers["content-type"].startswith("text/markdown")
    assert "design.md" in original.headers["content-disposition"]


def test_upload_route_uses_uploaded_filename_for_conversion_when_source_uri_is_present(route_app) -> None:
    _seed_upload_scope(route_app, "OSS")
    converter = CountingConverter()
    route_app.state.source_converter = converter
    client = TestClient(route_app)

    response = client.post(
        "/api/sources",
        data={
            "category": "Design Document",
            "sensitivity": "internal",
            "source_uri": "https://repo.example.org/architecture/overview.md",
            "domain_id": "OSS",
        },
        files={"file": ("overview.md", b"# Overview\n\nTrace me.\n", "text/markdown")},
    )

    assert response.status_code == 201
    assert converter.filenames == ["overview.md"]

    with route_app.state.session_factory() as session:
        source = session.get(SourceDocument, response.json()["source_document"]["id"])
        assert source is not None
        assert source.source_uri == "https://repo.example.org/architecture/overview.md"
        assert (source.metadata_ or {}).get("upload_filename") == "overview.md"


def test_office_preview_route_converts_and_caches_pdf(route_app) -> None:
    converter = CountingConverter()
    preview_calls = 0

    def office_preview_converter(*, filename: str, media_type: str, content: bytes) -> bytes:
        nonlocal preview_calls
        preview_calls += 1
        return b"%PDF-1.4\n% preview for " + filename.encode("utf-8")

    route_app.state.source_converter = converter
    route_app.state.office_preview_converter = office_preview_converter
    client = TestClient(route_app)

    upload = client.post(
        "/api/sources",
        files={
            "file": (
                "overview.docx",
                b"PK\x03\x04Office source content",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert upload.status_code == 201
    source_id = upload.json()["source_document"]["id"]

    preview = client.get(f"/api/sources/{source_id}/preview-file")

    assert preview.status_code == 200
    assert preview.content == b"%PDF-1.4\n% preview for overview.docx"
    assert preview.headers["content-type"].startswith("application/pdf")
    assert "overview.pdf" in preview.headers["content-disposition"]

    cached_preview = client.get(f"/api/sources/{source_id}/preview-file")

    assert cached_preview.status_code == 200
    assert cached_preview.content == preview.content
    assert preview_calls == 1
    with route_app.state.session_factory() as session:
        source = session.get(SourceDocument, source_id)
        assert source is not None
        assert (source.metadata_ or {}).get("preview_pdf_object_key")


def test_upload_route_normalizes_deterministic_llm_extraction_mode(route_app, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.source_routes.configured_active_model",
        lambda session, purpose: SimpleNamespace(
            endpoint_url="http://llm.example.test/v1",
            model_name="extractor",
            secret_reference=None,
            last_test_status="healthy",
        ),
    )
    monkeypatch.setattr("app.source_routes.probe_openai_compatible_endpoint", lambda **kwargs: None)
    client = TestClient(route_app)

    response = client.post(
        "/api/sources",
        data={"category": "Design Document", "sensitivity": "internal", "extraction_mode": "deterministic + LLM"},
        files={"file": ("overview.md", b"# Overview\n\nTrace me.\n", "text/markdown")},
    )

    assert response.status_code == 201
    with route_app.state.session_factory() as session:
        source = session.get(SourceDocument, response.json()["source_document"]["id"])
        assert source is not None
        assert (source.metadata_ or {}).get("extraction_mode") == "deterministic_llm"


def test_upload_route_fails_fast_when_deterministic_llm_model_is_missing(route_app) -> None:
    client = TestClient(route_app)

    response = client.post(
        "/api/sources",
        data={"category": "Design Document", "sensitivity": "internal", "extraction_mode": "deterministic + LLM"},
        files={"file": ("overview.md", b"# Overview\n\nTrace me.\n", "text/markdown")},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "LLM ingestion requires one active configured extraction model"
    with route_app.state.session_factory() as session:
        assert session.scalar(select(SourceDocument)) is None


def test_upload_route_treats_all_domains_as_unscoped_domain(route_app) -> None:
    client = TestClient(route_app)

    response = client.post(
        "/api/sources",
        data={"domain_id": "All domains", "category": "Design Document", "sensitivity": "internal"},
        files={"file": ("overview.md", b"# Overview\n\nTrace me.\n", "text/markdown")},
    )

    assert response.status_code == 201
    assert response.json()["source_document"]["domain_id"] is None


def test_upload_route_rejects_unknown_domain_labels(route_app) -> None:
    with route_app.state.session_factory() as session:
        session.add(Domain(id="BSS", name="BSS", owner="ea", steward="bss"))
        session.commit()
    client = TestClient(route_app)

    response = client.post(
        "/api/sources",
        data={"domain_id": "Architecture Principles", "category": "Design Document", "sensitivity": "internal"},
        files={"file": ("overview.md", b"# Overview\n\nTrace me.\n", "text/markdown")},
    )

    assert response.status_code == 422
    assert "unknown domain" in response.json()["detail"]


def test_upload_route_resolves_domain_display_name(route_app) -> None:
    with route_app.state.session_factory() as session:
        session.add(Domain(id="ARCH", name="Architecture", owner="ea", steward="ea"))
        session.commit()
    client = TestClient(route_app)

    response = client.post(
        "/api/sources",
        data={"domain_id": "Architecture", "category": "Design Document", "sensitivity": "internal"},
        files={"file": ("overview.md", b"# Overview\n\nTrace me.\n", "text/markdown")},
    )

    assert response.status_code == 201
    assert response.json()["source_document"]["domain_id"] == "ARCH"


def test_upload_route_rejects_unknown_category(route_app) -> None:
    client = TestClient(route_app)

    response = client.post(
        "/api/sources",
        data={"category": "Uncontrolled Category", "sensitivity": "internal"},
        files={"file": ("overview.md", b"# Overview\n\nTrace me.\n", "text/markdown")},
    )

    assert response.status_code == 422
    assert "unknown canonical category" in response.json()["detail"]


def test_upload_route_accepts_configured_category_entity(route_app) -> None:
    with route_app.state.session_factory() as session:
        create_entity(session, preferred_label="API Contract", ontology_type="category", domain_id=None)
        session.commit()
    client = TestClient(route_app)

    response = client.post(
        "/api/sources",
        data={"category": "API Contract", "sensitivity": "internal"},
        files={"file": ("contract.yaml", b"openapi: 3.0.0\n", "application/yaml")},
    )

    assert response.status_code == 201
    assert response.json()["source_document"]["category"] == "API Contract"


def test_upload_route_rejects_source_uri_without_file(route_app) -> None:
    client = TestClient(route_app)

    response = client.post(
        "/api/sources",
        data={
            "source_uri": "git@example.com:margin/repo.git",
            "source_version": "main",
            "category": "Design Document",
            "sensitivity": "internal",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "file is required; source_uri is provenance metadata only"


def test_metadata_correction_requires_write_access_to_target_scope(route_app) -> None:
    @route_app.middleware("http")
    async def _inject_permissions(request, call_next):
        request.state.permissions = PermissionSnapshot(
            principal_id="alice",
            domain_permissions={"OSS": frozenset({"read", "write"})},
            category_permissions={"Design Document": frozenset({"read", "write"})},
            sensitivity_permissions={"internal": frozenset({"read", "write"})},
        )
        return await call_next(request)

    client = TestClient(route_app)
    uploaded = client.post(
        "/api/sources",
        data={"domain_id": "OSS", "category": "Design Document", "sensitivity": "internal"},
        files={"file": ("overview.md", b"# Overview\n\nTrace me.\n", "text/markdown")},
    )
    assert uploaded.status_code == 201
    with route_app.state.session_factory() as session:
        session.add(Domain(id="BSS", name="BSS", owner="ea", steward="bss"))
        session.commit()

    response = client.patch(f"/api/sources/{uploaded.json()['source_document']['id']}", json={"domain_id": "BSS"})

    assert response.status_code == 403
    assert "missing write access for domain BSS" in response.json()["detail"]


def test_metadata_correction_checks_existing_source_before_new_domain(route_app) -> None:
    with route_app.state.session_factory() as session:
        session.add(Domain(id="BSS", name="BSS", owner="ea", steward="bss"))
        session.commit()
    client = TestClient(route_app)

    response = client.patch("/api/sources/src_missing", json={"domain_id": "Architecture Principles"})

    assert response.status_code == 404


def test_metadata_correction_checks_current_scope_before_new_domain(route_app) -> None:
    @route_app.middleware("http")
    async def _inject_permissions(request, call_next):
        request.state.permissions = PermissionSnapshot(
            principal_id="alice",
            domain_permissions={"OSS": frozenset({"read"})},
            category_permissions={"Design Document": frozenset({"read", "write"})},
            sensitivity_permissions={"internal": frozenset({"read", "write"})},
        )
        return await call_next(request)

    client = TestClient(route_app)
    with route_app.state.session_factory() as session:
        session.add(Domain(id="OSS", name="OSS", owner="ea", steward="oss"))
        session.add(Domain(id="BSS", name="BSS", owner="ea", steward="bss"))
        session.commit()
    uploaded = client.post(
        "/api/sources",
        data={"domain_id": "OSS", "category": "Design Document", "sensitivity": "internal"},
        files={"file": ("overview.md", b"# Overview\n\nTrace me.\n", "text/markdown")},
    )
    assert uploaded.status_code == 403

    with route_app.state.session_factory() as session:
        service = SourceIngestionService(route_app.state.object_store)
        result = service.upload_source(
            session,
            filename="overview.md",
            media_type="text/markdown",
            data=b"# Overview\n\nTrace me.\n",
            domain_id="OSS",
            category="Design Document",
            sensitivity="internal",
            actor=None,
            auto_process=False,
        )
        source_id = result.source_document.id
        session.commit()

    response = client.patch(f"/api/sources/{source_id}", json={"domain_id": "Architecture Principles"})

    assert response.status_code == 403
    assert "missing write access for domain OSS" in response.json()["detail"]


def test_metadata_correction_route_updates_domain_category_and_sensitivity(route_app) -> None:
    with route_app.state.session_factory() as session:
        create_entity(session, preferred_label="Architecture Standards", ontology_type="category", domain_id=None)
        session.commit()
    client = TestClient(route_app)
    upload = client.post(
        "/api/sources", files={"file": ("standard.txt", b"Architecture standard", "text/plain")}
    ).json()
    source_id = upload["source_document"]["id"]

    response = client.patch(
        f"/api/admin/sources/{source_id}/metadata",
        json={"domain_id": "OSS", "category": "Architecture Standards", "sensitivity": "confidential"},
    )

    assert response.status_code == 200
    body = response.json()["source_document"]
    assert body["domain_id"] == "OSS"
    assert body["category"] == "Architecture Standards"
    assert body["sensitivity"] == "confidential"


def test_metadata_correction_route_rejects_unknown_category(route_app) -> None:
    client = TestClient(route_app)
    upload = client.post(
        "/api/sources", files={"file": ("standard.txt", b"Architecture standard", "text/plain")}
    ).json()
    source_id = upload["source_document"]["id"]

    response = client.patch(f"/api/admin/sources/{source_id}/metadata", json={"category": "Uncontrolled Category"})

    assert response.status_code == 422
    assert "unknown canonical category" in response.json()["detail"]


def test_retry_route_reprocesses_failed_job(route_app) -> None:
    converter = FlakyConverter()
    route_app.state.source_converter = converter
    client = TestClient(route_app)

    upload = client.post("/api/sources", files={"file": ("retry.md", b"# Retry\n\nTry again.\n", "text/markdown")})
    assert upload.status_code == 201
    failed_job = upload.json()["ingestion_job"]
    assert failed_job["state"] == "failed"

    response = client.post(f"/api/admin/ingestion-jobs/{failed_job['id']}/retry")

    assert response.status_code == 200
    job = response.json()["ingestion_job"]
    assert job["state"] == "extracting"
    assert job["attempts"] == 2
    assert converter.calls == 2


def test_reprocess_route_retries_latest_failed_job(route_app) -> None:
    converter = FlakyConverter()
    route_app.state.source_converter = converter
    client = TestClient(route_app)

    upload = client.post(
        "/api/sources", files={"file": ("reprocess-failed.md", b"# Retry\n\nTry again.\n", "text/markdown")}
    )
    assert upload.status_code == 201
    source_id = upload.json()["source_document"]["id"]
    failed_job = upload.json()["ingestion_job"]
    assert failed_job["state"] == "failed"

    response = client.post(f"/api/sources/{source_id}/reprocess")

    assert response.status_code == 200
    job = response.json()["ingestion_job"]
    assert job["id"] == failed_job["id"]
    assert job["state"] == "extracting"
    assert job["attempts"] == 2
    assert converter.calls == 2


def test_reprocess_route_creates_new_job_after_extraction_ready(route_app) -> None:
    converter = CountingConverter()
    route_app.state.source_converter = converter
    client = TestClient(route_app)

    upload = client.post(
        "/api/sources", files={"file": ("reprocess-ready.md", b"# Reprocess\n\nCreate a fresh job.\n", "text/markdown")}
    )
    assert upload.status_code == 201
    source_id = upload.json()["source_document"]["id"]
    first_job = upload.json()["ingestion_job"]
    assert first_job["state"] == "extracting"

    response = client.post(f"/api/sources/{source_id}/reprocess")

    assert response.status_code == 200
    job = response.json()["ingestion_job"]
    assert job["id"] != first_job["id"]
    assert job["state"] == "extracting"
    assert job["attempts"] == 1
    assert converter.calls == 2
    with route_app.state.session_factory() as session:
        jobs = session.scalars(select(IngestionJob)).all()
        assert len(jobs) == 2


def test_upload_marks_job_failed_when_queue_dispatch_unavailable(route_app) -> None:
    def _queue_unavailable(_job_id: str) -> str:
        raise RuntimeError("broker unavailable")

    route_app.state.enqueue_ingestion_job = _queue_unavailable
    client = TestClient(route_app)

    response = client.post(
        "/api/sources", files={"file": ("queue-failure.md", b"# Queue\n\nDispatch fails.\n", "text/markdown")}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "ingestion queue unavailable; job marked failed"
    with route_app.state.session_factory() as session:
        job = session.scalar(select(IngestionJob))
        assert job is not None
        assert get_job_status(job) == "failed"
        assert "queue dispatch failed" in (job.error_message or "")
        source = session.get(SourceDocument, job.source_document_id)
        assert source is not None
        assert source.status == "failed"


def test_reprocess_queue_dispatch_failure_keeps_ready_source_status(route_app) -> None:
    client = TestClient(route_app)
    upload = client.post(
        "/api/sources",
        files={"file": ("reprocess-queue.md", b"# Reprocess\n\nQueue dispatch may fail.\n", "text/markdown")},
    )
    assert upload.status_code == 201
    source_id = upload.json()["source_document"]["id"]

    def _queue_unavailable(_job_id: str) -> str:
        raise RuntimeError("broker unavailable")

    route_app.state.enqueue_ingestion_job = _queue_unavailable

    response = client.post(f"/api/sources/{source_id}/reprocess")

    assert response.status_code == 503
    with route_app.state.session_factory() as session:
        source = session.get(SourceDocument, source_id)
        assert source is not None
        assert source.status == "extraction_ready"
        latest_job = SourceIngestionService(route_app.state.object_store).latest_job_for_source(session, source_id)
        assert latest_job is not None
        assert get_job_status(latest_job) == "failed"
        assert "queue dispatch failed" in (latest_job.error_message or "")


def test_retry_marks_job_failed_when_queue_dispatch_unavailable(route_app) -> None:
    converter = FlakyConverter()
    route_app.state.source_converter = converter
    client = TestClient(route_app)

    upload = client.post(
        "/api/sources", files={"file": ("retry-queue.md", b"# Retry\n\nWill fail first.\n", "text/markdown")}
    )
    assert upload.status_code == 201
    failed_job_id = upload.json()["ingestion_job"]["id"]

    def _queue_unavailable(_job_id: str) -> str:
        raise RuntimeError("broker unavailable")

    route_app.state.enqueue_ingestion_job = _queue_unavailable

    response = client.post(f"/api/admin/ingestion-jobs/{failed_job_id}/retry")

    assert response.status_code == 503
    with route_app.state.session_factory() as session:
        job = session.get(IngestionJob, failed_job_id)
        assert job is not None
        assert get_job_status(job) == "failed"
        assert "queue dispatch failed" in (job.error_message or "")
        source = session.get(SourceDocument, job.source_document_id)
        assert source is not None
        assert source.status == "failed"


def test_cancel_route_rejects_completed_jobs(route_app) -> None:
    client = TestClient(route_app)
    upload = client.post("/api/sources", files={"file": ("cancel.md", b"# Cancel\n\nStop me.\n", "text/markdown")})
    job = upload.json()["ingestion_job"]
    with route_app.state.session_factory() as session:
        persisted_job = session.get(IngestionJob, job["id"])
        assert persisted_job is not None
        persisted_job.status = "completed"
        session.commit()

    response = client.post(f"/api/admin/ingestion-jobs/{job['id']}/cancel")

    assert response.status_code == 409


def test_unsupported_upload_type_returns_415(route_app) -> None:
    client = TestClient(route_app)

    response = client.post("/api/sources", files={"file": ("bundle.zip", b"zip bytes", "application/zip")})

    assert response.status_code == 415


def test_extraction_report_and_relationship_approval_routes(route_app) -> None:
    _seed_source_metadata(route_app)
    with route_app.state.session_factory() as session:
        create_entity(session, preferred_label="Service A", ontology_type="component", domain_id="BSS")
        create_entity(session, preferred_label="Service B", ontology_type="component", domain_id="BSS")
        session.commit()
    client = TestClient(route_app)
    upload = client.post(
        "/api/sources",
        data={"domain_id": "BSS", "category": "Design Document", "sensitivity": "internal"},
        files={"file": ("design.md", b"# Design\n\nService A may depend on Service B.\n", "text/markdown")},
    )
    source_id = upload.json()["source_document"]["id"]
    preview = client.get(f"/api/sources/{source_id}/preview").json()
    element = next(item for item in preview["elements"] if "Service A" in item["text"])

    extraction = client.post(
        f"/api/sources/{source_id}/extractions",
        json={
            "idea_blocks": [
                {
                    "statement": "Service A may depend on Service B.",
                    "category": "Design Document",
                    "type": "relationship",
                    "confidence": "inferred",
                    "source_element_id": element["id"],
                    "source_quote": "Service A may depend on Service B.",
                    "entities": [
                        {"name": "Service A", "kind": "component", "role": "subject"},
                        {"name": "Service B", "kind": "component", "role": "object"},
                    ],
                    "relationships": [
                        {
                            "source": "Service A",
                            "relation_type": "depends_on",
                            "target": "Service B",
                            "confidence": "inferred",
                        }
                    ],
                }
            ]
        },
    )

    assert extraction.status_code == 200
    report = client.get(f"/api/sources/{source_id}/extraction-report")
    assert report.status_code == 200
    assert report.json()["report"]["review_task_count"] == 2

    with route_app.state.session_factory() as session:
        relationship = session.scalar(select(EntityRelationship))
        tasks = session.scalars(select(ReviewTask)).all()
        assert relationship is not None
        assert len(tasks) == 2

    approval = client.post(f"/api/sources/relationships/{relationship.id}/approve")

    assert approval.status_code == 200
    assert approval.json()["status"] == "verified"
    with route_app.state.session_factory() as session:
        tasks = session.scalars(select(ReviewTask)).all()
        assert any(task.target_type == "entity_relationship" and task.status == "closed" for task in tasks)
        assert any(task.target_type == "idea_block" and task.status == "open" for task in tasks)


def test_delete_source_removes_source_relationships_but_keeps_entities(route_app) -> None:
    _seed_source_metadata(route_app)
    client = TestClient(route_app)
    upload = client.post(
        "/api/sources",
        data={"domain_id": "BSS", "category": "Design Document", "sensitivity": "internal"},
        files={"file": ("delete.md", b"# Delete\n\nService A may depend on Service B.\n", "text/markdown")},
    )
    source_id = upload.json()["source_document"]["id"]
    element = next(
        item
        for item in client.get(f"/api/sources/{source_id}/preview").json()["elements"]
        if "Service A" in item["text"]
    )
    with route_app.state.session_factory() as session:
        _ = session.scalar(select(SourceDocumentVersion).where(SourceDocumentVersion.source_document_id == source_id))
        service_a = create_entity(session, preferred_label="Service A", ontology_type="component", domain_id="BSS")
        service_b = create_entity(session, preferred_label="Service B", ontology_type="component", domain_id="BSS")
        block = IdeaBlock(id="ib:source-delete", domain_id="BSS")
        session.add_all([service_a, service_b, block])
        session.flush()
        revision = create_idea_block_revision(
            session,
            block,
            statement="Service A may depend on Service B.",
            normalized_statement="service a may depend on service b.",
            type="relationship",
            confidence="inferred",
            polarity="supports",
            qualifiers={},
            valid_from=None,
            valid_to=None,
            publication_status="candidate",
            actor="test",
            process="manual",
        )
        session.add(IdeaBlockEntity(idea_block_id=block.id, entity_id=service_a.id, role="subject"))
        session.add(IdeaBlockEntity(idea_block_id=block.id, entity_id=service_b.id, role="object"))
        session.add(
            EntityMention(
                id="mention:source-delete",
                idea_block_revision_id=revision.id,
                source_element_id=element["id"],
                domain_id="BSS",
                text="Service A",
                normalized_text="service a",
                proposed_type="component",
            )
        )
        session.add(
            EntityResolutionDecision(
                id="decision:source-delete",
                mention_id="mention:source-delete",
                entity_id=service_a.id,
                outcome="resolved",
                confidence=1.0,
                candidate_entity_ids=[service_a.id],
                method="test",
                status="resolved",
            )
        )
        session.add(
            ClaimEvidence(
                id="ce:source-delete",
                idea_block_id=block.id,
                idea_block_revision_id=revision.id,
                source_element_id=element["id"],
                source_quote="Service A may depend on Service B.",
                polarity="supports",
                confidence="inferred",
                extraction_method="manual",
            )
        )
        session.add(
            EntityRelationship(
                id="rel:source-delete",
                source_entity_id=service_a.id,
                target_entity_id=service_b.id,
                relation_type="depends_on",
                confidence="inferred",
                status="candidate",
            )
        )
        session.add(
            RelationshipProvenance(
                id="rp:source-delete",
                relationship_id="rel:source-delete",
                idea_block_id=block.id,
                source_element_id=element["id"],
                source_quote="Service A may depend on Service B.",
                confidence="inferred",
                extraction_method="manual",
                status="candidate",
            )
        )
        session.add(
            EmbeddingRecord(
                id="emb:stale-source-delete",
                target_type="idea_block_revision",
                target_id=revision.id,
                model_id="test-model",
                index_version="test-index",
                embedding=[0.1] * EMBEDDING_DIMENSIONS,
                content_hash="test-content",
                provider_id="test-provider",
                dimensions=EMBEDDING_DIMENSIONS,
            )
        )
        session.add(
            EmbeddingJob(
                id="embjob:stale-source-delete",
                target_type="idea_block_revision",
                target_id=revision.id,
                model_id="test-model",
                index_version="test-index",
                reason="test",
                status="completed",
            )
        )
        session.commit()

    response = client.delete(f"/api/sources/{source_id}", headers={"x-margin-groups": "margin-admin"})

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    with route_app.state.session_factory() as session:
        assert session.get(SourceDocument, source_id) is None
        assert session.scalar(select(EntityRelationship)) is None
        assert session.scalar(select(IdeaBlock)) is None
        assert session.scalar(select(EntityMention)) is None
        assert session.scalar(select(EntityResolutionDecision)) is None
        assert session.scalar(select(ClaimEvidence)) is None
        assert session.scalar(select(EmbeddingRecord)) is None
        assert session.scalar(select(EmbeddingJob)) is None
        assert len(session.scalars(select(Entity)).all()) == 3


def test_delete_source_removes_queued_job_and_versions(route_app) -> None:
    _seed_upload_scope(route_app, "BSS")
    route_app.state.enqueue_ingestion_job = lambda job_id: f"queued-{job_id}"
    _seed_source_metadata(route_app)
    client = TestClient(route_app)
    upload = client.post(
        "/api/sources",
        data={"domain_id": "BSS", "category": "Design Document", "sensitivity": "internal"},
        files={"file": ("queued.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert upload.status_code == 201
    source_id = upload.json()["source_document"]["id"]
    with route_app.state.session_factory() as session:
        job = session.scalar(select(IngestionJob).where(IngestionJob.source_document_id == source_id))
        assert job is not None
        assert job.status == "received"
        version = SourceDocumentVersion(
            id="version:queued-delete",
            source_document_id=source_id,
            version_sha256="queued-delete-sha",
            converter_name="test",
            converter_version="test",
            converted_object_key=None,
            metadata_={},
            dublin_core={},
        )
        session.add(version)
        element = SourceElement(
            id="element:queued-delete",
            source_version_id=version.id,
            kind="paragraph",
            ordinal=0,
            text="Queued source element",
        )
        session.add(element)
        session.add(
            EmbeddingRecord(
                id="emb:queued-delete",
                target_type="source_element",
                target_id=element.id,
                model_id="test-model",
                index_version="test-index",
                embedding=[0.1] * EMBEDDING_DIMENSIONS,
                content_hash="test-content",
                provider_id="test-provider",
                dimensions=EMBEDDING_DIMENSIONS,
            )
        )
        session.add(
            EmbeddingJob(
                id="embjob:queued-delete",
                target_type="source_element",
                target_id=element.id,
                model_id="test-model",
                index_version="test-index",
                reason="test",
                status="completed",
            )
        )
        job.source_version_id = version.id
        session.commit()

    response = client.delete(f"/api/sources/{source_id}", headers={"x-margin-groups": "margin-admin"})

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    with route_app.state.session_factory() as session:
        assert session.get(SourceDocument, source_id) is None
        assert session.scalar(select(SourceDocumentVersion)) is None
        assert session.scalar(select(IngestionJob)) is None
        assert session.scalar(select(EmbeddingRecord)) is None
        assert session.scalar(select(EmbeddingJob)) is None


def test_delete_source_requires_margin_admin_group(route_app) -> None:
    _seed_source_metadata(route_app)
    client = TestClient(route_app)
    upload = client.post(
        "/api/sources",
        data={"domain_id": "BSS", "category": "Design Document", "sensitivity": "internal"},
        files={"file": ("guarded.md", b"# Guarded\n\nOnly admins delete.\n", "text/markdown")},
    )
    assert upload.status_code == 201
    source_id = upload.json()["source_document"]["id"]

    response = client.delete(f"/api/sources/{source_id}", headers={"x-margin-groups": "architect"})

    assert response.status_code == 403
    assert response.json()["detail"] == "margin-admin group is required to delete sources"
    with route_app.state.session_factory() as session:
        assert session.get(SourceDocument, source_id) is not None
