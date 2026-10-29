from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from margin_db.model_catalog_models import ModelConfiguration
from margin_db.model_registry import ActiveModelConfig
from margin_inference import InferenceClient, InferencePolicyError, InferenceUnavailableError
from margin_ingest.contracts import NormalizedDocument
from margin_ingest.indexing import SourceIndex, SourceTextElement
from margin_ingest.vision import VisionConstraintError, check_vision_model_constraints
from margin_schema.text_normalization import normalize_canonical_text

DOCUMENT_TEXT_EXTRACTION_PROMPT = (
    "Transcribe the entire attached document verbatim into Markdown, "
    "preserving headings, tables, lists, and code blocks. Do not summarize."
)


@dataclass(frozen=True)
class ConversionQuality:
    score: float
    text_coverage: float
    table_coverage: float
    has_images_or_diagrams: bool
    ocr_needed: bool
    section_tree_confidence: float
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


def expected_source_manifest(document: NormalizedDocument) -> dict[str, object]:
    """Return the converter-native page and element manifest used by quality gates."""

    return {
        "pages": [
            {
                "page_number": page.page_number,
                "checksum": page.checksum,
                "element_ids": [element.element_id for element in page.elements],
            }
            for page in document.pages
        ],
        "elements": [
            {
                "id": element.element_id,
                "kind": element.kind,
                "ordinal": element.ordinal,
                "page_number": element.page_number,
                "parent_id": element.parent_id,
                "char_start": element.char_start,
                "char_end": element.char_end,
                "asset_key": element.asset_key,
                "metadata": element.metadata,
            }
            for element in document.elements
        ],
        "asset_keys": [asset.asset_key for asset in document.visual_assets],
    }


def validate_normalized_document(document: NormalizedDocument) -> tuple[str, ...]:
    """Validate structural and evidence references before persistence."""

    errors: list[str] = []
    page_numbers = [page.page_number for page in document.pages]
    if not page_numbers:
        errors.append("normalized document has no pages")
    if len(page_numbers) != len(set(page_numbers)):
        errors.append("duplicate page numbers")
    element_ids = [element.element_id for element in document.elements]
    if len(element_ids) != len(set(element_ids)):
        errors.append("duplicate element ids")
    if [element.ordinal for element in document.elements] != list(range(len(document.elements))):
        errors.append("element ordinals are not contiguous")
    element_id_set = set(element_ids)
    page_id_sets: dict[int, set[str]] = {}
    for page in document.pages:
        page_ids = [element.element_id for element in page.elements]
        if len(page_ids) != len(set(page_ids)):
            errors.append(f"page {page.page_number} contains duplicate element ids")
        unknown_ids = set(page_ids) - element_id_set
        if unknown_ids:
            errors.append(f"page {page.page_number} contains unknown elements")
        page_id_sets[page.page_number] = set(page_ids)
        expected_checksum = sha256(page.text.encode("utf-8")).hexdigest()
        if not page.checksum:
            errors.append(f"page {page.page_number} has no checksum")
        elif page.text and page.checksum != expected_checksum:
            errors.append(f"page {page.page_number} checksum does not match page text")
    element_page_membership: dict[str, int] = {}
    for page_number, page_element_ids in page_id_sets.items():
        for element_id in page_element_ids:
            if element_id in element_page_membership:
                errors.append(f"element {element_id} appears on multiple pages")
            element_page_membership[element_id] = page_number
    for element in document.elements:
        if element.parent_id is not None and element.parent_id not in element_id_set:
            errors.append(f"element {element.element_id} has an unknown parent")
        if element.char_start < 0 or element.char_end < element.char_start or element.char_end > len(document.text):
            errors.append(f"element {element.element_id} has an invalid text range")
        if element.page_number is not None and element.page_number not in page_id_sets:
            errors.append(f"element {element.element_id} references an unknown page")
        elif element.page_number is not None and element.element_id not in page_id_sets[element.page_number]:
            errors.append(f"element {element.element_id} is missing from its page")
        elif element.page_number is not None and element_page_membership.get(element.element_id) != element.page_number:
            errors.append(f"element {element.element_id} has inconsistent page membership")
    asset_keys = [asset.asset_key for asset in document.visual_assets]
    if len(asset_keys) != len(set(asset_keys)):
        errors.append("duplicate visual asset keys")
    asset_key_set = set(asset_keys)
    for element in document.elements:
        if element.asset_key and element.asset_key not in asset_key_set:
            errors.append(f"element {element.element_id} references an unknown asset")
    return tuple(dict.fromkeys(errors))


