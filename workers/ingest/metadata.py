from __future__ import annotations

import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from margin_db.source_models import DUBLIN_CORE_FIELDS


@dataclass(frozen=True)
class SourceMetadata:
    title: str
    creator: str | None
    date: str | None
    format: str
    page_count: int | None
    language: str | None
    document_type: str
    dublin_core: dict[str, Any] = field(default_factory=dict)
    hints: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def extract_metadata(
    path: str | Path,
    markdown: str = "",
    conversion_metadata: dict[str, Any] | None = None,
    media_type: str | None = None,
) -> SourceMetadata:
    source = Path(path)
    conversion_metadata = conversion_metadata or {}
    suffix_format = media_type or source.suffix.lower().lstrip(".") or "text"
    warnings: list[str] = []

    embedded = _embedded_metadata(source, conversion_metadata)
    title = (
        _clean_string(conversion_metadata.get("title"))
        or _clean_string(embedded.get("title"))
        or _title_from_markdown(markdown)
        or source.stem.replace("-", " ").replace("_", " ").title()
    )
    creator = _clean_string(conversion_metadata.get("creator")) or _clean_string(embedded.get("creator"))
    date = _clean_string(conversion_metadata.get("date")) or _clean_string(embedded.get("date"))
    language = _clean_string(conversion_metadata.get("language")) or _guess_language(markdown)
    page_count, page_count_verified = _page_count(source, markdown, conversion_metadata)
    document_type, hints = _document_type(source, markdown, conversion_metadata)

    if page_count is None and source.suffix.lower() in {".pdf", ".pptx"}:
        warnings.append("page_count_not_available")
    elif page_count_verified is False:
        warnings.append("converter_page_count_mismatch")

    dublin_core = normalize_dublin_core(
        {
            "title": title,
            "creator": creator,
            "subject": conversion_metadata.get("subject"),
            "description": conversion_metadata.get("description"),
            "publisher": conversion_metadata.get("publisher"),
            "contributor": conversion_metadata.get("contributor"),
            "date": date,
            "type": document_type,
            "format": suffix_format,
            "identifier": conversion_metadata.get("identifier") or str(source),
            "source": conversion_metadata.get("source") or str(source),
            "language": language,
            "relation": conversion_metadata.get("relation"),
            "coverage": conversion_metadata.get("coverage"),
            "rights": conversion_metadata.get("rights"),
        }
    )
    return SourceMetadata(
        title=title,
        creator=creator,
        date=date,
        format=suffix_format,
        page_count=page_count,
        language=language,
        document_type=document_type,
        dublin_core=dublin_core,
        hints=tuple(hints),
        warnings=tuple(warnings),
    )


def normalize_dublin_core(values: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return canonical Dublin Core map with all 15 plain keys."""
    raw_values = values or {}
    normalized: dict[str, Any] = {}
    for dc_field in DUBLIN_CORE_FIELDS:
        value = raw_values.get(dc_field)
        if value is None:
            value = raw_values.get(f"dc:{dc_field}")
        normalized[dc_field] = _clean_string(value)
    return normalized


def _embedded_metadata(path: Path, conversion_metadata: dict[str, Any]) -> dict[str, Any]:
    if path.suffix.lower() == ".docx" and zipfile.is_zipfile(path):
        return _office_core_properties(path)
    if path.suffix.lower() == ".pptx" and zipfile.is_zipfile(path):
        return _office_core_properties(path)
    if path.suffix.lower() == ".pdf" and path.exists():
        text = path.read_bytes().decode("latin-1", errors="ignore")
        return {
            "title": _pdf_info_field(text, "Title"),
            "creator": _pdf_info_field(text, "Author") or _pdf_info_field(text, "Creator"),
            "date": _pdf_info_field(text, "CreationDate"),
        }
    return {key: conversion_metadata.get(key) for key in ("title", "creator", "date", "language")}


def _office_core_properties(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        if "docProps/core.xml" not in archive.namelist():
            return {}
        root = ElementTree.fromstring(archive.read("docProps/core.xml"))
    fields = {}
    for child in root:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag in {"title", "creator", "created", "modified"}:
            fields["date" if tag in {"created", "modified"} else tag] = child.text
    return fields


def _pdf_info_field(text: str, field: str) -> str | None:
    match = re.search(rf"/{field}\s*\(([^)]*)\)", text)
    return match.group(1).strip() if match else None


def _title_from_markdown(markdown: str) -> str | None:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return None


def _page_count(path: Path, markdown: str, conversion_metadata: dict[str, Any]) -> tuple[int | None, bool | None]:
    explicit = conversion_metadata.get("page_count") or conversion_metadata.get("pages")
    parsed: int | None = None
    if path.suffix.lower() == ".pdf" and path.exists():
        text = path.read_bytes().decode("latin-1", errors="ignore")
        match = re.search(r"/Count\s+(\d+)", text)
        if match:
            parsed = int(match.group(1))
    if path.suffix.lower() == ".pptx" and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            parsed = len([name for name in archive.namelist() if name.startswith("ppt/slides/slide")])
    if parsed is not None:
        return parsed, not isinstance(explicit, int) or explicit == parsed
    if isinstance(explicit, int):
        return explicit, None
    page_markers = re.findall(r"<!--\s*page:\s*\d+\s*-->", markdown, flags=re.IGNORECASE)
    return (max(1, len(page_markers)), None) if page_markers else (None, None)


def _document_type(path: Path, markdown: str, conversion_metadata: dict[str, Any]) -> tuple[str, list[str]]:
    lowered = f"{path.name}\n{markdown[:2000]}".lower()
    hints: list[str] = []
    if "openapi:" in lowered or '"openapi"' in lowered or "swagger:" in lowered:
        return "api_contract", ["openapi"]
    if "asyncapi:" in lowered or '"asyncapi"' in lowered:
        return "event_contract", ["asyncapi"]
    if path.suffix.lower() in {".dsl", ".mmd", ".mermaid", ".puml", ".plantuml"}:
        return "diagram", ["diagram_as_code"]
    if any(token in lowered for token in ("hld", "lld", "design document", "architecture design")):
        hints.append("design_document")
        return "design_document", hints
    if "adr" in lowered or re.search(r"\bstatus:\s*(accepted|proposed|superseded)", lowered):
        hints.append("adr")
        return "adr", hints
    return str(conversion_metadata.get("document_type") or "source_document"), hints


def _guess_language(markdown: str) -> str | None:
    text = re.sub(r"\s+", "", markdown)
    if not text:
        return None
    ascii_ratio = sum(1 for char in text if ord(char) < 128) / len(text)
    return "en" if ascii_ratio > 0.9 else None


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
