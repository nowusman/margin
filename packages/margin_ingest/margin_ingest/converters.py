from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from margin_ingest.contracts import ConversionResult, NormalizedDocument, NormalizedElement, NormalizedPage, VisualAsset
from margin_ingest.indexing import construct_normalized_document, is_placeholder_figure_description
from margin_schema.ids import element_id

if TYPE_CHECKING:
    from docling.datamodel.base_models import InputFormat as DoclingInputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions as DoclingPdfPipelineOptions
    from docling.document_converter import FormatOption as DoclingFormatOption
else:
    DoclingInputFormat = object
    DoclingPdfPipelineOptions = object
    DoclingFormatOption = object

TEXT_EXTENSIONS = {".json", ".md", ".markdown", ".txt", ".yaml", ".yml"}
DIAGRAM_EXTENSIONS = {".dsl", ".mmd", ".mermaid", ".puml", ".plantuml", ".iuml"}
OFFICE_EXTENSIONS = {".docx", ".pptx"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp"}
RICH_EXTENSIONS = OFFICE_EXTENSIONS | PDF_EXTENSIONS
LIGHTWEIGHT_EXTENSIONS = TEXT_EXTENSIONS | DIAGRAM_EXTENSIONS
DOCLING_DOCUMENT_TIMEOUT_SECONDS = 120
DOCLING_MAX_FILE_SIZE = 50 * 1024 * 1024
DOCLING_MAX_PAGES = 200

EXTENSION_TO_MEDIA_TYPE = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".dsl": "text/vnd.structurizr",
    ".mmd": "text/vnd.mermaid",
    ".mermaid": "text/vnd.mermaid",
    ".puml": "text/vnd.plantuml",
    ".plantuml": "text/vnd.plantuml",
    ".iuml": "text/vnd.plantuml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
}


class ConversionError(RuntimeError):
    """Raised when a converter cannot produce local normalized content."""


class ConversionUnavailable(ConversionError):
    """Raised when a required local converter dependency is not installed."""


class Converter(Protocol):
    name: str

    def convert(self, path: str | Path, media_type: str | None = None) -> ConversionResult:
        """Convert a local file to normalized ConversionResult."""


class _DoclingAdapter(Protocol):
    def convert(self, path: Path, **kwargs: int) -> object:
        """Convert one local rich-document source."""