def extract_document_text(
    source_bytes: bytes,
    *,
    model: ModelConfiguration | ActiveModelConfig,
    page_number: int | None = None,
    domain_id: str | None = None,
    sensitivity: str | None = None,
) -> str:
    """Extract one document page with the configured vision/OCR model."""
    if page_number is not None and _document_media_type(source_bytes) == "application/pdf":
        request_bytes = _pdf_page_png_bytes(source_bytes, page_number=page_number)
        media_type = "image/png"
    else:
        request_bytes = source_bytes
        media_type = _document_media_type(request_bytes)

    try:
        check_vision_model_constraints(
            model,
            domain_id=domain_id,
            sensitivity=sensitivity,
            media_type=media_type,
            payload_size_bytes=len(request_bytes),
            require_image_input=media_type.startswith("image/"),
        )
    except VisionConstraintError as exc:
        raise InferencePolicyError(str(exc)) from exc

    encoded = base64.b64encode(request_bytes).decode("ascii")
    client = InferenceClient(
        base_url=model.endpoint_url,
        model=model.model_name,
        secret_reference=model.secret_reference,
        timeout_seconds=120,
    )
    prompt_text = DOCUMENT_TEXT_EXTRACTION_PROMPT + (
        f" Extract only page {page_number}." if page_number is not None else ""
    )
    response = client.chat_completions(
        messages=[
            {"role": "system", "content": "Extract document text precisely and preserve its structure."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}},
                ],
            },
        ],
        max_tokens=model.max_tokens,
        event="document_text_extraction",
        redact_content=True,
    )
    text = response.content.strip()
    if not text:
        raise InferenceUnavailableError("vision/OCR model returned empty document text")
    return text


def _pdf_page_png_bytes(source_bytes: bytes, *, page_number: int) -> bytes:
    if page_number < 1:
        raise InferenceUnavailableError(f"invalid PDF page number for document text extraction: {page_number}")
    try:
        import fitz
    except ImportError as exc:
        raise InferenceUnavailableError("PDF page image extraction requires PyMuPDF") from exc

    try:
        document = fitz.open(stream=source_bytes, filetype="pdf")
        page_index = page_number - 1
        if page_index >= document.page_count:
            raise InferenceUnavailableError(f"PDF page {page_number} is outside the document page range")
        page = document.load_page(page_index)
        matrix = fitz.Matrix(2, 2)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        return bytes(pixmap.tobytes("png"))
    except InferenceUnavailableError:
        raise
    except Exception as exc:
        raise InferenceUnavailableError(f"PDF page {page_number} could not be rendered for extraction") from exc
    finally:
        if "document" in locals():
            document.close()


