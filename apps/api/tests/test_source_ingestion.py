from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from margin_db.object_storage import FilesystemObjectStore, StoredObject
from margin_db.source_ingestion import SourceIngestionService
from margin_db.source_ingestion_helpers import UnsupportedSourceTypeError, job_to_dict, validate_file_signature
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_db.user_models import User
from margin_ingest.contracts import ConversionResult
from margin_ingest.indexing import construct_normalized_document
from tests.helpers.source_scope import seed_source_scope


@dataclass
class RecordingStore:
    ready_calls: int = 0
    put_calls: int = 0

    def ensure_ready(self) -> None:
        self.ready_calls += 1

    def put_bytes(self, data: bytes) -> StoredObject:
        self.put_calls += 1
        return StoredObject(
            content_hash="a" * 64, size_bytes=len(data), uri="s3://margin-artifacts/raw/aa/aa/" + "a" * 64
        )

    def get_bytes(self, content_hash: str) -> bytes:
        return b"# Title\n\nBody\n"

    def exists(self, content_hash: str) -> bool:
        return True


@dataclass
class PassthroughConverter:
    def convert(self, *, filename: str, media_type: str, data: bytes) -> ConversionResult:
        text = data.decode("utf-8")
        return ConversionResult(
            normalized_document=construct_normalized_document(text, source_name=filename, format=media_type),
            converter="passthrough",
            converter_version="test",
        )


def test_upload_source_prepares_object_store_before_writing() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    session_factory = sessionmaker(bind=engine)
    store = RecordingStore()
    service = SourceIngestionService(store, converter=PassthroughConverter())

    with session_factory() as session:
        service.upload_source(
            session,
            filename="design.txt",
            media_type="text/plain",
            data=b"# Title\n\nBody\n",
            domain_id="OSS",
            category="Design Document",
            sensitivity="internal",
            actor="margin-admin",
            auto_process=False,
        )
        source = session.scalar(select(SourceDocument))
        actor = session.get(User, "margin-admin")

    assert store.ready_calls == 1
    assert store.put_calls == 1
    assert source is not None
    assert source.ingested_by == "margin-admin"
    assert actor is not None


def test_validate_file_signature_helpers() -> None:
    # Valid signatures
    assert validate_file_signature(b"%PDF-1.4 header", "doc.pdf") == "application/pdf"
    assert validate_file_signature(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image.png") == "image/png"
    assert validate_file_signature(b"\xff\xd8\xff\xe0\x00\x10JFIF", "image.jpeg") == "image/jpeg"
    assert validate_file_signature(b"II*\x00\x08\x00\x00\x00", "image.tiff") == "image/tiff"
    assert validate_file_signature(b"MM\x00*\x00\x00\x00\x08", "image.tif") == "image/tiff"
    assert validate_file_signature(b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image.webp") == "image/webp"
    assert (
        validate_file_signature(b"PK\x03\x04\x14\x00\x00\x00", "doc.docx")
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert (
        validate_file_signature(b"PK\x03\x04\x14\x00\x00\x00", "slides.pptx")
        == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert validate_file_signature(b"# Title\n\nContent", "notes.md") == "text/markdown"

    # Corrupted or unsupported signatures
    with pytest.raises(UnsupportedSourceTypeError):
        validate_file_signature(b"", "empty.png")
    with pytest.raises(UnsupportedSourceTypeError):
        validate_file_signature(b"not a png", "corrupt.png")
    with pytest.raises(UnsupportedSourceTypeError):
        validate_file_signature(b"not a pdf", "corrupt.pdf")
    with pytest.raises(UnsupportedSourceTypeError):
        validate_file_signature(b"not a docx", "corrupt.docx")
    with pytest.raises(UnsupportedSourceTypeError):
        validate_file_signature(b"RIFF\x00\x00\x00\x00XXXX", "corrupt.webp")
    with pytest.raises(UnsupportedSourceTypeError):
        validate_file_signature(b"\x00\x01\x02\x03", "corrupt.txt")


def test_upload_source_corrupted_signature_rejected() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    session_factory = sessionmaker(bind=engine)
    store = RecordingStore()
    service = SourceIngestionService(store, converter=PassthroughConverter())

    with session_factory() as session:
        with pytest.raises(UnsupportedSourceTypeError):
            service.upload_source(
                session,
                filename="image.png",
                media_type="image/png",
                data=b"not a valid png payload",
                domain_id="OSS",
                category="Design Document",
                sensitivity="internal",
                actor="margin-admin",
            )
        with pytest.raises(UnsupportedSourceTypeError):
            service.upload_source(
                session,
                filename="report.pdf",
                media_type="application/pdf",
                data=b"not a valid pdf payload",
                domain_id="OSS",
                category="Design Document",
                sensitivity="internal",
                actor="margin-admin",
            )

    assert store.put_calls == 0


def test_upload_source_standalone_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from margin_db.model_catalog_models import ModelConfiguration

    fixture_path = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "source_ingestion" / "sample.png"
    assert fixture_path.exists()
    png_bytes = fixture_path.read_bytes()

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    session_factory = sessionmaker(bind=engine)
    store = FilesystemObjectStore(tmp_path / "objects")
    service = SourceIngestionService(store)

    monkeypatch.setattr(
        "margin_ingest.quality.extract_document_text",
        lambda source_bytes, *, model, page_number, domain_id=None, sensitivity=None: "",
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

        result = service.upload_source(
            session,
            filename="sample.png",
            media_type="image/png",
            data=png_bytes,
            domain_id="OSS",
            category="Design Document",
            sensitivity="internal",
            actor="margin-admin",
            auto_process=True,
        )
        session.commit()

        assert result.cached is False
        assert result.source_document.status == "extraction_ready"
        assert result.source_document.media_type == "image/png"

        job = job_to_dict(result.ingestion_job)
        assert job is not None
        assert job["state"] == "extracting"

        version = session.scalar(select(SourceDocumentVersion))
        assert version is not None

        pages = list(session.scalars(select(SourcePage)).all())
        assert len(pages) == 1
        assert pages[0].page_number == 1

        elements = list(session.scalars(select(SourceElement)).all())
        assert len(elements) >= 1
        image_elem = next((e for e in elements if e.kind in {"image", "picture"}), None)
        assert image_elem is not None
