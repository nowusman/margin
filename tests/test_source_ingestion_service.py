from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from margin_db.knowledge_models import EmbeddingJob
from margin_db.model_catalog_models import ModelConfiguration
from margin_db.object_storage import FilesystemObjectStore, sha256_bytes
from margin_db.review_models import ReviewTask
from margin_db.source_indexing import IndexedSection, SourceIndex
from margin_db.source_ingestion import (
    IngestionJob,
    RetryNotAllowedError,
    SourceIngestionError,
    SourceIngestionService,
    UnsupportedSourceTypeError,
    _validate_vision_ocr_request,
    get_job_status,
    job_to_dict,
    set_job_status,
    validate_source_type,
)
from margin_db.source_models import (
    DUBLIN_CORE_FIELDS,
    ConversionQualityReport,
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourceElementOutcome,
    SourcePage,
    UnmappedExtraction,
)
from margin_inference import InferenceClient, InferenceResponse, InferenceUsage
from margin_ingest.contracts import ConversionResult, NormalizedDocument, NormalizedElement, NormalizedPage, VisualAsset
from margin_ingest.indexing import construct_normalized_document
from tests.helpers.source_scope import seed_source_scope, upload_scoped_source


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@dataclass
class CountingConverter:
    calls: int = 0

    def convert(self, *, filename: str, media_type: str, data: bytes) -> ConversionResult:
        self.calls += 1
        text = data.decode("utf-8")
        return ConversionResult(
            normalized_document=construct_normalized_document(
                text, source_name=filename, format=media_type, metadata={"filename": filename, "media_type": media_type}
            ),
            converter="counting-converter",
            converter_version="test",
        )


class FlakyConverter:
    def __init__(self) -> None:
        self.calls = 0

    def convert(self, *, filename: str, media_type: str, data: bytes) -> ConversionResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("conversion failed once")
        text = data.decode("utf-8")
        return ConversionResult(
            normalized_document=construct_normalized_document(text, source_name=filename, format=media_type),
            converter="flaky-converter",
            converter_version="test",
        )


@dataclass
class VisualConverter:
    asset: bytes
    calls: int = 0

    def convert(self, *, filename: str, media_type: str, data: bytes) -> ConversionResult:
        del data
        self.calls += 1
        asset_key = sha256_bytes(self.asset)
        page_text = "# Visual source\n\nA chart requires OCR.\n"
        element = NormalizedElement(
            element_id="chart-1", kind="chart", text="", ordinal=0, page_number=1, asset_key=asset_key
        )
        return ConversionResult(
            normalized_document=NormalizedDocument(
                source_name=filename,
                format=media_type,
                text=page_text,
                pages=(
                    NormalizedPage(
                        page_number=1, checksum=sha256_bytes(page_text.encode()), text=page_text, elements=(element,)
                    ),
                ),
                elements=(element,),
                visual_assets=(
                    VisualAsset(
                        asset_id="chart-1",
                        asset_key=asset_key,
                        media_type="image/png",
                        checksum=asset_key,
                        data=self.asset,
                    ),
                ),
            ),
            converter="visual-converter",
            converter_version="test",
        )


class EmptySpanSourceIndex:
    def build(self, text: str, *, default_heading: str, metadata: dict | None = None):
        del metadata
        del text
        return (
            (
                IndexedSection(
                    source_tree_node_id="empty",
                    parent_source_tree_node_id=None,
                    heading=default_heading,
                    section_path=default_heading,
                    start_offset=0,
                    end_offset=0,
                    order_index=0,
                    spans=(),
                ),
            ),
            {
                "source_index_adapter": "empty-test-index",
                "source_index_behavior": "test empty spans",
                "text_coverage": 0,
                "table_coverage": 0,
                "section_count": 1,
                "span_count": 0,
                "warnings": ["no citable spans"],
            },
        )