def validate_file_signature(data: bytes, filename: str, media_type: str | None = None) -> str:
    """Validate file signature (magic bytes) against extension and media type.

    Raises ConversionError if data is empty or signature is mismatched or corrupted.
    Returns the resolved media type.
    """
    if not data:
        raise ConversionError(f"file data is empty: {filename}")

    suffix = Path(filename).suffix.lower()
    expected_media_type = EXTENSION_TO_MEDIA_TYPE.get(suffix)
    if not expected_media_type and media_type:
        expected_media_type = media_type.split(";")[0].strip().lower()

    if not expected_media_type:
        raise ConversionError(f"unsupported source type for {filename}: {suffix or 'unknown'}")

    if expected_media_type == "image/png":
        if not (len(data) >= 8 and data.startswith(b"\x89PNG\r\n\x1a\n")):
            raise ConversionError(f"corrupted or invalid PNG file signature for {filename}")
        return "image/png"

    if expected_media_type == "image/jpeg":
        if not (len(data) >= 3 and data.startswith(b"\xff\xd8\xff")):
            raise ConversionError(f"corrupted or invalid JPEG file signature for {filename}")
        return "image/jpeg"

    if expected_media_type == "image/tiff":
        valid_tiff = len(data) >= 4 and (
            data.startswith(b"II*\x00")
            or data.startswith(b"MM\x00*")
            or data.startswith(b"II+\x00")
            or data.startswith(b"MM\x00+")
        )
        if not valid_tiff:
            raise ConversionError(f"corrupted or invalid TIFF file signature for {filename}")
        return "image/tiff"

    if expected_media_type == "image/webp":
        if not (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
            raise ConversionError(f"corrupted or invalid WebP file signature for {filename}")
        return "image/webp"

    if expected_media_type == "application/pdf":
        if b"%PDF-" not in data[:1024]:
            raise ConversionError(f"corrupted or invalid PDF file signature for {filename}")
        return "application/pdf"

    if expected_media_type in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        if not (len(data) >= 4 and data.startswith(b"PK\x03\x04")):
            raise ConversionError(f"corrupted or invalid Office Open XML ZIP file signature for {filename}")
        return expected_media_type

    if expected_media_type in {
        "text/markdown",
        "text/plain",
        "text/vnd.structurizr",
        "text/vnd.mermaid",
        "text/vnd.plantuml",
        "application/yaml",
        "application/json",
        "application/x-yaml",
        "text/yaml",
        "text/x-yaml",
    }:
        if b"\x00" in data[:1024]:
            raise ConversionError(f"binary content detected in text format for {filename}")
        try:
            data[:4096].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConversionError(f"invalid UTF-8 encoding for text source {filename}: {exc}") from exc
        return expected_media_type

    return expected_media_type


def build_default_docling_pipeline_options() -> DoclingPdfPipelineOptions | None:
    """Explicitly configure Docling pipeline options for rich document conversion."""
    try:
        from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions, TableStructureOptions
    except ImportError:
        return None

    pipeline_options = PdfPipelineOptions(
        document_timeout=DOCLING_DOCUMENT_TIMEOUT_SECONDS, ocr_options=EasyOcrOptions()
    )
    pipeline_options.create_legacy_output = False
    pipeline_options.accelerator_options.num_threads = 4
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options = TableStructureOptions(do_cell_matching=True)
    pipeline_options.generate_page_images = True
    pipeline_options.generate_picture_images = True
    pipeline_options.images_scale = 2.0
    return pipeline_options


class DoclingPrimaryConverter:
    """Primary local converter for rich PDF, DOCX and PPTX documents."""

    name = "docling-primary"

    def __init__(
        self, adapter: _DoclingAdapter | None = None, pipeline_options: DoclingPdfPipelineOptions | None = None
    ) -> None:
        self._adapter = adapter
        self.pipeline_options = (
            pipeline_options if pipeline_options is not None else build_default_docling_pipeline_options()
        )

    def convert(self, path: str | Path, media_type: str | None = None) -> ConversionResult:
        source = Path(path)
        data = source.read_bytes()
        validate_file_signature(data, source.name, media_type)
        if len(data) > DOCLING_MAX_FILE_SIZE:
            raise ConversionError(f"{source.name} exceeds the Docling source limit of {DOCLING_MAX_FILE_SIZE} bytes")

        adapter = self._adapter or self._load_docling_adapter()

        try:
            if self._adapter is None:
                converted = adapter.convert(
                    source, max_num_pages=DOCLING_MAX_PAGES, max_file_size=DOCLING_MAX_FILE_SIZE
                )
            else:
                converted = adapter.convert(source)
        except Exception as exc:  # pragma: no cover - adapter specific
            raise ConversionError(f"Docling conversion failed for {source.name}: {exc}") from exc

        markdown, raw_json, metadata, visual_assets = _coerce_docling_result(converted, source=source)
        source_format = _format_from_path(source, media_type)
        converter_ver = _package_version("docling")
        metadata = {
            "format": source_format,
            "source_name": source.name,
            "converter": self.name,
            "converter_version": converter_ver,
            **metadata,
        }
        normalized_md = normalize_markdown(markdown)
        doc = construct_normalized_document(
            normalized_md,
            source_name=source.name,
            format=source_format,
            raw_json=raw_json,
            metadata=metadata,
            visual_assets=visual_assets,
        )
        return ConversionResult(
            normalized_document=doc,
            converter=self.name,
            converter_version=converter_ver,
            raw_json=raw_json,
            warnings=doc.warnings,
        )

    def _load_docling_adapter(self) -> _DoclingAdapter:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PipelineOptions
            from docling.document_converter import (
                DocumentConverter,
                PdfFormatOption,
                PowerpointFormatOption,
                WordFormatOption,
            )
        except ImportError as exc:
            raise ConversionUnavailable(
                "Docling is not installed locally. Rich document conversion requires "
                "approved local Docling and MarkItDown dependencies."
            ) from exc

        format_options: dict[DoclingInputFormat, DoclingFormatOption] = {}
        if self.pipeline_options is not None:
            format_options[InputFormat.PDF] = PdfFormatOption(pipeline_options=self.pipeline_options)
        office_options = PipelineOptions(document_timeout=DOCLING_DOCUMENT_TIMEOUT_SECONDS)
        format_options[InputFormat.DOCX] = WordFormatOption(pipeline_options=office_options)
        format_options[InputFormat.PPTX] = PowerpointFormatOption(pipeline_options=office_options)
        return cast(
            _DoclingAdapter,
            DocumentConverter(
                allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.PPTX], format_options=format_options
            ),
        )


