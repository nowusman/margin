from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from margin_db.model_catalog_models import ModelConfiguration
from margin_db.object_storage import FilesystemObjectStore
from margin_db.source_ingestion import SourceIngestionService, get_job_status, set_job_status
from margin_db.source_models import SourceDocumentVersion, SourceElement, SourcePage, SourcePageOutcome
from margin_ingest.contracts import ConversionResult
from margin_ingest.indexing import compute_ocr_idempotency_key, construct_normalized_document
from margin_schema.ids import source_version_id
from tests.helpers.source_scope import seed_source_scope, upload_scoped_source


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class MixedNativeOcrConverter:
    def __init__(self) -> None:
        self.name = "mixed-converter"
        self.version = "1.0"
        self.calls = 0

    def convert(self, *, filename: str, media_type: str, data: bytes) -> ConversionResult:
        self.calls += 1
        text = data.decode("utf-8")
        doc = construct_normalized_document(
            text, source_name=filename, format=media_type, metadata={"format": "pdf", "page_count": 3}
        )
        return ConversionResult(
            normalized_document=doc,
            converter=self.name,
            converter_version=self.version,
            raw_json={
                "converter": self.name,
                "source_name": filename,
                "page_count": 3,
                "pages": [{"number": 1}, {"number": 2}, {"number": 3}],
            },
        )


def test_mixed_native_ocr_page_handling_and_retry_idempotency(tmp_path, session_factory, monkeypatch) -> None:
    converter = MixedNativeOcrConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    ocr_invoked_pages: list[int] = []

    def fake_extract_document_text(source_bytes, *, model, page_number=None, domain_id=None, sensitivity=None):
        del source_bytes, model, domain_id, sensitivity
        assert page_number is not None
        ocr_invoked_pages.append(page_number)
        return (
            "| Service Target | Error Threshold | Sleep Window |\n"
            "| Payment Gateway | 50% failures | 30 seconds |\n"
            "| Inventory Service | 40% failures | 15 seconds |"
        )

    monkeypatch.setattr("margin_ingest.quality.extract_document_text", fake_extract_document_text)

    sample_mixed_markdown = (
        "%PDF-1.4\n\n"
        "<!-- page: 1 -->\n\n"
        "# Platform Architecture Specification\n\n"
        "The platform architecture utilizes an event-driven event backbone.\n"
        "Native services communicate asynchronously over dedicated topics.\n\n"
        "<!-- page: 3 -->\n\n"
        "# Native Service Quotas\n\n"
        "All ingress gateways must enforce an upstream rate limit of 1000 requests per second.\n"
        "Mixed OCR processing must retain page order and never drop native text pages.\n"
    )

    with session_factory() as session:
        model = ModelConfiguration(
            id="vision-ocr",
            display_name="Vision/OCR model",
            purpose="vision_ocr",
            status="active",
            runtime="test-runtime",
            model_name="test-model",
            endpoint_url="http://127.0.0.1:9/v1",
            secret_reference="env:TEST_SECRET",
            system_prompt="You are an extraction model.",
            max_tokens=4096,
            capabilities={"image_input": True},
            last_test_status="healthy",
        )
        session.add(model)
        session.flush()

        # Initial ingestion
        result = upload_scoped_source(
            service,
            session,
            filename="mixed_doc.pdf",
            media_type="application/pdf",
            data=sample_mixed_markdown.encode("utf-8"),
        )
        job = result.ingestion_job
        assert get_job_status(job) == "extracting"

        # 1. Assert OCR model was invoked ONLY for page 2
        assert ocr_invoked_pages == [2]

        # 2. Assert all 3 pages remain present in order with their respective elements
        version_id = job.source_version_id
        assert version_id is not None
        version = session.get(SourceDocumentVersion, version_id)
        assert version is not None

        pages = list(
            session.scalars(
                select(SourcePage).where(SourcePage.source_version_id == version_id).order_by(SourcePage.page_number)
            ).all()
        )
        assert len(pages) == 3
        assert [p.page_number for p in pages] == [1, 2, 3]

        elements = list(
            session.scalars(
                select(SourceElement)
                .where(SourceElement.source_version_id == version_id)
                .order_by(SourceElement.ordinal)
            ).all()
        )
        assert len(elements) > 0
        # Ordinals must be strictly continuous
        assert [e.ordinal for e in elements] == list(range(len(elements)))

        # Native pages 1 and 3 are present
        p1_texts = [e.text for e in elements if e.page_number == 1 and e.text]
        assert any("event-driven event backbone" in t for t in p1_texts)

        # Scanned page 2 has OCR elements
        p2_texts = [e.text for e in elements if e.page_number == 2 and e.text]
        assert any("Payment Gateway" in t for t in p2_texts)

        p3_texts = [e.text for e in elements if e.page_number == 3 and e.text]
        assert any("upstream rate limit of 1000 requests" in t for t in p3_texts)

        # 3. Assert SourcePageOutcome records are persisted for all 3 pages
        outcomes = list(
            session.scalars(
                select(SourcePageOutcome).where(SourcePageOutcome.source_page_id.in_([p.id for p in pages]))
            ).all()
        )
        outcomes_by_page_id = {o.source_page_id: o for o in outcomes}
        assert len(outcomes) == 3

        p2_outcome = outcomes_by_page_id[pages[1].id]
        assert p2_outcome.outcome == "extracted"
        assert p2_outcome.processor == "vision-ocr"
        assert p2_outcome.attempt_count == 1
        assert p2_outcome.idempotency_key is not None
        assert p2_outcome.result is not None
        assert "Payment Gateway" in p2_outcome.result

        target_version_id = source_version_id(
            {
                "source_document_id": result.source_document.id,
                "version_sha256": result.source_document.sha256,
                "converter": converter.name,
                "converter_version": converter.version,
            }
        )
        expected_p2_key = compute_ocr_idempotency_key(
            source_version_id=target_version_id,
            page_number=2,
            asset_checksum=result.source_document.sha256,
            processor_model_id="vision-ocr",
            processor_version="test-model",
        )
        assert p2_outcome.idempotency_key == expected_p2_key

        # Native page outcomes
        p1_outcome = outcomes_by_page_id[pages[0].id]
        assert p1_outcome.outcome == "extracted"
        assert p1_outcome.attempt_count == 1

        # 4. Retry idempotency test:
        # Reset OCR calls counter; mark job failed to allow retry; call retry_job
        ocr_invoked_pages.clear()
        set_job_status(job, "failed", error_message="intermittent downstream error")
        session.flush()

        retried_job = service.retry_job(session, job.id)
        assert get_job_status(retried_job) == "extracting"

        # Assert OCR model was NOT invoked on retry (cached idempotency result was reused)
        assert ocr_invoked_pages == []

        # Assert pages and elements remain present with identical IDs and counts
        retried_pages = list(
            session.scalars(
                select(SourcePage).where(SourcePage.source_version_id == version_id).order_by(SourcePage.page_number)
            ).all()
        )
        assert [p.id for p in retried_pages] == [p.id for p in pages]

        retried_elements = list(
            session.scalars(
                select(SourceElement)
                .where(SourceElement.source_version_id == version_id)
                .order_by(SourceElement.ordinal)
            ).all()
        )
        assert [e.id for e in retried_elements] == [e.id for e in elements]