def test_process_job_uses_vision_ocr_when_document_text_extraction_is_required(
    tmp_path, session_factory, monkeypatch
) -> None:
    from margin_db.model_catalog_models import ModelConfiguration

    converter = CountingConverter()
    service = SourceIngestionService(
        FilesystemObjectStore(tmp_path / "objects"), converter=converter, source_index=SourceIndex()
    )
    monkeypatch.setattr(
        "margin_ingest.quality.extract_document_text",
        lambda source_bytes, *, model, page_number, domain_id=None, sensitivity=None: (
            "Primary model recovered text.\n\n## Recovered Section\n\nPostgreSQL stores source spans."
        ),
    )

    with session_factory() as session:
        session.add(
            ModelConfiguration(
                id="vision-ocr",
                display_name="Vision/OCR model",
                purpose="vision_ocr",
                runtime="openai-compatible",
                model_name="minimax-m3:cloud",
                endpoint_url="http://host.docker.internal:11434/v1",
                system_prompt="Margin governed vision/OCR extraction engine",
                max_tokens=4096,
                capabilities={"image_input": True},
                status="active",
                last_test_status="healthy",
            )
        )
        session.commit()
        result = upload_scoped_source(
            service,
            session,
            filename="scan.pdf",
            media_type="application/pdf",
            data=b"",
            domain_id="BSS",
            category="Design Document",
            sensitivity="internal",
        )
        session.commit()

        assert result.source_document.status == "extraction_ready"
        job = job_to_dict(result.ingestion_job)
        assert job["quality_report"]["vision_ocr_text_extraction_applied"] is True
        version = session.scalar(select(SourceDocumentVersion))
        assert version is not None
        assert version.metadata_["vision_ocr_text_extraction_applied"] is True
        elements = session.scalars(select(SourceElement)).all()
        assert elements
        assert any("PostgreSQL" in (element.text or "") for element in elements)


def test_process_job_fails_fast_when_vision_ocr_model_is_unreachable(tmp_path, session_factory, monkeypatch) -> None:
    from margin_db.model_catalog_models import ModelConfiguration
    from workers.extract.inference import InferenceUnavailableError

    converter = CountingConverter()
    service = SourceIngestionService(
        FilesystemObjectStore(tmp_path / "objects"), converter=converter, source_index=SourceIndex()
    )

    def unreachable(source_bytes, *, model, page_number, domain_id=None, sensitivity=None):
        del source_bytes, model, page_number, domain_id, sensitivity
        raise InferenceUnavailableError("inference request failed")

    monkeypatch.setattr("margin_ingest.quality.extract_document_text", unreachable)

    with session_factory() as session:
        session.add(
            ModelConfiguration(
                id="vision-ocr",
                display_name="Vision/OCR model",
                purpose="vision_ocr",
                runtime="openai-compatible",
                model_name="minimax-m3:cloud",
                endpoint_url="http://host.docker.internal:11434/v1",
                system_prompt="Margin governed vision/OCR extraction engine",
                max_tokens=4096,
                capabilities={"image_input": True},
                status="active",
                last_test_status="healthy",
            )
        )
        session.commit()
        result = upload_scoped_source(
            service,
            session,
            filename="scan.pdf",
            media_type="application/pdf",
            data=b"",
            domain_id="BSS",
            category="Design Document",
            sensitivity="internal",
        )
        session.commit()

        job = job_to_dict(result.ingestion_job)
        assert job["state"] == "failed"
        assert "inference request failed" in job["error_message"]
        assert result.source_document.status == "failed"


def test_vision_ocr_rejects_too_many_ocr_pages(monkeypatch) -> None:
    monkeypatch.setenv("MARGIN_LLM_OCR_MAX_PAGES", "2")

    with pytest.raises(SourceIngestionError, match="exceeds MARGIN_LLM_OCR_MAX_PAGES=2"):
        _validate_vision_ocr_request(page_numbers=[1, 2, 3], source_byte_count=100)


def test_vision_ocr_rejects_large_source(monkeypatch) -> None:
    monkeypatch.setenv("MARGIN_LLM_OCR_MAX_SOURCE_BYTES", "10")

    with pytest.raises(SourceIngestionError, match="exceeds MARGIN_LLM_OCR_MAX_SOURCE_BYTES=10"):
        _validate_vision_ocr_request(page_numbers=[1], source_byte_count=11)