def _document_media_type(source_bytes: bytes) -> str:
    if source_bytes[:5] == b"%PDF-":
        return "application/pdf"
    if source_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if source_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if source_bytes[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if source_bytes[:4] == b"RIFF" and source_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def score_conversion_quality(
    markdown: str, source_index: SourceIndex, source_bytes: bytes | None = None, metadata: dict[str, Any] | None = None
) -> ConversionQuality:
    metadata = metadata or {}
    warnings: list[str] = []
    text_chars = sum(1 for char in markdown if char.isalnum())
    byte_length = len(source_bytes) if source_bytes is not None else int(metadata.get("byte_length") or 0)
    raw_json = metadata.get("raw_json")
    structured_fragments = _structured_text_fragments(raw_json)
    if structured_fragments:
        normalized_markdown = _normalize_text(markdown)
        matched_fragments = sum(
            1 for fragment in structured_fragments if _normalize_text(fragment) in normalized_markdown
        )
        text_coverage = _ratio(matched_fragments, len(structured_fragments))
    else:
        text_coverage = _ratio(text_chars, byte_length) if byte_length else min(1.0, text_chars / 2000)

    table_spans = [span for span in source_index.spans if span.span_type in {"table", "table_row", "table_header"}]
    structured_table_count = _structured_collection_count(raw_json, "tables")
    table_markers = markdown.count("|---") + markdown.count("| ---")
    if structured_table_count:
        table_coverage = _ratio(len(table_spans), structured_table_count)
    else:
        table_coverage = 1.0 if table_markers or table_spans else 0.0

    structured_image_count = sum(
        _structured_collection_count(raw_json, key) for key in ("pictures", "images", "figures")
    )
    has_images_or_diagrams = structured_image_count > 0 or any(
        span.span_type == "figure" or span.metadata.get("figure_type") == "diagram" for span in source_index.spans
    )
    has_images_or_diagrams = has_images_or_diagrams or bool(
        re.search(r"!\[[^\]]*\]\([^)]*\)|\b(?:figure|diagram):", markdown, re.IGNORECASE)
    )
    source_format = str(metadata.get("format") or metadata.get("source_format") or "").lower()
    source_page_count = _source_page_count(metadata, raw_json)
    indexed_pages = {span.page for span in source_index.spans if span.page is not None}
    missing_page_count = max(0, source_page_count - len(indexed_pages)) if indexed_pages else 0
    ocr_needed = source_format.endswith("pdf") and (
        text_chars < 80 or (bool(structured_fragments) and text_coverage < 0.5)
    )

    section_tree_confidence = _section_confidence(source_index)
    if text_coverage < 0.05:
        warnings.append("low_text_coverage")
    if ocr_needed:
        warnings.append("ocr_may_be_needed")
    if missing_page_count:
        warnings.append("missing_converted_pages")
    if structured_table_count and table_coverage < 1.0:
        warnings.append("missing_or_malformed_tables")
    if structured_image_count and not any(span.span_type == "figure" for span in source_index.spans):
        warnings.append("unreconciled_structured_images")
    if section_tree_confidence < 0.5:
        warnings.append("weak_section_tree")
    if source_index.warnings:
        warnings.extend(source_index.warnings)

    score = (
        (0.45 * min(1.0, text_coverage))
        + (0.2 * table_coverage)
        + (0.25 * section_tree_confidence)
        + (0.1 * (0.4 if ocr_needed else 1.0))
    )
    return ConversionQuality(
        score=round(score, 3),
        text_coverage=round(min(1.0, text_coverage), 3),
        table_coverage=round(table_coverage, 3),
        has_images_or_diagrams=has_images_or_diagrams,
        ocr_needed=ocr_needed,
        section_tree_confidence=round(section_tree_confidence, 3),
        warnings=tuple(dict.fromkeys(warnings)),
        details={
            "text_chars": text_chars,
            "byte_length": byte_length,
            "section_count": len(source_index.sections),
            "span_count": len(source_index.spans),
            "table_span_count": len(table_spans),
            "structured_text_fragment_count": len(structured_fragments),
            "structured_table_count": structured_table_count,
            "structured_image_count": structured_image_count,
            "source_page_count": source_page_count,
            "indexed_page_count": len(indexed_pages),
            "missing_page_count": missing_page_count,
        },
    )


def native_element_manifest(source_index: SourceIndex, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    raw_json = metadata.get("raw_json")
    elements: list[dict[str, Any]] = []
    if isinstance(raw_json, dict):
        for collection in ("texts", "tables", "pictures", "images", "figures"):
            for item in _structured_collection_items(raw_json, collection):
                text = " ".join(_text_values(item)).strip()
                page = item.get("page") or item.get("page_number") if isinstance(item, dict) else None
                elements.append(
                    {
                        "native_id": (
                            str(item.get("id") or item.get("native_id") or f"{collection}:{len(elements)}")
                            if isinstance(item, dict)
                            else f"{collection}:{len(elements)}"
                        ),
                        "kind": collection.removesuffix("s"),
                        "text": text,
                        "page_number": int(page) if isinstance(page, int | str) and str(page).isdigit() else None,
                        "structural_path": _structural_path(item, collection, len(elements)),
                    }
                )
    if elements:
        return _align_converter_elements(elements, source_index)
    return [
        {
            "native_id": element.native_id,
            "kind": element.kind,
            "text": element.text,
            "page_number": element.page,
            "structural_path": element.structural_path,
            "occurrence_ordinal": element.occurrence_ordinal,
            "source_element_ids": list(element.source_element_ids),
            "metadata": element.metadata,
        }
        for element in source_index.elements
    ]


def page_manifest(source_index: SourceIndex, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    expected_page_count = _source_page_count(metadata, metadata.get("raw_json"))
    indexed_by_number = {page.page_number: page for page in source_index.pages}
    structured_by_page: dict[int, list[str]] = {}
    raw_json = metadata.get("raw_json")
    if isinstance(raw_json, dict):
        for collection in ("texts", "tables", "pictures", "images", "figures"):
            for item in _structured_collection_items(raw_json, collection):
                if not isinstance(item, dict):
                    continue
                page = item.get("page") or item.get("page_number")
                text = " ".join(_text_values(item)).strip()
                if isinstance(page, int | str) and str(page).isdigit() and text:
                    structured_by_page.setdefault(int(page), []).append(text)
    if expected_page_count <= 0:
        return []
    pages: list[dict[str, Any]] = []
    for page_number in range(1, expected_page_count + 1):
        indexed = indexed_by_number.get(page_number)
        structured_text = "\n".join(structured_by_page.get(page_number, []))
        pages.append(
            {
                "page_number": page_number,
                "checksum": (
                    indexed.checksum
                    if indexed
                    else sha256((structured_text or f"missing:{page_number}").encode()).hexdigest()
                ),
                "classification": (
                    indexed.classification if indexed else ("text_extracted" if structured_text else "ocr_required")
                ),
                "retry_count": max(
                    0,
                    int((metadata.get("page_ocr_outcomes") or {}).get(str(page_number), {}).get("attempt_count", 0))
                    - 1,
                ),
            }
        )
    return pages


def _align_converter_elements(elements: list[dict[str, Any]], source_index: SourceIndex) -> list[dict[str, Any]]:
    spans_by_key: dict[tuple[int | None, str], list[SourceTextElement]] = {}
    for span in source_index.spans:
        key = span.page, sha256(normalize_canonical_text(span.text).encode("utf-8")).hexdigest()
        spans_by_key.setdefault(key, []).append(span)
    occurrences: dict[tuple[int | None, str], int] = {}
    aligned: list[dict[str, Any]] = []
    for element in elements:
        normalized = normalize_canonical_text(str(element.get("text") or ""))
        key = element.get("page_number"), sha256(normalized.encode("utf-8")).hexdigest()
        candidates = spans_by_key.get(key, []) or spans_by_key.get((None, key[1]), [])
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        match = candidates[occurrence] if occurrence < len(candidates) else None
        aligned.append(
            {
                **element,
                "occurrence_ordinal": occurrence,
                "source_element_ids": [match.span_id] if match is not None else [],
                "alignment_reason": None if match is not None else "converter element could not be aligned uniquely",
            }
        )
    return aligned


def _structural_path(value: object, collection: str, ordinal: int) -> str:
    if isinstance(value, dict):
        for key in ("path", "structural_path", "xpath", "json_path"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return f"{collection}/{ordinal}"


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, numerator / denominator)


def _section_confidence(source_index: SourceIndex) -> float:
    if not source_index.sections:
        return 0.0
    if "no_headings_root_section_created" in source_index.warnings:
        return 0.35
    titled = sum(1 for section in source_index.sections if section.title and section.title != "Document")
    span_factor = 1.0 if source_index.spans else 0.4
    hierarchy_factor = 1.0 if any(section.level > 1 for section in source_index.sections) else 0.8
    return min(1.0, (titled / len(source_index.sections)) * span_factor * hierarchy_factor)


def _structured_text_fragments(value: object) -> tuple[str, ...]:
    fragments: list[str] = []

    def collect(current: object, key: str | None = None) -> None:
        if isinstance(current, dict):
            for child_key, child in current.items():
                collect(child, str(child_key).casefold())
        elif isinstance(current, list):
            for child in current:
                collect(child, key)
        elif key in {"text", "text_content", "orig", "content"} and isinstance(current, str):
            cleaned = re.sub(r"\s+", " ", current).strip()
            if len(cleaned) >= 8:
                fragments.append(cleaned)

    collect(value)
    return tuple(dict.fromkeys(fragments))


def _structured_collection_count(value: object, collection_name: str) -> int:
    if not isinstance(value, dict):
        return 0
    count = 0
    for key, child in value.items():
        if str(key).casefold() == collection_name and isinstance(child, list | dict):
            count += len(child)
        elif isinstance(child, dict):
            count += _structured_collection_count(child, collection_name)
    return count


def _structured_collection_items(value: object, collection_name: str) -> list[object]:
    items: list[object] = []
    if not isinstance(value, dict):
        return items
    for key, child in value.items():
        if str(key).casefold() == collection_name:
            if isinstance(child, list):
                items.extend(child)
            elif isinstance(child, dict):
                items.extend(child.values())
        elif isinstance(child, dict):
            items.extend(_structured_collection_items(child, collection_name))
    return items


def _text_values(value: object) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        cleaned = re.sub(r"\s+", " ", value).strip()
        if cleaned:
            values.append(cleaned)
    elif isinstance(value, list):
        for child in value:
            values.extend(_text_values(child))
    elif isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in {"text", "text_content", "orig", "content", "caption"}:
                values.extend(_text_values(child))
    return values


def _source_page_count(metadata: dict[str, Any], raw_json: object) -> int:
    source_metadata = metadata.get("source_metadata")
    if isinstance(source_metadata, dict) and source_metadata.get("page_count"):
        return int(source_metadata["page_count"])
    if metadata.get("page_count"):
        return int(metadata["page_count"])
    return _structured_collection_count(raw_json, "pages")


def _normalize_text(value: str) -> str:
    return normalize_canonical_text(value)
