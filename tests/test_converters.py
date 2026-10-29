from __future__ import annotations

from pathlib import Path

import pytest

from margin_db.source_models import DUBLIN_CORE_FIELDS
from margin_ingest.converters import (
    ConversionError,
    DoclingPrimaryConverter,
    ImageConverter,
    LightweightSourceConverter,
    MarkItDownSecondaryConverter,
    build_default_docling_pipeline_options,
    convert_source_document,
    validate_file_signature,
)
from workers.ingest.metadata import extract_metadata

FIXTURES = Path("tests/fixtures/source_ingestion")


class FakeDoclingAdapter:
    def convert(self, path: Path) -> dict[str, object]:
        return {
            "markdown": f"# Converted {path.suffix.upper()}\n\nDocling local adapter content.",
            "json": {"pages": [{"number": 1}]},
            "metadata": {"title": f"Converted {path.name}", "page_count": 1},
        }


class FailingPrimary:
    name = "docling-primary"

    def convert(self, path: str | Path, media_type: str | None = None):
        raise ConversionError("local docling unavailable in test")


class FakeMarkItDownAdapter:
    def convert(self, path: str) -> dict[str, object]:
        return {
            "text_content": f"# MarkItDown {Path(path).name}\n\nSecondary content.",
            "metadata": {"title": "MarkItDown fixture"},
        }


@pytest.mark.parametrize("name", ["minimal.pdf", "minimal.docx", "minimal.pptx"])
def test_docling_primary_accepts_local_adapter_for_office_and_pdf_fixtures(name: str) -> None:
    result = DoclingPrimaryConverter(adapter=FakeDoclingAdapter()).convert(FIXTURES / name)

    assert result.converter == "docling-primary"
    assert result.markdown.startswith("# Converted")
    assert result.raw_json["pages"][0]["number"] == 1
    assert result.metadata["title"] == f"Converted {name}"


def test_lightweight_converter_handles_markdown_text_and_diagram_sources() -> None:
    converter = LightweightSourceConverter()

    note = converter.convert(FIXTURES / "note.txt")
    openapi = converter.convert(FIXTURES / "service.openapi.yaml")
    diagram = converter.convert(FIXTURES / "system.dsl")
    mermaid = converter.convert(FIXTURES / "flow.mmd")
    plantuml = converter.convert(FIXTURES / "deployment.puml")

    assert "Margin source ingestion note" in note.markdown
    assert "openapi:" in openapi.markdown
    assert openapi.metadata["source_name"] == "service.openapi.yaml"
    assert "```structurizr" in diagram.markdown
    assert "```mermaid" in mermaid.markdown
    assert "```plantuml" in plantuml.markdown
    assert diagram.metadata["converter"] == "lightweight-source"


@pytest.mark.parametrize("name", ["minimal.pdf", "minimal.docx", "minimal.pptx"])
def test_lightweight_converter_rejects_rich_binary_formats(name: str) -> None:
    with pytest.raises(ConversionError, match="requires Docling"):
        LightweightSourceConverter().convert(FIXTURES / name)


def test_convert_source_document_uses_lightweight_converter_for_text() -> None:
    result = convert_source_document(FIXTURES / "note.txt", primary=FailingPrimary())

    assert result.converter == "lightweight-source"
    assert "primary_converter_failed" not in result.warnings
    assert "secondary_converter" not in result.metadata


def test_markitdown_secondary_uses_real_adapter_boundary() -> None:
    result = MarkItDownSecondaryConverter(adapter=FakeMarkItDownAdapter()).convert(FIXTURES / "minimal.pdf")

    assert result.converter == "markitdown-secondary"
    assert "Secondary content" in result.markdown
    assert result.metadata["title"] == "MarkItDown fixture"


def test_markitdown_secondary_rejects_unsupported_formats() -> None:
    with pytest.raises(ConversionError, match="only supports rich formats"):
        MarkItDownSecondaryConverter(adapter=FakeMarkItDownAdapter()).convert(FIXTURES / "note.txt")


def test_convert_source_document_does_not_try_markitdown_for_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_markitdown(self: MarkItDownSecondaryConverter) -> object:
        raise ConversionError("local markitdown unavailable in test")

    monkeypatch.setattr(MarkItDownSecondaryConverter, "_load_markitdown_adapter", fail_markitdown)
    result = convert_source_document(FIXTURES / "note.txt", primary=FailingPrimary())

    assert result.converter == "lightweight-source"
    assert "secondary_converter" not in result.metadata
    assert "secondary_failures" not in result.metadata