class ImageConverter:
    """Standalone image converter for PNG, JPEG, TIFF, and WebP images."""

    name = "image-converter"
    version = "0.1"

    def convert(self, path: str | Path, media_type: str | None = None) -> ConversionResult:
        source = Path(path)
        data = source.read_bytes()
        validated_media_type = validate_file_signature(data, source.name, media_type)

        width: int | None = None
        height: int | None = None
        try:
            from PIL import Image  # type: ignore

            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
        except Exception:
            pass

        checksum = hashlib.sha256(data).hexdigest()
        asset_id = f"va_{checksum[:16]}"
        image_element_id = element_id({"source_name": source.name, "asset_id": asset_id, "asset_key": checksum})
        bbox = (0.0, 0.0, float(width), float(height)) if width and height else None

        visual_asset = VisualAsset(
            asset_id=asset_id,
            asset_key=checksum,
            media_type=validated_media_type,
            checksum=checksum,
            page_number=1,
            bounding_box=bbox,
            data=data,
            metadata={"width": width, "height": height, "media_type": validated_media_type},
        )

        element = NormalizedElement(
            element_id=image_element_id,
            kind="image",
            text="",
            ordinal=0,
            page_number=1,
            char_start=0,
            char_end=0,
            bounding_box=bbox,
            asset_key=checksum,
            confidence=1.0,
            metadata={
                "width": width,
                "height": height,
                "media_type": validated_media_type,
                "checksum": checksum,
                "structural_path": "image/0",
            },
        )

        page = NormalizedPage(
            page_number=1,
            checksum=checksum,
            text="",
            elements=(element,),
            width=float(width) if width else None,
            height=float(height) if height else None,
            metadata={
                "classification": "ocr_required",
                "width": width,
                "height": height,
                "media_type": validated_media_type,
            },
        )

        metadata = {
            "format": validated_media_type,
            "source_name": source.name,
            "converter": self.name,
            "converter_version": self.version,
            "width": width,
            "height": height,
            "checksum": checksum,
            "byte_length": len(data),
            "page_count": 1,
        }

        doc = NormalizedDocument(
            source_name=source.name,
            format=validated_media_type,
            text="",
            pages=(page,),
            elements=(element,),
            visual_assets=(visual_asset,),
            metadata=metadata,
        )

        return ConversionResult(
            normalized_document=doc,
            converter=self.name,
            converter_version=self.version,
            raw_json={
                "converter": self.name,
                "source_name": source.name,
                "width": width,
                "height": height,
                "checksum": checksum,
                "media_type": validated_media_type,
                "pages": [{"page_number": 1, "width": width, "height": height}],
                "pictures": [
                    {
                        "id": image_element_id,
                        "page": 1,
                        "bbox": bbox,
                        "checksum": checksum,
                        "media_type": validated_media_type,
                    }
                ],
            },
            warnings=(),
        )


class LightweightSourceConverter:
    """Small stdlib converter for deterministic text and diagram source files."""

    name = "lightweight-source"
    version = "0.1"

    def convert(self, path: str | Path, media_type: str | None = None) -> ConversionResult:
        source = Path(path)
        suffix = source.suffix.lower()
        source_format = _format_from_path(source, media_type)
        data = source.read_bytes()
        validate_file_signature(data, source.name, media_type)

        raw_extra: dict[str, Any] = {}
        if suffix in TEXT_EXTENSIONS:
            markdown = data.decode("utf-8", errors="replace")
        elif suffix in DIAGRAM_EXTENSIONS:
            markdown, raw_extra = _diagram_to_markdown_and_raw(source, data.decode("utf-8", errors="replace"))
        else:
            raise ConversionError(f"{source.name} requires Docling or MarkItDown")

        metadata = {
            "format": source_format,
            "source_name": source.name,
            "converter": self.name,
            "converter_version": self.version,
            "byte_length": len(data),
            **raw_extra,
        }
        normalized_md = normalize_markdown(markdown)
        doc = construct_normalized_document(
            normalized_md, source_name=source.name, format=source_format, metadata=metadata
        )
        return ConversionResult(
            normalized_document=doc,
            converter=self.name,
            converter_version=self.version,
            raw_json={"converter": self.name, "source_name": source.name, **raw_extra},
            warnings=doc.warnings,
        )