def test_process_job_enqueues_source_element_embeddings(tmp_path, session_factory) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        upload_scoped_source(
            service,
            session,
            filename="embeddable.md",
            media_type="text/markdown",
            data=b"# Overview\n\nMargin embeds source spans.\n",
            source_uri="https://example.test/embeddable.md",
            domain_id="BSS",
            category="Design Document",
            sensitivity="internal",
        )
        session.commit()

        jobs = list(session.scalars(select(EmbeddingJob)))
        assert {(job.target_type, job.status) for job in jobs} == {("source_element", "queued")}

        first_job_count = len(jobs)
        upload_scoped_source(
            service,
            session,
            filename="embeddable.md",
            media_type="text/markdown",
            data=b"# Overview\n\nMargin embeds source spans.\n",
            source_uri="https://example.test/embeddable.md",
            domain_id="BSS",
            category="Design Document",
            sensitivity="internal",
        )
        session.commit()
        assert len(list(session.scalars(select(EmbeddingJob)))) == first_job_count


def test_upload_creates_document_version_sections_spans_quality_and_job(tmp_path, session_factory) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)
    data = b"# Overview\n\nMargin stores source spans in PostgreSQL.\n"

    with session_factory() as session:
        result = upload_scoped_source(
            service,
            session,
            filename="hld.md",
            media_type="text/markdown",
            data=data,
            domain_id="BSS",
            category="Design Document",
            sensitivity="internal",
        )
        session.commit()

        assert result.cached is False
        assert result.source_document.status == "extraction_ready"
        job = job_to_dict(result.ingestion_job)
        assert job is not None
        assert job["state"] == "extracting"
        assert job["quality_report"]["text_coverage"] > 0
        assert job["quality_report"]["converter"] == "counting-converter"
        assert converter.calls == 1
        assert session.scalar(select(SourceDocumentVersion)) is not None
        assert session.scalar(select(SourcePage)) is not None
        element = session.scalar(select(SourceElement).where(SourceElement.kind == "paragraph"))
        assert element is not None
        assert "PostgreSQL" in (element.text or "")
        elements = list(session.scalars(select(SourceElement)).all())
        assert elements


def test_heading_only_document_produces_a_citable_source_element(tmp_path, session_factory) -> None:
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=CountingConverter())

    with session_factory() as session:
        upload_scoped_source(
            service, session, filename="heading.md", media_type="text/markdown", data=b"# Architecture Principles\n"
        )
        element = session.scalar(select(SourceElement).where(SourceElement.kind == "heading"))

        assert element is not None and element.text == "# Architecture Principles"


def test_repeated_paragraphs_map_to_distinct_occurrences(tmp_path, session_factory) -> None:
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=CountingConverter())

    with session_factory() as session:
        upload_scoped_source(
            service,
            session,
            filename="repeated.md",
            media_type="text/markdown",
            data=b"# Repeated\n\nSame evidence.\n\nSame evidence.\n",
        )
        elements = list(session.scalars(select(SourceElement).where(SourceElement.kind == "paragraph")))

        assert len(elements) == 2
        assert len({element.id for element in elements}) == 2
        assert all(element.text == "Same evidence." for element in elements)


def test_source_ingestion_logs_upload_and_process_interactions(
    tmp_path, session_factory, caplog: pytest.LogCaptureFixture
) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        caplog.set_level("INFO", logger="margin_db.source_ingestion")
        upload_scoped_source(
            service,
            session,
            filename="logged.md",
            media_type="text/markdown",
            data=b"# Logged\n\nSource ingestion is logged.\n",
            domain_id="BSS",
            category="Design Document",
            sensitivity="internal",
        )

    interactions = [
        record.source_ingestion_interaction
        for record in caplog.records
        if hasattr(record, "source_ingestion_interaction")
    ]
    events = {interaction["event"] for interaction in interactions}
    assert {"upload_source", "process_job"} <= events
    upload = next(interaction for interaction in interactions if interaction["event"] == "upload_source")
    process = next(interaction for interaction in interactions if interaction["event"] == "process_job")
    assert upload["request"]["filename"] == "logged.md"
    assert upload["request"]["size_bytes"] == len(b"# Logged\n\nSource ingestion is logged.\n")
    assert "data" not in upload["request"]
    assert upload["response"]["status"] == "extraction_ready"
    assert process["response"]["status"] == "extracting"
    assert process["start_timestamp"]
    assert process["end_timestamp"]
    messages = [record.getMessage() for record in caplog.records]
    assert any('"start_timestamp"' in message for message in messages)
    assert any('"response"' in message for message in messages)