def test_convert_source_document_fails_rich_documents_without_local_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_markitdown(self: MarkItDownSecondaryConverter) -> object:
        raise ConversionError("local markitdown unavailable in test")

    monkeypatch.setattr(MarkItDownSecondaryConverter, "_load_markitdown_adapter", fail_markitdown)

    with pytest.raises(ConversionError, match="all local converters failed"):
        convert_source_document(FIXTURES / "minimal.pdf", primary=FailingPrimary())


def test_metadata_extractor_returns_dublin_core_fields() -> None:
    converted = LightweightSourceConverter().convert(FIXTURES / "architecture.md")
    metadata = extract_metadata(
        FIXTURES / "architecture.md", markdown=converted.markdown, conversion_metadata=converted.metadata
    )

    assert metadata.title == "Customer Platform HLD"
    assert metadata.language == "en"
    assert metadata.document_type == "design_document"
    assert metadata.dublin_core["title"] == "Customer Platform HLD"


def test_metadata_extractor_reads_pdf_page_count_and_embedded_fields() -> None:
    converted = DoclingPrimaryConverter(adapter=FakeDoclingAdapter()).convert(FIXTURES / "minimal.pdf")
    metadata = extract_metadata(
        FIXTURES / "minimal.pdf", markdown=converted.markdown, conversion_metadata=converted.metadata
    )

    assert metadata.title == "Converted minimal.pdf"
    assert metadata.page_count == 1


@pytest.mark.parametrize(
    "fixture_name",
    [
        "architecture.md",
        "note.txt",
        "service.openapi.yaml",
        "events.asyncapi.yaml",
        "system.dsl",
        "flow.mmd",
        "deployment.puml",
    ],
)
def test_all_supported_source_records_expose_canonical_dublin_core_keys(fixture_name: str) -> None:
    converted = LightweightSourceConverter().convert(FIXTURES / fixture_name)
    metadata = extract_metadata(
        FIXTURES / fixture_name, markdown=converted.markdown, conversion_metadata=converted.metadata
    )

    assert set(metadata.dublin_core) == set(DUBLIN_CORE_FIELDS)


