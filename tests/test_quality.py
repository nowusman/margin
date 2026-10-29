from __future__ import annotations

import base64
from pathlib import Path

import pytest

from margin_db.model_catalog_models import ModelConfiguration
from margin_db.source_indexing import LocalSourceTreeSourceIndex
from margin_inference import InferenceClient, InferenceResponse, InferenceUnavailableError
from margin_ingest.indexing import build_source_index
from margin_ingest.quality import extract_document_text, score_conversion_quality

FIXTURES = Path("tests/fixtures/source_ingestion")


def _vision_ocr_model() -> ModelConfiguration:
    return ModelConfiguration(
        id="vision-ocr",
        display_name="Vision/OCR model",
        purpose="vision_ocr",
        runtime="openai-compatible",
        model_name="test-model",
        endpoint_url="http://127.0.0.1:9/v1",
        secret_reference="env:TEST_SECRET",
        max_tokens=4096,
        capabilities={"image_input": True, "supported_mime_types": ["application/pdf", "image/png"]},
        status="active",
        last_test_status="healthy",
    )


def test_extract_document_text_uses_vision_ocr_model_configuration(monkeypatch) -> None:
    calls = []

    def fake_chat(self, messages, **kwargs):
        calls.append((self, messages, kwargs))
        assert self.base_url == "http://127.0.0.1:9/v1"
        assert self.model == "test-model"
        assert self.secret_reference == "env:TEST_SECRET"
        assert messages[1]["content"][0]["text"]
        assert messages[1]["content"][1]["image_url"]["url"].startswith("data:application/pdf;base64,")
        assert kwargs["max_tokens"] == 4096
        return InferenceResponse(
            content="# Recovered\n\nPostgreSQL stores source spans.",
            raw_response={"choices": [{"message": {"content": "# Recovered\n\nPostgreSQL stores source spans."}}]},
        )

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_chat)

    text = extract_document_text(b"%PDF-1.4 fake", model=_vision_ocr_model())
    assert text == "# Recovered\n\nPostgreSQL stores source spans."
    assert len(calls) == 1


def test_extract_document_text_renders_pdf_page_and_uses_client(monkeypatch) -> None:
    rendered_png = b"\x89PNG\r\n\x1a\nrendered-page"

    monkeypatch.setattr("margin_ingest.quality._pdf_page_png_bytes", lambda source_bytes, *, page_number: rendered_png)

    def fake_chat(self, messages, **kwargs):
        assert self.base_url == "http://127.0.0.1:9/v1"
        assert self.model == "test-model"
        encoded = base64.b64encode(rendered_png).decode("ascii")
        assert messages[1]["content"][1]["image_url"]["url"] == f"data:image/png;base64,{encoded}"
        assert "page 2" in messages[1]["content"][0]["text"]
        return InferenceResponse(
            content="Rendered page text.", raw_response={"choices": [{"message": {"content": "Rendered page text."}}]}
        )

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_chat)

    text = extract_document_text(b"%PDF-1.4 full-document", model=_vision_ocr_model(), page_number=2)

    assert text == "Rendered page text."


def test_extract_document_text_raises_when_vision_ocr_model_unreachable(monkeypatch) -> None:
    def fake_chat(self, messages, **kwargs):
        raise InferenceUnavailableError("inference request failed")

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_chat)

    with pytest.raises(InferenceUnavailableError, match="inference request failed"):
        extract_document_text(b"%PDF-1.4 fake", model=_vision_ocr_model())


def test_extract_document_text_raises_when_chat_content_empty(monkeypatch) -> None:
    def fake_chat(self, messages, **kwargs):
        return InferenceResponse(content="", raw_response={})

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_chat)

    with pytest.raises(InferenceUnavailableError, match="vision/OCR model returned empty document text"):
        extract_document_text(b"%PDF-1.4 fake", model=_vision_ocr_model())


def test_quality_score_reports_tables_diagrams_and_section_confidence() -> None:
    markdown = (FIXTURES / "architecture.md").read_text(encoding="utf-8")
    source_index = build_source_index(markdown, document_id="doc-1")

    quality = score_conversion_quality(
        markdown, source_index, source_bytes=markdown.encode("utf-8"), metadata={"format": "md"}
    )

    assert quality.score > 0.7
    assert quality.table_coverage == 1.0
    assert quality.has_images_or_diagrams is True
    assert quality.ocr_needed is False
    assert quality.section_tree_confidence > 0.7