def test_upload_source_file_streams_object_and_records_document(tmp_path, session_factory) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)
    source_path = tmp_path / "large.md"
    source_path.write_text("# Overview\n\nMargin streams uploads to object storage.\n")

    with session_factory() as session:
        result = service.upload_source_file(
            session,
            filename="large.md",
            media_type="text/markdown",
            path=source_path,
            domain_id="BSS",
            category="Design Document",
            sensitivity="internal",
        )
        session.commit()

        assert result.cached is False
        assert result.source_document.size_bytes == source_path.stat().st_size
        assert service.object_store.get_bytes(result.source_document.sha256) == source_path.read_bytes()
        assert converter.calls == 1


def test_uploads_without_source_uri_create_independent_documents(tmp_path, session_factory) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)
    data = b"# Same\n\nSame uploaded bytes.\n"

    with session_factory() as session:
        first = upload_scoped_source(service, session, filename="same.md", media_type="text/markdown", data=data)
        second = upload_scoped_source(service, session, filename="same-again.md", media_type="text/markdown", data=data)
        session.commit()

        assert second.cached is False
        assert second.source_document.id != first.source_document.id
        assert first.source_document.sha256 == second.source_document.sha256
        assert converter.calls == 2
        assert len(session.scalars(select(SourceDocumentVersion)).all()) == 2


def test_logical_source_identity_is_domain_scoped_and_metadata_is_independent(tmp_path, session_factory) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)
    data = b"# Shared bytes\n\nDomain-scoped source.\n"

    with session_factory() as session:
        first = service.upload_source(
            session,
            filename="shared.md",
            source_uri="https://example.test/shared.md",
            media_type="text/markdown",
            data=data,
            domain_id="OSS",
            category="Design Document",
            sensitivity="internal",
            owner="owner-a",
        )
        second = service.upload_source(
            session,
            filename="shared.md",
            source_uri="https://example.test/shared.md",
            media_type="text/markdown",
            data=data,
            domain_id="BSS",
            category="Design Document",
            sensitivity="confidential",
            owner="owner-b",
        )

        assert first.source_document.id != second.source_document.id
        assert first.source_document.sha256 == second.source_document.sha256
        assert first.source_document.sensitivity == "internal"
        assert second.source_document.sensitivity == "confidential"
        assert first.source_document.owner == "owner-a"
        assert second.source_document.owner == "owner-b"


def test_stable_source_uri_is_idempotent_and_changed_bytes_create_a_version(tmp_path, session_factory) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        first = upload_scoped_source(
            service,
            session,
            filename="stable.md",
            source_uri="HTTPS://EXAMPLE.TEST/stable.md#first",
            media_type="text/markdown",
            data=b"# Stable\n\nVersion one.\n",
            owner="original-owner",
            metadata={"source_language": "en", "extraction_mode": "deterministic"},
        )
        cached = upload_scoped_source(
            service,
            session,
            filename="renamed.md",
            source_uri="https://example.test/stable.md#second",
            media_type="text/markdown",
            data=b"# Stable\n\nVersion one.\n",
            owner="different-owner",
            sensitivity="confidential",
            metadata={"source_language": "ar", "extraction_mode": "llm"},
        )
        changed = upload_scoped_source(
            service,
            session,
            filename="stable.md",
            source_uri="https://example.test/stable.md",
            media_type="text/markdown",
            data=b"# Stable\n\nVersion two.\n",
        )

        assert cached.cached is True
        assert cached.source_document.id == first.source_document.id
        assert cached.source_document.owner == "original-owner"
        assert cached.source_document.sensitivity == "internal"
        assert cached.source_document.metadata_["source_language"] == "en"
        assert changed.cached is False
        assert changed.source_document.id == first.source_document.id
        versions = list(
            session.scalars(
                select(SourceDocumentVersion).where(
                    SourceDocumentVersion.source_document_id == first.source_document.id
                )
            )
        )
        assert len(versions) == 2
        assert sum(version.status == "active" for version in versions) == 1