def test_validate_file_signature_accepts_valid_signatures() -> None:
    # PNG
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    assert validate_file_signature(png_bytes, "test.png") == "image/png"

    # JPEG
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    assert validate_file_signature(jpeg_bytes, "test.jpg") == "image/jpeg"
    assert validate_file_signature(jpeg_bytes, "test.jpeg") == "image/jpeg"

    # TIFF (little endian and big endian)
    tiff_le = b"II*\x00\x08\x00\x00\x00"
    tiff_be = b"MM\x00*\x00\x00\x00\x08"
    assert validate_file_signature(tiff_le, "test.tiff") == "image/tiff"
    assert validate_file_signature(tiff_be, "test.tif") == "image/tiff"

    # WebP
    webp_bytes = b"RIFF\x1a\x00\x00\x00WEBPVP8 "
    assert validate_file_signature(webp_bytes, "test.webp") == "image/webp"

    # PDF
    pdf_bytes = b"%PDF-1.4\n1 0 obj"
    assert validate_file_signature(pdf_bytes, "test.pdf") == "application/pdf"

    # DOCX / PPTX (ZIP)
    zip_bytes = b"PK\x03\x04\x14\x00\x00\x00"
    assert (
        validate_file_signature(zip_bytes, "test.docx")
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert (
        validate_file_signature(zip_bytes, "test.pptx")
        == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )


@pytest.mark.parametrize(
    "filename,corrupted_bytes",
    [
        ("test.png", b"Not a PNG image header"),
        ("test.jpg", b"Not a JPEG image header"),
        ("test.jpeg", b"\x00\x00\x00\x00"),
        ("test.tiff", b"Not TIFF bytes"),
        ("test.webp", b"RIFFbad_data"),
        ("test.pdf", b"This is plain text pretending to be PDF"),
        ("test.docx", b"Not a PK zip file"),
        ("test.pptx", b"Plain text not pptx"),
        ("test.pdf", b""),
        ("note.txt", b"Hello\x00World Binary in text"),
    ],
)
def test_validate_file_signature_rejects_corrupted_headers(filename: str, corrupted_bytes: bytes) -> None:
    with pytest.raises(ConversionError):
        validate_file_signature(corrupted_bytes, filename)


def test_standalone_image_conversion_produces_single_page_and_visual_element() -> None:
    pytest.importorskip("PIL", reason="image dimensions are read with Pillow")
    converter = ImageConverter()
    for image_name in ["sample.png", "sample.jpg", "sample.webp", "sample.tiff"]:
        path = FIXTURES / image_name
        result = converter.convert(path)

        assert result.converter == "image-converter"
        doc = result.normalized_document
        assert len(doc.pages) == 1
        page = doc.pages[0]
        assert page.page_number == 1
        assert page.metadata["classification"] == "ocr_required"
        assert page.width is not None and page.width > 0
        assert page.height is not None and page.height > 0
        assert page.checksum is not None

        assert len(doc.elements) == 1
        element = doc.elements[0]
        assert element.kind == "image"
        assert element.page_number == 1
        assert element.asset_key == doc.visual_assets[0].asset_key
        assert element.bounding_box == (0.0, 0.0, float(page.width), float(page.height))
        assert element.text == ""

        assert len(doc.visual_assets) == 1
        asset = doc.visual_assets[0]
        assert asset.data is not None
        assert len(asset.data) > 0
        assert asset.checksum == doc.metadata["checksum"]


def test_convert_source_document_routes_images_to_image_converter() -> None:
    result = convert_source_document(FIXTURES / "sample.png")
    assert result.converter == "image-converter"
    assert len(result.normalized_document.pages) == 1
    assert len(result.normalized_document.elements) == 1
    assert len(result.normalized_document.visual_assets) == 1


def test_docling_pipeline_options_configuration() -> None:
    options = build_default_docling_pipeline_options()
    assert options is None or hasattr(options, "do_ocr")

    # Custom pipeline options object
    class CustomPipelineOptions:
        do_ocr = True
        do_table_structure = True
        generate_page_images = True
        generate_picture_images = True

    custom_options = CustomPipelineOptions()
    converter = DoclingPrimaryConverter(pipeline_options=custom_options)
    assert converter.pipeline_options is custom_options
    assert converter.pipeline_options.do_ocr is True
    assert converter.pipeline_options.do_table_structure is True
    assert converter.pipeline_options.generate_page_images is True
    assert converter.pipeline_options.generate_picture_images is True


def test_docling_primary_extracts_visual_assets_from_pictures() -> None:
    class DoclingWithPicturesAdapter:
        def convert(self, path: Path) -> dict[str, object]:
            return {
                "markdown": "# Document With Diagram\n\nSome text.",
                "json": {
                    "pages": [{"number": 1}],
                    "pictures": [
                        {
                            "id": "pic-1",
                            "page": 1,
                            "bbox": [10.0, 20.0, 100.0, 200.0],
                            "checksum": "abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcd",
                            "media_type": "image/png",
                        }
                    ],
                },
                "metadata": {"title": "Docling Pictures", "page_count": 1},
            }

    converter = DoclingPrimaryConverter(adapter=DoclingWithPicturesAdapter())
    result = converter.convert(FIXTURES / "minimal.pdf")

    assert len(result.normalized_document.visual_assets) == 1
    asset = result.normalized_document.visual_assets[0]
    assert asset.bounding_box == (10.0, 20.0, 100.0, 200.0)
    assert asset.page_number == 1
    assert asset.checksum == "abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcd"
    # Ensure visual asset is represented in normalized elements
    assert any(elem.asset_key == asset.asset_key for elem in result.normalized_document.elements)


def test_markitdown_rejects_placeholder_only_output() -> None:
    class PlaceholderOnlyMarkItDownAdapter:
        def convert(self, path: str) -> dict[str, object]:
            return {"text_content": "[image]\n\nFigure 1", "metadata": {"title": "Placeholder"}}

    converter = MarkItDownSecondaryConverter(adapter=PlaceholderOnlyMarkItDownAdapter())
    with pytest.raises(ConversionError, match="contains only placeholder content"):
        converter.convert(FIXTURES / "minimal.pdf")


def test_diagram_conversion_preserves_source_and_structure_deterministically() -> None:
    converter = LightweightSourceConverter()

    for fixture, fence in [("flow.mmd", "mermaid"), ("deployment.puml", "plantuml"), ("system.dsl", "structurizr")]:
        result = converter.convert(FIXTURES / fixture)
        assert result.converter == "lightweight-source"
        assert f"```{fence}" in result.markdown
        assert result.raw_json["diagram_type"] == fence
        assert "diagram_source" in result.raw_json
        assert len(result.raw_json["diagram_source"]) > 0