def test_quality_score_flags_pdf_with_little_text_as_ocr_candidate() -> None:
    source_index = build_source_index("# Scan\n\n", document_id="scan")

    quality = score_conversion_quality("# Scan\n\n", source_index, source_bytes=b"0" * 5000, metadata={"format": "pdf"})

    assert quality.ocr_needed is True
    assert "ocr_may_be_needed" in quality.warnings


def test_quality_reconciles_docling_structure_with_markdown() -> None:
    markdown = "# Architecture\n\nOnly the first page was retained.\n"
    source_index = build_source_index(markdown, document_id="structured")

    quality = score_conversion_quality(
        markdown,
        source_index,
        metadata={
            "format": "pdf",
            "page_count": 2,
            "raw_json": {
                "pages": [{"number": 1}, {"number": 2}],
                "texts": [
                    {"text": "Only the first page was retained."},
                    {"text": "This missing text belongs to page two."},
                ],
                "tables": [{"id": "table-1"}],
                "pictures": [{"id": "image-1"}],
            },
        },
    )

    assert quality.text_coverage == 0.5
    assert quality.table_coverage == 0.0
    assert quality.has_images_or_diagrams is True
    assert "missing_or_malformed_tables" in quality.warnings
    assert "unreconciled_structured_images" in quality.warnings


def test_source_index_adapter_passes_converter_structure_to_quality() -> None:
    _, quality = LocalSourceTreeSourceIndex().build(
        "# Architecture\n\nRetained text.\n",
        default_heading="Architecture",
        metadata={"format": "pdf", "raw_json": {"texts": [{"text": "Retained text."}, {"text": "Missing text."}]}},
    )

    assert quality["text_coverage"] == 0.5
    assert quality["structured_text_fragment_count"] == 2


def test_compute_ocr_idempotency_key_deterministic_and_sensitive() -> None:
    from margin_ingest.indexing import compute_ocr_idempotency_key

    key1 = compute_ocr_idempotency_key(
        source_version_id="ver-1",
        page_number=2,
        asset_checksum="chk-123",
        processor_model_id="model-ocr",
        processor_version="0.1",
    )
    key2 = compute_ocr_idempotency_key(
        source_version_id="ver-1",
        page_number=2,
        asset_checksum="chk-123",
        processor_model_id="model-ocr",
        processor_version="0.1",
    )
    assert key1 == key2

    # Verify sensitivity to each input component
    assert (
        compute_ocr_idempotency_key(
            source_version_id="ver-2",
            page_number=2,
            asset_checksum="chk-123",
            processor_model_id="model-ocr",
            processor_version="0.1",
        )
        != key1
    )
    assert (
        compute_ocr_idempotency_key(
            source_version_id="ver-1",
            page_number=3,
            asset_checksum="chk-123",
            processor_model_id="model-ocr",
            processor_version="0.1",
        )
        != key1
    )
    assert (
        compute_ocr_idempotency_key(
            source_version_id="ver-1",
            page_number=2,
            asset_checksum="chk-999",
            processor_model_id="model-ocr",
            processor_version="0.1",
        )
        != key1
    )
    assert (
        compute_ocr_idempotency_key(
            source_version_id="ver-1",
            page_number=2,
            asset_checksum="chk-123",
            processor_model_id="model-other",
            processor_version="0.1",
        )
        != key1
    )
    assert (
        compute_ocr_idempotency_key(
            source_version_id="ver-1",
            page_number=2,
            asset_checksum="chk-123",
            processor_model_id="model-ocr",
            processor_version="0.2",
        )
        != key1
    )