def test_reused_visual_version_retries_quarantined_vision_processing(tmp_path, session_factory, monkeypatch) -> None:
    asset = Path("tests/fixtures/source_ingestion/sample.png").read_bytes()
    converter = VisualConverter(asset)
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        first = upload_scoped_source(
            service, session, filename="visual.md", media_type="text/markdown", data=b"visual source"
        )
        first_version_id = first.ingestion_job.source_version_id
        element = session.scalar(select(SourceElement))
        assert element is not None
        assert element.metadata_["quarantined"] is True

        content = (
            '{"region_id":"chart-1","element_kind":"chart","transcription":"Revenue",'
            '"factual_description":"A revenue chart.","candidate_assertions":[],"confidence":0.9}'
        )

        def fake_chat_completions(self, messages, **kwargs):
            return InferenceResponse(
                content=content,
                usage=InferenceUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                raw_response={"choices": [{"message": {"content": content}}]},
            )

        monkeypatch.setattr(InferenceClient, "chat_completions", fake_chat_completions)
        session.add(
            ModelConfiguration(
                id="vision-retry-model",
                display_name="Vision retry model",
                purpose="vision_ocr",
                runtime="openai-compatible",
                model_name="vision-retry",
                endpoint_url="http://provider.test/v1",
                capabilities={"image_input": True, "supported_mime_types": ["image/png"]},
                status="active",
                last_test_status="healthy",
            )
        )
        first.ingestion_job.status = "received"
        processed = service.process_job(session, first.ingestion_job.id)
        session.flush()

        assert processed.source_version_id == first_version_id
        assert converter.calls == 2
        assert element.metadata_["quarantined"] is False
        task = session.scalar(select(ReviewTask).where(ReviewTask.target_id == element.id))
        assert task is not None
        assert task.status == "closed"
        outcome = session.scalar(
            select(SourceElementOutcome).where(SourceElementOutcome.source_element_id == element.id)
        )
        assert outcome is not None
        assert outcome.outcome == "extracted"
        assert (
            session.scalar(select(UnmappedExtraction).where(UnmappedExtraction.source_element_id == element.id)) is None
        )
        quality = session.scalar(
            select(ConversionQualityReport).where(ConversionQualityReport.source_version_id == first_version_id)
        )
        assert quality is not None
        assert quality.metadata_["visual_processing"] == {"processed": 1, "success": 1, "quarantined": 0}


def test_visual_asset_change_creates_new_immutable_version(tmp_path, session_factory) -> None:
    first_asset = Path("tests/fixtures/source_ingestion/sample.png").read_bytes()
    converter = VisualConverter(first_asset)
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        result = upload_scoped_source(
            service, session, filename="visual.md", media_type="text/markdown", data=b"visual source"
        )
        first_version_id = result.ingestion_job.source_version_id

        converter.asset = b"different visual asset"
        result.ingestion_job.status = "received"
        service.process_job(session, result.ingestion_job.id)

        versions = session.scalars(select(SourceDocumentVersion).order_by(SourceDocumentVersion.created_at)).all()
        assert len(versions) == 2
        assert first_version_id in {version.id for version in versions}
        assert result.ingestion_job.source_version_id != first_version_id
        active_version = session.get(SourceDocumentVersion, result.ingestion_job.source_version_id)
        assert active_version is not None
        assert active_version.status == "active"


def test_reprocessing_source_replaces_deterministic_version_and_quality_report(tmp_path, session_factory) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        result = upload_scoped_source(
            service,
            session,
            filename="reprocess.md",
            media_type="text/markdown",
            data=b"# Reprocess\n\nStable bytes.\n",
        )
        first_version_id = session.scalar(select(SourceDocumentVersion.id))

        result.ingestion_job.status = "received"
        service.process_job(session, result.ingestion_job.id)
        second_version_id = session.scalar(select(SourceDocumentVersion.id))
        quality_reports = session.scalars(select(ConversionQualityReport)).all()

        assert first_version_id == second_version_id
        assert len(quality_reports) == 1
        assert converter.calls == 2