class MarkItDownSecondaryConverter:
    """Recorded secondary converter for rich documents after Docling failure."""

    name = "markitdown-secondary"

    def __init__(self, adapter: Any | None = None) -> None:
        self._adapter = adapter

    def convert(self, path: str | Path, media_type: str | None = None) -> ConversionResult:
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix not in RICH_EXTENSIONS:
            raise ConversionError(
                "MarkItDown secondary converter only supports rich formats "
                f"{sorted(RICH_EXTENSIONS)}, got {source.name}"
            )
        data = source.read_bytes()
        validate_file_signature(data, source.name, media_type)

        adapter = self._adapter or self._load_markitdown_adapter()
        try:
            converted = adapter.convert(str(source))
        except Exception as exc:  # pragma: no cover - adapter specific
            raise ConversionError(f"MarkItDown conversion failed for {source.name}: {exc}") from exc

        markdown, raw_json, metadata = _coerce_markitdown_result(converted)
        source_format = _format_from_path(source, media_type)
        converter_ver = _package_version("markitdown")
        metadata = {
            "format": source_format,
            "source_name": source.name,
            "converter": self.name,
            "converter_version": converter_ver,
            **metadata,
        }
        normalized_md = normalize_markdown(markdown)
        doc = construct_normalized_document(
            normalized_md, source_name=source.name, format=source_format, raw_json=raw_json, metadata=metadata
        )

        if not doc.pages or not doc.elements or not doc.text.strip():
            raise ConversionError(f"MarkItDown did not produce a complete page/element manifest for {source.name}")

        meaningful_elements = [
            elem for elem in doc.elements if elem.text.strip() and not is_placeholder_figure_description(elem.text)
        ]
        if not meaningful_elements:
            raise ConversionError(
                f"MarkItDown output for {source.name} contains only placeholder content; not a quality pass"
            )

        return ConversionResult(
            normalized_document=doc,
            converter=self.name,
            converter_version=converter_ver,
            raw_json=raw_json,
            warnings=doc.warnings,
        )

    def _load_markitdown_adapter(self) -> Any:
        try:
            from markitdown import MarkItDown  # type: ignore
        except ImportError as exc:
            raise ConversionUnavailable(
                "MarkItDown is not installed locally. Rich document conversion requires "
                "approved local Docling and MarkItDown dependencies."
            ) from exc
        return MarkItDown()


def validate_converter_dependencies() -> None:
    missing: list[str] = []
    for package_name, import_path in (("docling", "docling.document_converter"), ("markitdown", "markitdown")):
        try:
            __import__(import_path)
        except ImportError:
            missing.append(package_name)
    if missing:
        raise ConversionUnavailable("Missing production document converter dependencies: " + ", ".join(missing))