def test_reconcile_mixed_ocr_document_preserves_native_pages_and_metadata() -> None:
    from margin_ingest.contracts import NormalizedDocument, NormalizedElement, NormalizedPage, VisualAsset
    from margin_ingest.indexing import reconcile_mixed_ocr_document

    base_elements = (
        NormalizedElement(
            element_id="el-1",
            kind="heading",
            text="Native Page 1 Header",
            page_number=1,
            ordinal=0,
            metadata={"source": "native"},
        ),
        NormalizedElement(
            element_id="el-2",
            kind="paragraph",
            text="Native services communicate asynchronously over dedicated topics.",
            page_number=1,
            ordinal=1,
            metadata={"source": "native"},
        ),
        NormalizedElement(
            element_id="el-3",
            kind="paragraph",
            text="Scanned Page 2 Placeholder",
            page_number=2,
            ordinal=2,
            metadata={"source": "scanned_placeholder"},
        ),
        NormalizedElement(
            element_id="el-4",
            kind="paragraph",
            text="All ingress gateways must enforce an upstream rate limit of 1000 requests per second.",
            page_number=3,
            ordinal=3,
            metadata={"source": "native"},
        ),
    )
    base_pages = (
        NormalizedPage(
            page_number=1, checksum="chk-p1", text="Page 1 text", metadata={"classification": "text_extracted"}
        ),
        NormalizedPage(
            page_number=2,
            checksum="chk-p2",
            text="Scanned Page 2 Placeholder",
            metadata={"classification": "ocr_required"},
        ),
        NormalizedPage(
            page_number=3, checksum="chk-p3", text="Page 3 text", metadata={"classification": "text_extracted"}
        ),
    )
    base_visuals = (
        VisualAsset(
            asset_id="va-1",
            asset_key="diag-1",
            media_type="image/png",
            checksum="chk-v1",
            data=b"fake-png-bytes",
            page_number=1,
        ),
    )
    base_text = (
        "<!-- page: 1 -->\n"
        "# Native Page 1 Header\n\n"
        "Native services communicate asynchronously over dedicated topics.\n\n"
        "<!-- page: 2 -->\n"
        "Scanned Page 2 Placeholder\n\n"
        "<!-- page: 3 -->\n"
        "All ingress gateways must enforce an upstream rate limit of 1000 requests per second.\n"
    )
    base_doc = NormalizedDocument(
        source_name="test.md",
        format="text/markdown",
        text=base_text,
        elements=base_elements,
        pages=base_pages,
        visual_assets=base_visuals,
        metadata={"category": "architecture", "custom_key": "val"},
    )

    ocr_page_2_text = (
        "| Service Target | Error Threshold | Sleep Window |\n"
        "| Payment Gateway | 50% failures | 30 seconds |\n"
        "| Inventory Service | 40% failures | 15 seconds |"
    )

    reconciled = reconcile_mixed_ocr_document(
        base_doc,
        ocr_pages={2: ocr_page_2_text},
        metadata={"category": "architecture", "custom_key": "val", "reconciled": True},
    )

    # 1. Assert all 3 pages are present in order
    assert len(reconciled.pages) == 3
    assert [p.page_number for p in reconciled.pages] == [1, 2, 3]
    assert reconciled.pages[0].metadata.get("classification") == "text_extracted"
    assert reconciled.pages[1].metadata.get("classification") == "text_extracted"
    assert reconciled.pages[1].metadata.get("ocr_applied") is True
    assert reconciled.pages[2].metadata.get("classification") == "text_extracted"

    # 2. Assert page 1 and page 3 elements are preserved
    p1_elems = [e for e in reconciled.elements if e.page_number == 1]
    assert len(p1_elems) == 2
    assert p1_elems[0].text == "Native Page 1 Header"
    assert p1_elems[1].text == "Native services communicate asynchronously over dedicated topics."

    p3_elems = [e for e in reconciled.elements if e.page_number == 3]
    assert len(p3_elems) == 1
    assert p3_elems[0].text == "All ingress gateways must enforce an upstream rate limit of 1000 requests per second."

    # 3. Assert page 2 has the OCR-extracted content
    p2_elems = [e for e in reconciled.elements if e.page_number == 2]
    assert len(p2_elems) > 0
    assert any("Payment Gateway" in e.text for e in p2_elems)

    # 4. Assert element ordinals are strictly contiguous
    ordinals = [e.ordinal for e in reconciled.elements]
    assert ordinals == list(range(len(reconciled.elements)))

    # 5. Assert visual assets and metadata are preserved
    assert len(reconciled.visual_assets) == 1
    assert reconciled.visual_assets[0].asset_key == "diag-1"
    assert reconciled.metadata.get("custom_key") == "val"
    assert reconciled.metadata.get("reconciled") is True

    # 6. Assert text contains page markers in correct order
    assert "<!-- page: 1 -->" in reconciled.text
    assert "<!-- page: 2 -->" in reconciled.text
    assert "<!-- page: 3 -->" in reconciled.text
    assert reconciled.text.index("<!-- page: 1 -->") < reconciled.text.index("<!-- page: 2 -->")
    assert reconciled.text.index("<!-- page: 2 -->") < reconciled.text.index("<!-- page: 3 -->")