def test_ingestion_source_ids_are_deterministic_across_empty_databases(tmp_path, session_factory) -> None:
    data = b"# Stable\n\nStable source identifiers.\n"
    observed: list[tuple[str, str, str, str]] = []

    for index in range(2):
        converter = CountingConverter()
        service = SourceIngestionService(FilesystemObjectStore(tmp_path / f"objects-{index}"), converter=converter)
        with session_factory() as session:
            upload_scoped_source(
                service,
                session,
                filename="stable.md",
                media_type="text/markdown",
                data=data,
                source_uri="https://example.test/stable.md",
            )
            document = session.scalar(select(SourceDocument))
            version = session.scalar(select(SourceDocumentVersion))
            page = session.scalar(select(SourcePage))
            element = session.scalar(select(SourceElement))

            assert document is not None
            assert version is not None
            assert page is not None
            assert element is not None
            observed.append((document.id, version.id, page.id, element.id))

    assert observed[0] == observed[1]
    assert observed[0][0].startswith("src_")
    assert observed[0][1].startswith("srcv_")
    assert observed[0][2].startswith("page_")
    assert observed[0][3].startswith("elem_")


def test_failed_conversion_marks_job_failed_and_retry_reprocesses(tmp_path, session_factory) -> None:
    converter = FlakyConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        result = upload_scoped_source(
            service, session, filename="retry.md", media_type="text/markdown", data=b"# Retry\n\nRetry succeeds.\n"
        )
        assert get_job_status(result.ingestion_job) == "failed"
        assert result.source_document.status == "failed"
        assert result.ingestion_job.error_message == "conversion failed once"
        failed_report = job_to_dict(result.ingestion_job)["quality_report"]
        assert failed_report["status"] == "failed"
        assert failed_report["converter"] == "conversion_failed"
        assert failed_report["failure_reason"] == "conversion failed once"

        retried = service.retry_job(session, result.ingestion_job.id)
        session.commit()

        retried_dict = job_to_dict(retried)
        assert retried_dict is not None
        assert retried_dict["state"] == "extracting"
        assert retried_dict["attempts"] == 2
        assert converter.calls == 2
        assert session.scalar(select(SourceDocumentVersion)) is not None


def test_ingestion_fails_without_citable_sections_and_spans(tmp_path, session_factory) -> None:
    service = SourceIngestionService(
        FilesystemObjectStore(tmp_path / "objects"), converter=CountingConverter(), source_index=EmptySpanSourceIndex()
    )

    with session_factory() as session:
        result = upload_scoped_source(
            service, session, filename="empty.md", media_type="text/markdown", data=b"# Empty\n"
        )
        session.commit()

        job = job_to_dict(result.ingestion_job)
        assert job is not None
        assert result.source_document.status == "failed"
        assert job["state"] == "failed"
        assert job["quality_report"]["failure_reason"] == "source conversion did not produce citable elements"
        assert session.scalar(select(SourceDocumentVersion)) is None


def test_retry_rejects_non_failed_job(tmp_path, session_factory) -> None:
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"))

    with session_factory() as session:
        result = upload_scoped_source(
            service, session, filename="ready.txt", media_type="text/plain", data=b"plain text"
        )

        with pytest.raises(RetryNotAllowedError):
            service.retry_job(session, result.ingestion_job.id)


def test_reprocess_replaces_version_without_breaking_ingestion_job_foreign_keys(tmp_path) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    local_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with local_session_factory() as session:
        result = upload_scoped_source(
            service,
            session,
            filename="fk-reprocess.md",
            media_type="text/markdown",
            data=b"# Ready\n\nKeep job version references valid.\n",
        )
        first_job_id = result.ingestion_job.id
        first_version_id = result.ingestion_job.source_version_id

        reprocess_job = service.requeue_or_create_reprocess_job(session, result.source_document.id)
        processed = service.process_job(session, reprocess_job.id)
        session.commit()

        first_job = session.get(IngestionJob, first_job_id)
        assert first_job is not None
        assert first_job.source_version_id == first_version_id
        assert processed.source_version_id == first_version_id