def convert_source_document(
    path: str | Path,
    primary: Converter | None = None,
    secondary: Converter | None = None,
    media_type: str | None = None,
) -> ConversionResult:
    source = Path(path)
    suffix = source.suffix.lower()

    if suffix in LIGHTWEIGHT_EXTENSIONS:
        result = LightweightSourceConverter().convert(path, media_type=media_type)
        return _with_quality_metadata(result)

    if suffix in IMAGE_EXTENSIONS:
        result = ImageConverter().convert(path, media_type=media_type)
        return _with_quality_metadata(result)

    if suffix not in RICH_EXTENSIONS:
        raise ConversionError(f"{source.name} has unsupported source format: {suffix or 'unknown'}")

    primary_converter = primary or DoclingPrimaryConverter()
    try:
        result = primary_converter.convert(path, media_type=media_type)
        return _require_citable_rich_output(_with_quality_metadata(result), converter_failed=False)
    except ConversionError as exc:
        primary_failure = str(exc)

    secondary_converter = secondary or MarkItDownSecondaryConverter()
    if getattr(secondary_converter, "name", "") != MarkItDownSecondaryConverter.name:
        raise ConversionError("rich document secondary converter must be MarkItDown")
    secondary_chain: tuple[Converter, ...] = (secondary_converter,)
    secondary_failures: list[str] = []
    for converter in secondary_chain:
        try:
            result = converter.convert(path, media_type=media_type)
        except ConversionError as exc:
            secondary_failures.append(f"{converter.name}: {exc}")
            continue
        metadata = {
            **result.normalized_document.metadata,
            "primary_converter": getattr(primary_converter, "name", "primary"),
            "secondary_reason": primary_failure,
            "secondary_converter": converter.name,
        }
        if secondary_failures:
            metadata["secondary_failures"] = secondary_failures
        updated_doc = replace(
            result.normalized_document, metadata=metadata, warnings=(*result.warnings, "primary_converter_failed")
        )
        return _require_citable_rich_output(
            _with_quality_metadata(replace(result, normalized_document=updated_doc, warnings=updated_doc.warnings)),
            converter_failed=True,
        )

    raise ConversionError(
        "all local converters failed after primary failure: " + "; ".join([primary_failure, *secondary_failures])
    )


def normalize_markdown(markdown: str) -> str:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    return normalized.strip() + "\n" if normalized.strip() else ""


def _with_quality_metadata(result: ConversionResult) -> ConversionResult:
    text = result.normalized_document.text
    text_length = len(text.strip())
    table_count = text.count("|---") + text.count("| ---")
    metadata = {
        **result.normalized_document.metadata,
        "text_coverage": text_length,
        "table_coverage": table_count,
        "warnings": list(result.warnings),
    }
    updated_doc = replace(result.normalized_document, metadata=metadata)
    return replace(result, normalized_document=updated_doc)


def _require_citable_rich_output(result: ConversionResult, *, converter_failed: bool) -> ConversionResult:
    if result.normalized_document.text.strip():
        return result
    failure_reason = "primary_converter_failed" if converter_failed else "converter_produced_empty_text"
    source_name = result.normalized_document.metadata.get("source_name", "rich document")
    raise ConversionError(
        f"{source_name} did not produce citable text; failure_reason={failure_reason}; converter={result.converter}"
    )


def _package_version(package_name: str) -> str:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return "unknown"