def test_ocr_retry_policy_exhaustion_on_repeated_failures(tmp_path, session_factory, monkeypatch) -> None:
    converter = MixedNativeOcrConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    def failing_extract(source_bytes, *, model, page_number=None, domain_id=None, sensitivity=None):
        del source_bytes, model, page_number, domain_id, sensitivity
        raise RuntimeError("OCR service temporarily unavailable")

    monkeypatch.setattr("margin_ingest.quality.extract_document_text", failing_extract)

    sample_mixed_markdown = (
        "%PDF-1.4\n\n"
        "<!-- page: 1 -->\n\n"
        "# Page 1 Architecture Decisions\n\n"
        "Native service specifications and event-driven backbone requirements.\n\n"
        "<!-- page: 3 -->\n\n"
        "# Page 3 Service Quotas\n\n"
        "Native ingress gateway limits and quota definitions.\n"
    )

    with session_factory() as session:
        model = ModelConfiguration(
            id="vision-ocr",
            display_name="Vision/OCR model",
            purpose="vision_ocr",
            status="active",
            runtime="test-runtime",
            model_name="test-model",
            endpoint_url="http://127.0.0.1:9/v1",
            secret_reference="env:TEST_SECRET",
            system_prompt="You are an extraction model.",
            max_tokens=4096,
            capabilities={"image_input": True},
            last_test_status="healthy",
        )
        session.add(model)
        session.flush()

        result = upload_scoped_source(
            service,
            session,
            filename="failing_ocr.pdf",
            media_type="application/pdf",
            data=sample_mixed_markdown.encode("utf-8"),
        )
        job = result.ingestion_job
        assert get_job_status(job) == "failed"
        assert "OCR service temporarily unavailable" in (job.error_message or "")

        # Retries 1 and 2 fail
        service.retry_job(session, job.id)
        assert get_job_status(job) == "failed"

        service.retry_job(session, job.id)
        assert get_job_status(job) == "failed"

        # Retry 3 exceeds max_retries
        service.retry_job(session, job.id)
        assert get_job_status(job) == "failed"
        assert "retry policy exhausted" in (job.error_message or "")