def test_reprocess_after_extraction_starts_creates_a_received_job(tmp_path, session_factory) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        result = upload_scoped_source(
            service,
            session,
            filename="ready-reprocess.md",
            media_type="text/markdown",
            data=b"# Ready\n\nReprocess this source.\n",
            domain_id="BSS",
            category="Design Document",
            sensitivity="internal",
        )
        first_job_id = result.ingestion_job.id

        reprocess_job = service.requeue_or_create_reprocess_job(session, result.source_document.id)

        assert reprocess_job.id != first_job_id
        assert get_job_status(reprocess_job) == "received"
        assert converter.calls == 1

        processed = service.process_job(session, reprocess_job.id)
        session.commit()

        jobs = session.scalars(select(IngestionJob).order_by(IngestionJob.created_at.desc())).all()

        assert get_job_status(processed) == "extracting"
        assert converter.calls == 2
        assert len(jobs) == 2
        assert service.latest_job_for_source(session, result.source_document.id).id == reprocess_job.id


def test_reprocess_after_exhausted_failed_job_creates_new_received_job(tmp_path, session_factory) -> None:
    converter = FlakyConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        result = upload_scoped_source(
            service,
            session,
            filename="exhausted.md",
            media_type="text/markdown",
            data=b"# Failed\n\nTry again later.\n",
            domain_id="BSS",
            category="Design Document",
            sensitivity="internal",
        )
        failed_job = service.process_job(session, result.ingestion_job.id)
        failed_job.retry_count = failed_job.max_retries
        session.flush()

        reprocess_job = service.requeue_or_create_reprocess_job(session, result.source_document.id)

        assert reprocess_job.id != failed_job.id
        assert get_job_status(reprocess_job) == "received"
        assert service.latest_job_for_source(session, result.source_document.id).id == reprocess_job.id


def test_process_job_skips_duplicate_after_extraction_starts(tmp_path, session_factory) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        result = upload_scoped_source(
            service,
            session,
            filename="architecture.md",
            media_type="text/markdown",
            data=b"# Decision\n\nUse PostgreSQL.\n",
        )
        first_job_status = get_job_status(result.ingestion_job)

        duplicate = service.process_job(session, result.ingestion_job.id)

        assert first_job_status == "extracting"
        assert get_job_status(duplicate) == "extracting"
        assert converter.calls == 1


def test_process_job_resumes_converting_job_after_worker_restart(tmp_path, session_factory) -> None:
    converter = CountingConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        result = upload_scoped_source(
            service, session, filename="resume.md", media_type="text/markdown", data=b"# Resume\n\nContinue safely.\n"
        )
        source_version_id = result.ingestion_job.source_version_id
        result.ingestion_job.status = "converting"

        resumed = service.process_job(session, result.ingestion_job.id)

        assert get_job_status(resumed) == "extracting"
        assert resumed.source_version_id == source_version_id
        assert converter.calls == 2


def test_preview_returns_tree_sections_spans_and_raw_text(tmp_path, session_factory) -> None:
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"))
    data = b"# Context\n\nEvidence paragraph.\n\n# Decision\n\nUse local conversion.\n"

    with session_factory() as session:
        result = upload_scoped_source(service, session, filename="adr.md", media_type="text/markdown", data=data)

        preview = service.preview(session, result.source_document.id)

        assert preview["source_document"]["id"] == result.source_document.id
        assert len(preview["elements"]) == 4
        assert len(preview["pages"]) >= 1
        assert any(element["text"] == "Use local conversion." for element in preview["elements"])
        assert preview["raw_extracted_text"] == data.decode("utf-8")


def test_manual_metadata_correction_updates_before_extraction_phase(tmp_path, session_factory) -> None:
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"))

    with session_factory() as session:
        result = upload_scoped_source(
            service,
            session,
            filename="metadata.md",
            media_type="text/markdown",
            data=b"# Metadata\n\nNeeds classification.\n",
        )
        document = service.update_metadata(
            session,
            result.source_document.id,
            domain_id="OSS",
            category="Architecture Standards",
            sensitivity="confidential",
        )

        assert document.domain_id == "OSS"
        assert document.category == "Architecture Standards"
        assert document.sensitivity == "confidential"


