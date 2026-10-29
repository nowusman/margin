from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Any

from margin_ingest.contracts import NormalizedDocument, NormalizedElement, NormalizedPage, VisualAsset
from margin_schema.ids import element_id as make_element_id


@dataclass(frozen=True)
class SourceTreeNode:
    section_id: str
    title: str
    level: int
    path: tuple[str, ...]
    ordinal: int
    parent_id: str | None
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class SourceTextElement:
    span_id: str
    section_id: str
    span_type: str
    text: str
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int
    page: int | None = None
    bounding_box: tuple[float, float, float, float] | None = None
    asset_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NativeSourceElement:
    native_id: str
    kind: str
    text: str
    structural_path: str
    occurrence_ordinal: int
    source_element_ids: tuple[str, ...]
    page: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SourcePageEntry:
    page_number: int
    checksum: str
    classification: str


@dataclass(frozen=True)
class SourceIndex:
    document_id: str
    sections: tuple[SourceTreeNode, ...]
    spans: tuple[SourceTextElement, ...]
    elements: tuple[NativeSourceElement, ...] = ()
    pages: tuple[SourcePageEntry, ...] = ()
    warnings: tuple[str, ...] = ()


class LocalSourceTreeAdapter:
    """Small deterministic local source-tree adapter over Markdown."""

    name = "local-source-tree-deterministic"

    def build(self, markdown: str, document_id: str) -> SourceIndex:
        return build_source_index(markdown, document_id=document_id)


def build_source_index(markdown: str, document_id: str) -> SourceIndex:
    lines = markdown.splitlines()
    offsets = _line_offsets(markdown)
    heading_rows = _heading_rows(lines)
    warnings: list[str] = []

    if not heading_rows:
        heading_rows = [(0, 1, "Document")]
        warnings.append("no_headings_root_section_created")

    sections: list[SourceTreeNode] = []
    stack: list[SourceTreeNode] = []
    seen_paths: set[tuple[str, ...]] = set()
    for ordinal, (line_index, level, title) in enumerate(heading_rows, start=1):
        next_start = heading_rows[ordinal][0] if ordinal < len(heading_rows) else len(lines)
        while stack and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        if parent and level > parent.level + 1:
            warnings.append("skipped_heading_level")
        path = (*parent.path, title) if parent else (title,)
        if path in seen_paths:
            warnings.append("duplicate_heading_path")
        seen_paths.add(path)
        section = SourceTreeNode(
            section_id=_stable_node_id(
                document_id=document_id, kind="sec", path=path, line_number=line_index + 1, title=title
            ),
            title=title,
            level=level,
            path=path,
            ordinal=ordinal,
            parent_id=parent.section_id if parent else None,
            start_line=line_index + 1,
            end_line=next_start,
            start_offset=offsets[line_index] if line_index < len(offsets) else 0,
            end_offset=offsets[next_start] if next_start < len(offsets) else len(markdown),
        )
        sections.append(section)
        stack.append(section)

    spans = _generate_spans(markdown, lines, offsets, sections, document_id)
    elements = _elements_for_spans(document_id, sections, spans)
    pages = _page_entries(markdown, spans)
    return SourceIndex(
        document_id=document_id,
        sections=tuple(sections),
        spans=tuple(spans),
        elements=tuple(elements),
        pages=tuple(pages),
        warnings=tuple(warnings),
    )