def _coerce_docling_result(
    converted: Any, source: Path | None = None
) -> tuple[str, dict[str, Any], dict[str, Any], list[VisualAsset]]:
    visual_assets: list[VisualAsset] = []
    source_name = source.name if source else "document"

    if isinstance(converted, ConversionResult):
        return (
            converted.markdown,
            converted.raw_json,
            dict(converted.metadata),
            list(converted.normalized_document.visual_assets),
        )

    if isinstance(converted, dict):
        markdown = str(converted.get("markdown") or converted.get("text") or "")
        raw_json_payload = dict(converted.get("json") or converted.get("raw_json") or converted)
        metadata = dict(converted.get("metadata") or {})
        pics = raw_json_payload.get("pictures") or []
        for idx, pic in enumerate(pics):
            if isinstance(pic, dict):
                page_no = pic.get("page") or pic.get("page_number") or 1
                bbox_val = pic.get("bbox") or pic.get("bounding_box")
                bbox = (
                    tuple(float(x) for x in bbox_val)
                    if isinstance(bbox_val, list | tuple) and len(bbox_val) == 4
                    else None
                )
                data_b64 = pic.get("data") or pic.get("base64")
                img_bytes = base64.b64decode(data_b64) if isinstance(data_b64, str) else None
                checksum = pic.get("checksum") or (
                    hashlib.sha256(img_bytes).hexdigest()
                    if img_bytes
                    else hashlib.sha256(f"{source_name}:pic:{idx}".encode()).hexdigest()
                )
                asset_key = pic.get("asset_key") or checksum
                media_type = pic.get("media_type") or "image/png"
                visual_assets.append(
                    VisualAsset(
                        asset_id=f"va_{checksum[:16]}",
                        asset_key=asset_key,
                        media_type=media_type,
                        checksum=checksum,
                        page_number=page_no,
                        bounding_box=bbox,
                        data=img_bytes,
                        metadata={"picture_index": idx, **pic.get("metadata", {})},
                    )
                )
        return markdown, raw_json_payload, metadata, visual_assets

    document = getattr(converted, "document", converted)
    if hasattr(document, "export_to_markdown"):
        markdown = str(document.export_to_markdown())
    elif hasattr(converted, "export_to_markdown"):
        markdown = str(converted.export_to_markdown())
    else:
        markdown = str(converted)

    raw_json: dict[str, Any] = {}
    for attr in ("export_to_dict", "model_dump", "dict"):
        candidate = getattr(document, attr, None)
        if callable(candidate):
            try:
                raw_json = dict(candidate())
                break
            except Exception:
                raw_json = {}

    metadata = dict(getattr(document, "metadata", {}) or getattr(converted, "metadata", {}) or {})
    if not raw_json and markdown:
        raw_json = {"markdown": markdown}

    doc_pictures = getattr(document, "pictures", None) or []
    for idx, pic in enumerate(doc_pictures):
        page_no = None
        bbox = None
        prov = getattr(pic, "prov", None)
        if prov and len(prov) > 0:
            page_no = getattr(prov[0], "page_no", None)
            bb = getattr(prov[0], "bbox", None)
            if bb:
                bbox = (
                    float(getattr(bb, "l", 0.0)),
                    float(getattr(bb, "t", 0.0)),
                    float(getattr(bb, "r", 0.0)),
                    float(getattr(bb, "b", 0.0)),
                )

        img_bytes = None
        if hasattr(pic, "get_image"):
            try:
                pil_img = pic.get_image(document)
                if pil_img:
                    buf = io.BytesIO()
                    pil_img.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
            except Exception:
                img_bytes = None
        elif hasattr(pic, "image"):
            img_attr = pic.image
            if hasattr(img_attr, "pil_image"):
                img_attr = img_attr.pil_image
            if hasattr(img_attr, "save"):
                try:
                    buf = io.BytesIO()
                    img_attr.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                except Exception:
                    img_bytes = None
            elif isinstance(img_attr, bytes):
                img_bytes = img_attr

        checksum = (
            hashlib.sha256(img_bytes).hexdigest()
            if img_bytes
            else hashlib.sha256(f"{source_name}:pic:{idx}".encode()).hexdigest()
        )
        visual_assets.append(
            VisualAsset(
                asset_id=f"va_{checksum[:16]}",
                asset_key=checksum,
                media_type="image/png",
                checksum=checksum,
                page_number=page_no or 1,
                bounding_box=bbox,
                data=img_bytes,
                metadata={"picture_index": idx},
            )
        )

    return markdown, raw_json, metadata, visual_assets


def _coerce_markitdown_result(converted: Any) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if isinstance(converted, ConversionResult):
        return converted.markdown, converted.raw_json, dict(converted.metadata)
    if isinstance(converted, dict):
        markdown = str(converted.get("markdown") or converted.get("text_content") or "")
        raw_json_payload = dict(converted.get("raw_json") or converted)
        metadata = dict(converted.get("metadata") or {})
        return markdown, raw_json_payload, metadata

    markdown = str(getattr(converted, "markdown", None) or getattr(converted, "text_content", None) or converted)
    metadata = dict(getattr(converted, "metadata", {}) or {})
    return markdown, {"markitdown": True}, metadata


def _format_from_path(path: Path, media_type: str | None) -> str:
    if media_type:
        return media_type
    suffix = path.suffix.lower().lstrip(".")
    return suffix or "text"


def _diagram_to_markdown_and_raw(path: Path, text: str) -> tuple[str, dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".mmd", ".mermaid"}:
        fence = "mermaid"
    elif suffix in {".puml", ".plantuml", ".iuml"}:
        fence = "plantuml"
    else:
        fence = "structurizr"
    title = path.stem.replace("-", " ").replace("_", " ").title()
    md = f"# {title}\n\n```{fence}\n{text.strip()}\n```\n"
    raw_extra = {"diagram_type": fence, "diagram_source": text.strip()}
    return md, raw_extra