def test_default_ingestion_uses_source_ingestion_converter_metadata_local_source_tree_and_quality(
    tmp_path, session_factory
) -> None:
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"))
    data = Path("tests/fixtures/source_ingestion/architecture.md").read_bytes()

    with session_factory() as session:
        result = upload_scoped_source(
            service, session, filename="architecture.md", media_type="text/markdown", data=data
        )
        session.commit()

        document = result.source_document
        assert document.title == "Customer Platform HLD"
        assert document.metadata_["source_metadata"]["document_type"] == "design_document"
        assert document.dublin_core["title"] == "Customer Platform HLD"
        assert set(document.dublin_core) == set(DUBLIN_CORE_FIELDS)

        version = session.scalar(select(SourceDocumentVersion))
        assert version is not None
        assert version.converter_name in {"docling-primary", "lightweight-source"}
        assert version.dublin_core["type"] == "design_document"
        assert set(version.dublin_core) == set(DUBLIN_CORE_FIELDS)

        pages = session.scalars(select(SourcePage)).all()
        elements = session.scalars(select(SourceElement)).all()
        assert pages
        assert {"paragraph", "list_item", "table_header", "table_row", "figure"}.issubset(
            {element.kind for element in elements}
        )

        job = job_to_dict(result.ingestion_job)
        assert job is not None
        assert job["quality_report"]["source_index_adapter"] == "local-source-tree-deterministic"
        assert job["quality_report"]["source_index_behavior"] == "deterministic local source tree"
        assert job["quality_report"]["section_tree_confidence"] > 0
        assert job["quality_report"]["span_count"] == len(elements)


def test_upload_rejects_unsupported_extension(tmp_path, session_factory) -> None:
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"))

    with session_factory() as session:
        with pytest.raises(UnsupportedSourceTypeError):
            upload_scoped_source(
                service, session, filename="archive.zip", media_type="application/zip", data=b"not allowed"
            )


@pytest.mark.parametrize(
    ("filename", "expected_media_type"),
    [
        ("source.pdf", "application/pdf"),
        ("slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("brief.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("design.md", "text/markdown"),
        ("note.txt", "text/plain"),
        ("service.openapi.yaml", "application/yaml"),
        ("openapi.json", "application/json"),
        ("events.asyncapi.yml", "application/yaml"),
        ("system.dsl", "text/vnd.structurizr"),
        ("flow.mmd", "text/vnd.mermaid"),
        ("diagram.mermaid", "text/vnd.mermaid"),
        ("deployment.puml", "text/vnd.plantuml"),
        ("deployment.plantuml", "text/vnd.plantuml"),
    ],
)
def test_source_ingestion_upload_type_validator_covers_all_required_formats(
    filename: str, expected_media_type: str
) -> None:
    assert validate_source_type(filename, None) == expected_media_type


def test_admin_visibility_lists_failed_jobs(tmp_path, session_factory) -> None:
    converter = FlakyConverter()
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"), converter=converter)

    with session_factory() as session:
        upload_scoped_source(
            service, session, filename="failed.md", media_type="text/markdown", data=b"# Failed\n\nFirst pass fails.\n"
        )

        failed_jobs = service.list_jobs(session, state="failed")

        assert len(failed_jobs) == 1
        assert isinstance(failed_jobs[0], IngestionJob)


def test_extracting_job_can_be_marked_failed_for_pipeline_failure(tmp_path, session_factory) -> None:
    service = SourceIngestionService(FilesystemObjectStore(tmp_path / "objects"))

    with session_factory() as session:
        result = upload_scoped_source(
            service,
            session,
            filename="architecture.md",
            media_type="text/markdown",
            data=b"# Architecture\n\nOrder API depends on Kafka.\n",
        )
        job = result.ingestion_job
        assert get_job_status(job) == "extracting"

        set_job_status(job, "failed", error_message="pipeline failed: compile failed")
        result.source_document.status = "failed"
        session.flush()

        assert get_job_status(job) == "failed"
        assert job.error_message == "pipeline failed: compile failed"
        assert result.source_document.status == "failed"