def is_placeholder_figure_description(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    patterns = (
        re.compile(r"^\[(?:image|figure|picture|photo|illustration)\]$", re.IGNORECASE),
        re.compile(r"^\[(?:image|figure|picture|photo|illustration):\s*[^\]]*\]$", re.IGNORECASE),
        re.compile(r"^figure\s*\d+\.?$", re.IGNORECASE),
        re.compile(r"^fig\.\s*\d+\.?$", re.IGNORECASE),
        re.compile(r"^image\s*\d+\.?$", re.IGNORECASE),
        re.compile(r"^!\[(?:image|figure|picture|photo)?\]\([^)]*\)$", re.IGNORECASE),
    )
    return any(p.match(stripped) for p in patterns)


def construct_normalized_document(
    text: str,
    *,
    source_name: str,
    format: str,
    raw_json: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    visual_assets: Sequence[VisualAsset] = (),
    warnings: Sequence[str] = (),
) -> NormalizedDocument:
    source_index = build_source_index(text, document_id=source_name)
    sections_by_id = {sec.section_id: sec for sec in source_index.sections}
    merged_warnings = list(warnings)
    merged_warnings.extend(source_index.warnings)

    raw_pages_info: list[dict[str, Any]] = []
    if raw_json and isinstance(raw_json.get("pages"), list):
        for p in raw_json["pages"]:
            if isinstance(p, dict):
                raw_pages_info.append(p)

    elements: list[NormalizedElement] = []
    element_ids = {span.span_id for span in source_index.spans}
    for idx, span in enumerate(source_index.spans):
        section = sections_by_id.get(span.section_id)
        structural_path = "/".join(section.path) if section else ""
        page_number = span.page if span.page is not None else _raw_element_page(span.text, raw_pages_info)
        elem_metadata: dict[str, Any] = {
            "structural_path": structural_path,
            "section_id": span.section_id,
            "span_id": span.span_id,
            **span.metadata,
        }
        parent_id = span.metadata.get("parent_element_id")
        if parent_id not in element_ids:
            parent_id = None
        elements.append(
            NormalizedElement(
                element_id=span.span_id,
                kind=span.span_type,
                text=span.text,
                ordinal=idx,
                page_number=page_number or 1,
                parent_id=parent_id,
                char_start=span.start_offset,
                char_end=span.end_offset,
                bounding_box=span.bounding_box,
                asset_key=span.asset_key,
                confidence=1.0,
                metadata=elem_metadata,
            )
        )

    # Include any visual assets not already represented in elements
    existing_asset_keys = {e.asset_key for e in elements if e.asset_key}
    for va in visual_assets:
        if va.asset_key not in existing_asset_keys:
            existing_asset_keys.add(va.asset_key)
            elements.append(
                NormalizedElement(
                    element_id=make_element_id(
                        {"source_name": source_name, "asset_id": va.asset_id, "asset_key": va.asset_key}
                    ),
                    kind="picture",
                    text="",
                    ordinal=len(elements),
                    page_number=va.page_number if va.page_number is not None else 1,
                    bounding_box=va.bounding_box,
                    asset_key=va.asset_key,
                    confidence=1.0,
                    metadata={"media_type": va.media_type, "checksum": va.checksum, **va.metadata},
                )
            )

    # Determine pages
    pages: list[NormalizedPage] = []
    if source_index.pages:
        raw_page_numbers: set[int] = set()
        for page in raw_pages_info:
            raw_page_number = page.get("number") or page.get("page_number")
            if isinstance(raw_page_number, int):
                raw_page_numbers.add(raw_page_number)
        indexed_pages = {page.page_number: page for page in source_index.pages}
        for p_num in sorted(set(indexed_pages) | raw_page_numbers):
            page_entry = indexed_pages.get(p_num)
            page_elems = tuple(e for e in elements if e.page_number == p_num)
            page_text = _page_text(text, p_num)
            raw_page = next(
                (page for page in raw_pages_info if (page.get("number") or page.get("page_number")) == p_num), {}
            )
            if not page_text and isinstance(raw_page, dict):
                page_text = _raw_page_text(raw_page)
            page_checksum = (
                page_entry.checksum if page_entry is not None else sha256(page_text.encode("utf-8")).hexdigest()
            )
            classification = (
                page_entry.classification
                if page_entry is not None
                else ("text_extracted" if page_text.strip() else "ocr_required")
            )
            pages.append(
                NormalizedPage(
                    page_number=p_num,
                    checksum=page_checksum,
                    text=page_text,
                    elements=page_elems,
                    metadata=(
                        {"classification": classification, **raw_page.get("metadata", {})}
                        if isinstance(raw_page.get("metadata"), dict)
                        else {"classification": classification}
                    ),
                )
            )
    elif raw_pages_info:
        for p_info in raw_pages_info:
            p_num = int(p_info.get("number") or p_info.get("page_number") or len(pages) + 1)
            page_elems = tuple(e for e in elements if e.page_number == p_num)
            page_text = _raw_page_text(p_info) or "\n".join(e.text for e in page_elems if e.text)
            p_checksum = sha256(page_text.encode("utf-8")).hexdigest()
            classification = "text_extracted" if page_text.strip() else "ocr_required"
            pages.append(
                NormalizedPage(
                    page_number=p_num,
                    checksum=p_checksum,
                    text=page_text,
                    elements=page_elems,
                    metadata={"classification": classification, **p_info},
                )
            )
    else:
        # Default single page
        p_checksum = sha256(text.encode("utf-8")).hexdigest()
        classification = "text_extracted" if text.strip() else "ocr_required"
        pages.append(
            NormalizedPage(
                page_number=1,
                checksum=p_checksum,
                text=text,
                elements=tuple(elements),
                metadata={"classification": classification},
            )
        )

    doc_metadata = dict(metadata or {})
    return NormalizedDocument(
        source_name=source_name,
        format=format,
        text=text,
        pages=tuple(pages),
        elements=tuple(elements),
        visual_assets=tuple(visual_assets),
        metadata=doc_metadata,
        warnings=tuple(dict.fromkeys(merged_warnings)),
    )


def _generate_spans(
    markdown: str, lines: list[str], offsets: list[int], sections: list[SourceTreeNode], document_id: str
) -> list[SourceTextElement]:
    raw_spans: list[SourceTextElement] = []
    current_page: int | None = None
    line_index = 0

    while line_index < len(lines):
        line = lines[line_index]
        page_match = re.match(r"<!--\s*page:\s*(\d+)\s*-->", line, re.IGNORECASE)
        if page_match:
            current_page = int(page_match.group(1))
            line_index += 1
            continue
        if not line.strip():
            line_index += 1
            continue
        if _is_table_separator(line):
            line_index += 1
            continue

        span_type = "paragraph"
        start = line_index
        metadata: dict[str, str] = {}

        if _is_heading(line):
            span_type = "heading"
            end = start
            line_index += 1
        elif line.startswith("```"):
            span_type = "code"
            info = line[3:].strip()
            if info:
                metadata["language"] = info
                if info.lower() in {"mermaid", "plantuml", "structurizr", "dsl"}:
                    span_type = "figure"
                    metadata["figure_type"] = "diagram"
            line_index += 1
            while line_index < len(lines) and not lines[line_index].startswith("```"):
                line_index += 1
            end = min(line_index, len(lines) - 1)
            line_index += 1
        elif _is_table_separator(line):
            line_index += 1
            continue
        elif _is_table_row(line):
            span_type = "table_header" if _is_table_header(lines, line_index) else "table_row"
            end = start
            line_index += 1
        elif _is_bullet(line):
            span_type = "list_item"
            line_index += 1
            while line_index < len(lines) and lines[line_index].startswith("  ") and not _is_bullet(lines[line_index]):
                line_index += 1
            end = line_index - 1
        elif _is_figure_line(line):
            span_type = "figure_caption" if line.strip().casefold().startswith("caption:") else "figure"
            line_index += 1
            end = start
        elif line.strip().casefold().startswith("table:"):
            span_type = "table_caption"
            line_index += 1
            end = start
        else:
            while (
                line_index < len(lines)
                and lines[line_index].strip()
                and not _is_heading(lines[line_index])
                and not lines[line_index].startswith("```")
                and not _is_table_row(lines[line_index])
                and not _is_bullet(lines[line_index])
            ):
                line_index += 1
            end = line_index - 1

        text = "\n".join(lines[start : end + 1]).strip()
        if span_type in {"figure", "figure_caption"} and is_placeholder_figure_description(text):
            metadata["is_placeholder"] = "true"
            text = ""
        if text or (span_type == "figure" and metadata.get("is_placeholder")):
            section = _section_for_line(sections, start + 1)
            raw_spans.append(
                SourceTextElement(
                    span_id=_stable_span_id(
                        document_id=document_id,
                        section_id=section.section_id,
                        span_type=span_type,
                        start_line=start + 1,
                        end_line=end + 1,
                        start_offset=offsets[start] if start < len(offsets) else 0,
                        end_offset=_end_offset(markdown, lines, offsets, end),
                    ),
                    section_id=section.section_id,
                    span_type=span_type,
                    text=text,
                    start_line=start + 1,
                    end_line=end + 1,
                    start_offset=offsets[start] if start < len(offsets) else 0,
                    end_offset=_end_offset(markdown, lines, offsets, end),
                    page=current_page,
                    metadata=metadata,
                )
            )

    return _group_table_spans(raw_spans, document_id)


def _group_table_spans(spans: list[SourceTextElement], document_id: str) -> list[SourceTextElement]:
    """Expand contiguous Markdown tables into a stable parent/row/cell tree."""

    expanded: list[SourceTextElement] = []
    index = 0
    while index < len(spans):
        span = spans[index]
        if span.span_type not in {"table_header", "table_row"}:
            expanded.append(span)
            index += 1
            continue

        table_spans: list[SourceTextElement] = []
        while index < len(spans) and spans[index].span_type in {"table_header", "table_row"}:
            candidate = spans[index]
            if table_spans and candidate.section_id != table_spans[-1].section_id:
                break
            table_spans.append(candidate)
            index += 1

        table_id = _table_id(document_id, table_spans)
        caption = None
        if expanded:
            candidate_caption = expanded[-1]
            if (
                candidate_caption.span_type == "table_caption"
                and candidate_caption.page == table_spans[0].page
                and candidate_caption.section_id == table_spans[0].section_id
            ):
                caption = expanded.pop()
        header_span = next((item for item in table_spans if item.span_type == "table_header"), None)
        header_cells = _split_table_cells(header_span.text) if header_span else []
        repeated_header_ids = {
            item.span_id
            for item in table_spans
            if item is not header_span and header_cells and _split_table_cells(item.text) == header_cells
        }
        summary_spans = [item for item in table_spans if item.span_id not in repeated_header_ids]
        table_start = table_spans[0]
        table_end = table_spans[-1]
        table_parent = SourceTextElement(
            span_id=table_id,
            section_id=table_start.section_id,
            span_type="table",
            text=_table_summary_text(summary_spans, header_cells, caption=caption.text if caption else None),
            start_line=caption.start_line if caption else table_start.start_line,
            end_line=table_end.end_line,
            start_offset=caption.start_offset if caption else table_start.start_offset,
            end_offset=table_end.end_offset,
            page=table_start.page,
            bounding_box=table_start.bounding_box,
            asset_key=table_start.asset_key,
            metadata={
                "table_id": table_id,
                "table_role": "parent",
                "summary_only": True,
                "header_count": len(header_cells),
                "row_count": max(0, len(summary_spans) - (1 if header_span else 0)),
                "column_count": len(header_cells),
                "column_headers": header_cells,
                "caption": caption.text if caption else None,
            },
        )
        expanded.append(table_parent)
        if caption is not None:
            expanded.append(
                replace(
                    caption,
                    metadata={
                        **caption.metadata,
                        "table_id": table_id,
                        "table_role": "caption",
                        "parent_element_id": table_id,
                    },
                )
            )

        data_row_index = 0
        for table_span in table_spans:
            is_header = table_span is header_span
            is_repeated_header = table_span.span_id in repeated_header_ids
            if not is_header and not is_repeated_header:
                data_row_index += 1
            row_index = 0 if is_header or is_repeated_header else data_row_index
            row_id = table_span.span_id
            row_metadata = {
                **table_span.metadata,
                "table_id": table_id,
                "table_role": "header" if is_header else "repeated_header" if is_repeated_header else "row",
                "row_index": row_index,
                "column_count": len(header_cells) or len(_split_table_cells(table_span.text)),
                "column_headers": header_cells,
                "parent_element_id": table_id,
            }
            expanded.append(
                replace(
                    table_span,
                    span_type="table_header" if is_repeated_header else table_span.span_type,
                    metadata=row_metadata,
                )
            )
            cells = _split_table_cells_with_offsets(table_span.text, table_span.start_offset)
            for column_index, (cell_text, cell_start, cell_end) in enumerate(cells):
                cell_id = _table_child_id(
                    document_id,
                    table_id,
                    "header_cell" if is_header else "repeated_header_cell" if is_repeated_header else "cell",
                    row_index,
                    column_index,
                )
                expanded.append(
                    SourceTextElement(
                        span_id=cell_id,
                        section_id=table_span.section_id,
                        span_type="table_cell",
                        text=cell_text,
                        start_line=table_span.start_line,
                        end_line=table_span.end_line,
                        start_offset=cell_start,
                        end_offset=cell_end,
                        page=table_span.page,
                        bounding_box=table_span.bounding_box,
                        asset_key=table_span.asset_key,
                        metadata={
                            "table_id": table_id,
                            "table_role": (
                                "header_cell" if is_header else "repeated_header_cell" if is_repeated_header else "cell"
                            ),
                            "row_index": row_index,
                            "column_index": column_index,
                            "column_name": header_cells[column_index] if column_index < len(header_cells) else None,
                            "merged_cell": False,
                            "parent_element_id": row_id,
                        },
                    )
                )
    return expanded


def _table_id(document_id: str, spans: list[SourceTextElement]) -> str:
    first = spans[0]
    last = spans[-1]
    return make_element_id(
        {
            "document_id": document_id,
            "kind": "table",
            "section_id": first.section_id,
            "page": first.page,
            "start_offset": first.start_offset,
            "end_offset": last.end_offset,
        }
    )


def _table_child_id(document_id: str, table_id: str, kind: str, row_index: int, column_index: int) -> str:
    return make_element_id(
        {
            "document_id": document_id,
            "table_id": table_id,
            "kind": kind,
            "row_index": row_index,
            "column_index": column_index,
        }
    )


def _split_table_cells(text: str) -> list[str]:
    return [cell.strip() for cell in text.strip().strip("|").split("|")]


def _table_summary_text(spans: list[SourceTextElement], header_cells: list[str], *, caption: str | None = None) -> str:
    lines = [item.text for item in spans]
    if header_cells and len(lines) > 1:
        separator = "| " + " | ".join("---" for _ in header_cells) + " |"
        lines.insert(1, separator)
    return "\n".join(([caption] if caption else []) + lines)


def _split_table_cells_with_offsets(text: str, base_offset: int) -> list[tuple[str, int, int]]:
    stripped = text.strip()
    left_trim = len(text) - len(text.lstrip())
    content = stripped[1:-1] if stripped.startswith("|") and stripped.endswith("|") else stripped
    content_start = base_offset + left_trim + (1 if stripped.startswith("|") else 0)
    cells: list[tuple[str, int, int]] = []
    cursor = 0
    for raw_cell in content.split("|"):
        leading = len(raw_cell) - len(raw_cell.lstrip())
        value = raw_cell.strip()
        start = content_start + cursor + leading
        cells.append((value, start, start + len(value)))
        cursor += len(raw_cell) + 1
    return cells


def _page_text(text: str, page_number: int) -> str:
    """Return the original normalized text for one explicit page marker."""

    lines = text.splitlines()
    marker = re.compile(r"^<!--\s*page:\s*(\d+)\s*-->$", re.IGNORECASE)
    page_lines: list[str] = []
    current_page = 1
    found_marker = False
    for line in lines:
        match = marker.match(line.strip())
        if match:
            found_marker = True
            current_page = int(match.group(1))
            continue
        if current_page == page_number:
            page_lines.append(line)
    if not found_marker:
        return text
    return "\n".join(page_lines).strip()


def _raw_page_text(page_info: dict[str, Any]) -> str:
    for key in ("text", "markdown", "content", "text_content"):
        value = page_info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _raw_element_page(text: str, raw_pages_info: list[dict[str, Any]]) -> int | None:
    normalized_text = text.strip()
    if not normalized_text:
        return None
    for page_info in raw_pages_info:
        page_number = page_info.get("number") or page_info.get("page_number")
        if not isinstance(page_number, int):
            continue
        page_text = _raw_page_text(page_info)
        if page_text and normalized_text in page_text:
            return page_number
    return None


def _heading_rows(lines: list[str]) -> list[tuple[int, int, str]]:
    headings = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    return headings


def _stable_node_id(*, document_id: str, kind: str, path: tuple[str, ...], line_number: int, title: str) -> str:
    digest = sha256(
        "\0".join((document_id, kind, "/".join(path), str(line_number), title)).casefold().encode("utf-8")
    ).hexdigest()[:16]
    return f"{document_id}:{kind}:{digest}"


def _stable_span_id(
    *,
    document_id: str,
    section_id: str,
    span_type: str,
    start_line: int,
    end_line: int,
    start_offset: int,
    end_offset: int,
) -> str:
    return make_element_id(
        {
            "document_id": document_id,
            "section_id": section_id,
            "span_type": span_type,
            "start_line": start_line,
            "end_line": end_line,
            "start_offset": start_offset,
            "end_offset": end_offset,
        }
    )


def _line_offsets(markdown: str) -> list[int]:
    offsets = []
    offset = 0
    for line in markdown.splitlines(keepends=True):
        offsets.append(offset)
        offset += len(line)
    if not offsets:
        offsets.append(0)
    return offsets


def _section_for_line(sections: list[SourceTreeNode], line_number: int) -> SourceTreeNode:
    candidates = [section for section in sections if section.start_line <= line_number]
    return candidates[-1] if candidates else sections[0]


def _end_offset(markdown: str, lines: list[str], offsets: list[int], end_line_index: int) -> int:
    if end_line_index >= len(lines):
        return len(markdown)
    return offsets[end_line_index] + len(lines[end_line_index])


def _is_heading(line: str) -> bool:
    return bool(re.match(r"^#{1,6}\s+", line))


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_table_header(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    cells = [cell.strip() for cell in lines[index + 1].strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _is_table_separator(line: str) -> bool:
    if not _is_table_row(line):
        return False
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _is_bullet(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[-*+]|\d+\.)\s+", line))


def _is_figure_line(line: str) -> bool:
    stripped = line.strip().lower()
    return (
        stripped.startswith("![")
        or stripped.startswith("figure:")
        or stripped.startswith("caption:")
        or is_placeholder_figure_description(stripped)
    )


def _elements_for_spans(
    document_id: str, sections: list[SourceTreeNode], spans: list[SourceTextElement]
) -> list[NativeSourceElement]:
    sections_by_id = {section.section_id: section for section in sections}
    occurrences: dict[tuple[str, str, str], int] = {}
    elements: list[NativeSourceElement] = []
    for span in spans:
        section = sections_by_id[span.section_id]
        checksum = sha256(span.text.encode("utf-8")).hexdigest()
        structural_path = "/".join(section.path)
        occurrence_key = structural_path, span.span_type, checksum
        occurrence = occurrences.get(occurrence_key, 0)
        occurrences[occurrence_key] = occurrence + 1
        digest = sha256(
            "\0".join((document_id, structural_path, span.span_type, checksum, str(occurrence))).encode("utf-8")
        ).hexdigest()
        elements.append(
            NativeSourceElement(
                native_id=f"native_{digest}",
                kind=span.span_type,
                text=span.text,
                structural_path=structural_path,
                occurrence_ordinal=occurrence,
                source_element_ids=(span.span_id,),
                page=span.page,
                metadata=dict(span.metadata),
            )
        )
    return elements


def _page_entries(markdown: str, spans: list[SourceTextElement]) -> list[SourcePageEntry]:
    marker_pages = {
        int(match.group(1))
        for match in re.finditer(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", markdown, re.IGNORECASE | re.MULTILINE)
    }
    page_numbers = sorted(marker_pages | {span.page for span in spans if span.page is not None})
    if not page_numbers:
        return []
    entries: list[SourcePageEntry] = []
    for page_number in page_numbers:
        page_text = _page_text(markdown, page_number)
        entries.append(
            SourcePageEntry(
                page_number=page_number,
                checksum=sha256(page_text.encode("utf-8")).hexdigest(),
                classification="text_extracted" if page_text.strip() else "ocr_required",
            )
        )
    return entries


def compute_ocr_idempotency_key(
    *, source_version_id: str, page_number: int, asset_checksum: str, processor_model_id: str, processor_version: str
) -> str:
    key_payload = "\0".join(
        (source_version_id, str(page_number), asset_checksum, processor_model_id, processor_version)
    )
    return "ocr_idemp_" + sha256(key_payload.encode("utf-8")).hexdigest()


def reconcile_mixed_ocr_document(
    base_document: NormalizedDocument, ocr_pages: Mapping[int, str], *, metadata: Mapping[str, Any] | None = None
) -> NormalizedDocument:
    existing_pages = {p.page_number: p for p in base_document.pages}
    all_page_numbers = sorted(set(existing_pages.keys()) | set(ocr_pages.keys()))
    if not all_page_numbers:
        all_page_numbers = [1]

    updated_pages: list[NormalizedPage] = []
    for page_num in all_page_numbers:
        orig_page = existing_pages.get(page_num)
        orig_page_elems = (
            orig_page.elements
            if orig_page and orig_page.elements
            else tuple(e for e in base_document.elements if e.page_number == page_num)
        )
        if page_num in ocr_pages:
            ocr_text = ocr_pages[page_num].strip()
            page_width = orig_page.width if orig_page else None
            page_height = orig_page.height if orig_page else None
            page_meta = dict(orig_page.metadata) if orig_page else {}
            visual_elems = [e for e in orig_page_elems if e.kind in {"image", "picture", "figure"} or bool(e.asset_key)]

            ocr_elements: list[NormalizedElement] = []
            if ocr_text:
                page_text_with_marker = f"<!-- page: {page_num} -->\n\n{ocr_text}"
                page_index = build_source_index(
                    page_text_with_marker, document_id=f"{base_document.source_name}-p{page_num}"
                )
                page_element_ids = {item.span_id for item in page_index.spans}
                for span in page_index.spans:
                    parent_id = span.metadata.get("parent_element_id")
                    if parent_id not in page_element_ids:
                        parent_id = None
                    ocr_elements.append(
                        NormalizedElement(
                            element_id=span.span_id,
                            kind=span.span_type,
                            text=span.text,
                            ordinal=len(ocr_elements),
                            page_number=page_num,
                            parent_id=parent_id,
                            char_start=span.start_offset,
                            char_end=span.end_offset,
                            confidence=1.0,
                            metadata={
                                "structural_path": f"page/{page_num}/{span.span_type}",
                                "section_id": span.section_id,
                                "span_id": span.span_id,
                                "ocr_extracted": True,
                            },
                        )
                    )

            all_page_elems = tuple(visual_elems + ocr_elements)
            page_checksum = (
                sha256(ocr_text.encode("utf-8")).hexdigest()
                if ocr_text
                else sha256(f"missing:{page_num}".encode()).hexdigest()
            )
            page_meta["classification"] = "text_extracted" if ocr_text else "ocr_required"
            page_meta["ocr_applied"] = True
            updated_pages.append(
                NormalizedPage(
                    page_number=page_num,
                    checksum=page_checksum,
                    text=ocr_text,
                    elements=all_page_elems,
                    width=page_width,
                    height=page_height,
                    metadata=page_meta,
                )
            )
        else:
            if orig_page is not None:
                if not orig_page.elements and orig_page_elems:
                    orig_page = replace(orig_page, elements=orig_page_elems)
                updated_pages.append(orig_page)

    all_elements: list[NormalizedElement] = []
    for page in updated_pages:
        for element in page.elements:
            all_elements.append(replace(element, ordinal=len(all_elements)))

    page_chunks: list[str] = []
    for page in updated_pages:
        p_text = page.text.strip()
        if p_text.startswith(f"<!-- page: {page.page_number} -->"):
            page_chunks.append(p_text)
        elif re.match(r"<!--\s*page:\s*\d+\s*-->", p_text, re.IGNORECASE):
            page_chunks.append(p_text)
        else:
            page_chunks.append(f"<!-- page: {page.page_number} -->\n\n{page.text}")
    full_text = "\n\n".join(page_chunks).strip() + "\n"

    doc_metadata = dict(base_document.metadata)
    if metadata:
        doc_metadata.update(metadata)
    doc_metadata["vision_ocr_text_extraction_applied"] = True

    return NormalizedDocument(
        source_name=base_document.source_name,
        format=base_document.format,
        text=full_text,
        pages=tuple(updated_pages),
        elements=tuple(all_elements),
        visual_assets=base_document.visual_assets,
        metadata=doc_metadata,
        warnings=base_document.warnings,
    )
