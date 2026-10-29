from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from math import ceil

from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement

logger = logging.getLogger("workers.extract.windows")


@dataclass(frozen=True)
class ContextElement:
    source_element_id: str
    element_type: str
    text: str
    page_number: int | None
    source_page_id: str | None = None


@dataclass(frozen=True)
class EligibleElement:
    """A source element that may be cited as evidence for this window's record.

    The same window may expose several eligible elements (heading + body, table
    caption + rows, figure caption + OCR text). The validator accepts citations
    to any of these elements as long as the exact quote resolves against the
    element's stored text.
    """

    source_element_id: str
    source_page_id: str | None
    element_type: str
    page_number: int | None
    text: str
    role: str  # "primary" | "eligible" — primary elements must produce outcomes
    text_role: str  # "heading" | "body" | "table_caption" | "table_row" | "figure_caption" | "ocr"


@dataclass(frozen=True)
class ExtractionWindow:
    window_id: str
    window_index: int
    source_document_id: str
    source_version_id: str
    source_element_id: str
    text: str
    content_type: str
    strategy: str
    source_page_id: str | None
    page_number: int | None
    section_heading: str | None
    section_path: str
    preceding_context: tuple[ContextElement, ...]
    following_context: tuple[ContextElement, ...]
    context_element_ids: tuple[str, ...]
    document_metadata: dict[str, object]
    estimated_tokens: int
    primary_source_element_ids: tuple[str, ...] = ()
    eligible_citable_elements: tuple[EligibleElement, ...] = ()
    record_element_ids: tuple[str, ...] = ()
    governed_candidates: tuple[dict[str, object], ...] = ()
    ontology_version: str | None = None
    domain_charter_version: int | None = None
    domain_charter_context: str = ""
    playbook_context: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class _Record:
    """Logical record of one or more source elements."""

    elements: tuple[SourceElement, ...]
    strategy: str
    header: str  # minimum required heading/header context to repeat per chunk
    section_heading: str | None = None
    section_path: str = ""
    record_text: str | None = None

    @property
    def element_ids(self) -> tuple[str, ...]:
        return tuple(elem.id for elem in self.elements)


def build_extraction_windows(
    *,
    document: SourceDocument,
    version: SourceDocumentVersion,
    elements: Iterable[SourceElement],
    max_tokens: int = 1_500,
    neighbor_tokens: int = 160,
) -> tuple[ExtractionWindow, ...]:
    """Build extraction windows around logical records.

    Each window represents a logical record (heading + body, table caption +
    rows, figure caption + OCR, adjacent paragraphs, or a single element with
    no record siblings). The window's text concatenates the record's elements;
    the primary element IDs list every element that must produce an outcome;
    the eligible citable elements expose every element in the record so the
    citation validator can accept evidence for any of them.
    """
    if max_tokens < 32:
        raise ValueError("max_tokens must be at least 32")
    if neighbor_tokens < 0:
        raise ValueError("neighbor_tokens cannot be negative")

    ordered_elements = sorted(
        (elem for elem in elements if (elem.text or "").strip()),
        key=lambda elem: (
            elem.ordinal if elem.ordinal is not None else 2**31,
            elem.page_number if elem.page_number is not None else 2**31,
            elem.id,
        ),
    )
    document_metadata = _document_metadata(document)
    windows: list[ExtractionWindow] = []
    records = _records_for_document(document, ordered_elements)

    for record in records:
        fixed_tokens = _estimate_tokens(
            " ".join(
                value
                for value in (document.title, record.section_heading or "", record.section_path, record.header)
                if value
            )
        )
        context_budget = min(neighbor_tokens, max(0, (max_tokens - fixed_tokens) // 3))
        primary_budget = max(8, max_tokens - fixed_tokens - (context_budget * 2))
        primary_elem = record.elements[0]
        preceding_context: tuple[ContextElement, ...] = ()
        following_context: tuple[ContextElement, ...] = ()
        primary_index = next((index for index, elem in enumerate(ordered_elements) if elem.id == primary_elem.id), None)
        if primary_index is not None:
            preceding_context = _context(ordered_elements[:primary_index], context_budget, preceding=True)
            following_context = _context(ordered_elements[primary_index + len(record.elements) :], context_budget)
        chunks = _record_chunks(record, primary_budget)
        eligible_elements = tuple(
            _to_eligible_element(elem, role="primary", text_role=_text_role(elem)) for elem in record.elements
        )
        primary_element_ids = tuple(elem.id for elem in record.elements)
        for chunk_index, chunk in enumerate(chunks):
            estimated_tokens = fixed_tokens + _estimate_tokens(chunk)
            estimated_tokens += sum(_estimate_tokens(item.text) for item in preceding_context)
            estimated_tokens += sum(_estimate_tokens(item.text) for item in following_context)
            if estimated_tokens > max_tokens:
                overflow = estimated_tokens - max_tokens
                following_context = _shrink_context(following_context, overflow, preceding=False)
                estimated_tokens = fixed_tokens + _estimate_tokens(chunk)
                estimated_tokens += sum(_estimate_tokens(item.text) for item in preceding_context)
                estimated_tokens += sum(_estimate_tokens(item.text) for item in following_context)
            if estimated_tokens > max_tokens:
                preceding_context = _shrink_context(preceding_context, estimated_tokens - max_tokens, preceding=True)
                estimated_tokens = fixed_tokens + _estimate_tokens(chunk)
                estimated_tokens += sum(_estimate_tokens(item.text) for item in preceding_context)
                estimated_tokens += sum(_estimate_tokens(item.text) for item in following_context)

            context_element_ids = tuple(item.source_element_id for item in (*preceding_context, *following_context))
            window_id = _window_id(version.id, record, chunk_index, chunk)
            windows.append(
                ExtractionWindow(
                    window_id=window_id,
                    window_index=len(windows),
                    source_document_id=document.id,
                    source_version_id=version.id,
                    source_element_id=primary_elem.id,
                    text=chunk,
                    content_type=record.strategy,
                    strategy=record.strategy,
                    source_page_id=primary_elem.source_page_id,
                    page_number=primary_elem.page_number,
                    section_heading=record.section_heading,
                    section_path=record.section_path,
                    preceding_context=preceding_context,
                    following_context=following_context,
                    context_element_ids=context_element_ids,
                    document_metadata=document_metadata,
                    estimated_tokens=min(estimated_tokens, max_tokens),
                    primary_source_element_ids=primary_element_ids,
                    eligible_citable_elements=eligible_elements,
                    record_element_ids=primary_element_ids,
                )
            )
    return tuple(windows)


def _records_for_document(document: SourceDocument, ordered_elements: list[SourceElement]) -> list[_Record]:
    """Group ordered elements into logical records.

    Records are formed by joining a structural element (heading, table caption,
    figure caption) with its body siblings, or by collecting a contiguous
    table/figure/OCR run or children sharing a parent_element_id.
    Elements that have no structural sibling form a single-element record.
    """
    records: list[_Record] = []
    used: set[str] = set()
    current_heading: str | None = None
    current_path: str = ""

    children_by_parent: dict[str, list[SourceElement]] = {}
    for elem in ordered_elements:
        if elem.parent_element_id:
            children_by_parent.setdefault(elem.parent_element_id, []).append(elem)

    for index, elem in enumerate(ordered_elements):
        if elem.id in used:
            continue
        kind = elem.kind.casefold()
        table_id = _table_id(elem)
        if kind == "table" and table_id and elem.metadata_.get("table_role") == "parent":
            table_elements = [
                candidate
                for candidate in ordered_elements
                if _table_id(candidate) == table_id
                and candidate.kind.casefold() in {"table", "table_caption", "table_header", "table_row"}
            ]
            used.add(elem.id)
            summary_elements = [elem]
            summary_elements.extend(
                candidate for candidate in table_elements if candidate.kind.casefold() == "table_caption"
            )
            for candidate in summary_elements[1:]:
                used.add(candidate.id)
            records.append(
                _Record(
                    elements=tuple(summary_elements),
                    strategy="table_summary",
                    header=_table_header_from_elements(table_elements),
                    section_heading=current_heading,
                    section_path=current_path,
                    record_text=(elem.text or "").strip(),
                )
            )
            continue
        if kind in {"table_header", "table_row", "table_cell"}:
            # Always stay on the structured-table path. Never fall through to
            # generic text_overlap extraction for these kinds.
            if not table_id:
                logger.warning(
                    "structured table element %s (%s) missing table_id; "
                    "keeping structured path without column-header grouping",
                    elem.id,
                    kind,
                )
            if kind != "table_row":
                used.add(elem.id)
                continue
            if table_id:
                table_elements = [
                    candidate
                    for candidate in ordered_elements
                    if _table_id(candidate) == table_id
                    and candidate.kind.casefold() in {"table_header", "table_row", "table_cell"}
                    and (
                        candidate.kind.casefold() == "table_header"
                        or candidate.metadata_.get("row_index") == elem.metadata_.get("row_index")
                    )
                ]
            else:
                table_elements = [elem]
            for candidate in table_elements:
                used.add(candidate.id)
            header = _table_header_from_elements(table_elements)
            row_text = (elem.text or "").strip()
            record_elements = [elem]
            record_elements.extend(candidate for candidate in table_elements if candidate.id != elem.id)
            records.append(
                _Record(
                    elements=tuple(record_elements),
                    strategy="table_rows",
                    header=header,
                    section_heading=current_heading,
                    section_path=current_path,
                    record_text="\n".join(value for value in (header, row_text) if value),
                )
            )
            continue
        if elem.kind.casefold() == "heading":
            current_heading = (elem.text or "").strip()
            current_path = current_heading

        siblings = _gather_record_siblings(elem, ordered_elements, index, children_by_parent)
        strategy = _window_strategy(document, elem)
        header = _record_header(elem, siblings)
        sibling_elements = tuple(siblings)
        for sibling in siblings:
            used.add(sibling.id)
        records.append(
            _Record(
                elements=sibling_elements,
                strategy=strategy,
                header=header,
                section_heading=current_heading,
                section_path=current_path,
            )
        )
    return records


def _gather_record_siblings(
    primary: SourceElement,
    elements: list[SourceElement],
    index: int,
    children_by_parent: dict[str, list[SourceElement]],
) -> list[SourceElement]:
    """Collect the elements that form one logical record starting at ``primary``."""
    primary_kind = primary.kind.casefold()
    if primary_kind == "heading":
        body: list[SourceElement] = []
        for sibling in elements[index + 1 :]:
            if sibling.kind.casefold() in {
                "heading",
                "table_caption",
                "figure_caption",
                "caption",
                "table",
                "table_header",
                "table_row",
                "table_cell",
            }:
                break
            body.append(sibling)
        return [primary, *body]
    if primary_kind in {"table_caption", "caption"}:
        if any(_table_id(sibling) for sibling in elements[index + 1 :]):
            return [primary]
        body = []
        for sibling in elements[index + 1 :]:
            if sibling.kind.casefold() not in {"table", "table_row", "table_header"}:
                break
            body.append(sibling)
        return [primary, *body]
    if primary_kind in {"figure_caption"}:
        body = []
        for sibling in elements[index + 1 :]:
            if sibling.kind.casefold() not in {"figure", "image", "image_ocr", "ocr", "diagram"}:
                break
            body.append(sibling)
        return [primary, *body]
    if primary_kind in {"table", "table_row"}:
        body = []
        for sibling in elements[index + 1 :]:
            if sibling.kind.casefold() not in {"table", "table_row", "table_header"}:
                break
            body.append(sibling)
        return [primary, *body]
    if primary.id in children_by_parent:
        return [primary, *children_by_parent[primary.id]]
    return [primary]


def _record_header(primary: SourceElement, siblings: list[SourceElement]) -> str:
    """Return the minimum heading/header context to repeat in every chunk."""
    primary_kind = primary.kind.casefold()
    if primary_kind in {"table", "table_row"}:
        first_elem = siblings[0] if siblings else primary
        lines = [line for line in (first_elem.text or "").splitlines() if line.strip()]
        if len(lines) >= 2:
            return "\n".join(lines[:2])
        if lines:
            return lines[0]
        return ""
    if primary_kind in {"table_caption", "caption", "figure_caption", "heading"}:
        return (primary.text or "").strip()
    return ""


def _window_strategy(document: SourceDocument, elem: SourceElement) -> str:
    kind = elem.kind.casefold()
    media_type = document.media_type.casefold()
    text = (elem.text or "").lstrip().casefold()
    if kind in {"table", "table_row", "table_header", "table_cell"}:
        return "table_rows"
    if (
        kind in {"diagram", "mermaid", "plantuml", "structurizr"}
        or (kind in {"code", "code_block", "figure"} and _looks_like_diagram(text))
        or media_type in {"text/vnd.structurizr", "text/vnd.mermaid", "text/vnd.plantuml"}
    ):
        return "diagram_records"
    if kind in {"contract", "openapi", "asyncapi", "schema"} or media_type in {
        "application/json",
        "application/yaml",
        "application/x-yaml",
        "application/vnd.asyncapi",
        "application/vnd.oai.openapi",
        "text/yaml",
        "text/x-yaml",
    }:
        return "contract_records"
    if kind in {"image", "picture", "ocr", "image_ocr", "figure"}:
        return "image_ocr"
    return "text_overlap"


def _looks_like_diagram(text: str) -> bool:
    return text.startswith(("```mermaid", "```plantuml", "```structurizr", "```dsl", "@startuml"))


def _to_eligible_element(elem: SourceElement, *, role: str, text_role: str) -> EligibleElement:
    return EligibleElement(
        source_element_id=elem.id,
        source_page_id=elem.source_page_id,
        element_type=elem.kind,
        page_number=elem.page_number,
        text=(elem.text or "").strip(),
        role=role,
        text_role=text_role,
    )


def _text_role(elem: SourceElement) -> str:
    kind = elem.kind.casefold()
    if kind == "heading":
        return "heading"
    if kind in {"table_caption", "caption"}:
        return "table_caption"
    if kind in {"table_header", "table_row", "table_cell", "table"}:
        return "table_row"
    if kind in {"figure_caption"}:
        return "figure_caption"
    if kind in {"figure", "image", "image_ocr", "ocr", "diagram"}:
        return "ocr"
    return "body"


def _document_metadata(document: SourceDocument) -> dict[str, object]:
    source_metadata = document.metadata_ or {}
    expected_types = source_metadata.get("expected_ontology_types")
    return {
        "title": document.title,
        "media_type": document.media_type,
        "source_uri": document.source_uri,
        "domain_id": document.domain_id,
        "category_id": document.category_id,
        "sensitivity": document.sensitivity,
        "dublin_core": document.dublin_core or {},
        "expected_ontology_types": (
            tuple(value for value in expected_types if isinstance(value, str))
            if isinstance(expected_types, list | tuple)
            else ()
        ),
    }


def _record_chunks(record: _Record, token_budget: int) -> tuple[str, ...]:
    """Chunk a record's text while repeating the minimum required header."""
    if token_budget <= 0:
        return ()
    header = record.header
    header_tokens = _estimate_tokens(header)
    if header_tokens >= token_budget and header:
        return (header,)

    body_elements: list[SourceElement]
    body_text_override: str | None = None
    if header and record.elements:
        header_elem = next((elem for elem in record.elements if (elem.text or "").strip() == header), None)
        if header_elem is not None:
            body_elements = [elem for elem in record.elements if elem.id != header_elem.id]
        else:
            body_elements = list(record.elements)
            first_elem = body_elements[0]
            first_text = (first_elem.text or "").strip()
            if header and first_text.startswith(header):
                body_text_override = first_text[len(header) :].lstrip("\n").strip()
                body_elements = body_elements[1:]
    else:
        body_elements = list(record.elements)

    body_text = (
        body_text_override
        if body_text_override is not None
        else "\n".join((elem.text or "").strip() for elem in body_elements).strip()
    )
    if not body_text:
        return (header,) if header else ()

    if not header and _estimate_tokens(body_text) <= token_budget:
        return (body_text,)

    body_budget = max(1, token_budget - header_tokens)
    if record.strategy == "table_rows" and record.record_text is not None:
        return _record_boundary_chunks(record.record_text, "", 0, token_budget, boundary="\n")
    if record.strategy == "table_summary" and record.record_text is not None:
        return _table_record_chunks(record.record_text, header, header_tokens, token_budget)
    if record.strategy == "table_rows":
        return _table_record_chunks(body_text, header, header_tokens, token_budget)
    if record.strategy in {"diagram_records", "contract_records", "image_ocr"}:
        return _record_boundary_chunks(body_text, header, header_tokens, token_budget, boundary="\n\n")
    body_chunks = _text_chunks(body_text, body_budget)
    if not header:
        return body_chunks
    return tuple(f"{header}\n{chunk}" for chunk in body_chunks)


def _table_record_chunks(text: str, header: str, header_tokens: int, token_budget: int) -> tuple[str, ...]:
    lines = [line for line in text.splitlines() if line.strip()]
    header_lines = [line for line in header.splitlines() if line.strip()]
    header_line_count = len(header_lines) if lines[: len(header_lines)] == header_lines else 2
    if len(lines) <= header_line_count:
        return _record_boundary_chunks(text, header, header_tokens, token_budget, boundary="\n")
    chunks: list[str] = []
    rows: list[str] = []
    for row in lines[header_line_count:]:
        if header and _estimate_tokens("\n".join((header, row))) > token_budget:
            if rows:
                chunks.append("\n".join((header, *rows)))
                rows = []
            row_budget = max(1, token_budget - header_tokens)
            for fragment in _text_chunks(row, row_budget):
                chunks.append(f"{header}\n{fragment}")
            continue
        candidate = "\n".join((header, *rows, row))
        if rows and _estimate_tokens(candidate) > token_budget:
            chunks.append("\n".join((header, *rows)))
            rows = [row]
        else:
            rows.append(row)
    if rows:
        chunks.append("\n".join((header, *rows)))
    return tuple(chunks)


def _record_boundary_chunks(
    text: str, header: str, header_tokens: int, token_budget: int, *, boundary: str
) -> tuple[str, ...]:
    records = [record.strip() for record in text.split(boundary) if record.strip()]
    if len(records) <= 1:
        body_budget = max(1, token_budget - header_tokens) if header else token_budget
        body_chunks = _text_chunks(text, body_budget)
        if not header:
            return body_chunks
        return tuple(f"{header}{boundary}{chunk}" for chunk in body_chunks)
    chunks: list[str] = []
    current: list[str] = []
    for item in records:
        candidate = f"{header}{boundary}{item}" if header else item
        if _estimate_tokens(candidate) > token_budget:
            if current:
                chunks.append(f"{header}{boundary}{boundary.join(current)}" if header else boundary.join(current))
                current = []
            body_budget = max(1, token_budget - header_tokens) if header else token_budget
            for fragment in _text_chunks(item, body_budget):
                chunks.append(f"{header}{boundary}{fragment}" if header else fragment)
            continue
        combined = f"{header}{boundary}{boundary.join([*current, item])}" if header else boundary.join([*current, item])
        if current and _estimate_tokens(combined) > token_budget:
            chunks.append(f"{header}{boundary}{boundary.join(current)}" if header else boundary.join(current))
            current = [item]
        else:
            current.append(item)
    if current:
        chunks.append(f"{header}{boundary}{boundary.join(current)}" if header else boundary.join(current))
    return tuple(chunks)


def _text_chunks(text: str, token_budget: int) -> tuple[str, ...]:
    max_chars = max(1, token_budget * 4)
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining.strip())
            break
        split_at = max(
            remaining.rfind("\n", 0, max_chars + 1),
            remaining.rfind(". ", 0, max_chars + 1),
            remaining.rfind(" ", 0, max_chars + 1),
        )
        if split_at <= 0:
            split_at = max_chars
        elif remaining[split_at : split_at + 2] == ". ":
            split_at += 1
        chunk = remaining[:split_at].strip()
        chunks.append(chunk)
        overlap_chars = min(max(0, token_budget // 8) * 4, len(chunk) // 4)
        overlap = chunk[-overlap_chars:].strip() if overlap_chars else ""
        remaining = " ".join(value for value in (overlap, remaining[split_at:].strip()) if value)
    return tuple(chunk for chunk in chunks if chunk)


def _context(
    elements: Iterable[SourceElement], token_budget: int, *, preceding: bool = False
) -> tuple[ContextElement, ...]:
    if token_budget <= 0:
        return ()
    ordered = list(elements)
    candidates = reversed(ordered) if preceding else iter(ordered)
    remaining = token_budget
    context: list[ContextElement] = []
    for elem in candidates:
        text = _truncate_to_tokens((elem.text or "").strip(), remaining)
        if not text:
            continue
        context.append(
            ContextElement(
                source_element_id=elem.id,
                element_type=elem.kind,
                text=text,
                page_number=elem.page_number,
                source_page_id=elem.source_page_id,
            )
        )
        remaining -= _estimate_tokens(text)
        if remaining <= 0:
            break
    if preceding:
        context.reverse()
    return tuple(context)


def _shrink_context(
    context: tuple[ContextElement, ...], tokens_to_remove: int, *, preceding: bool
) -> tuple[ContextElement, ...]:
    items = list(context)
    index = 0 if preceding else -1
    remaining = max(tokens_to_remove, 0)
    while items and remaining > 0:
        item = items[index]
        item_tokens = _estimate_tokens(item.text)
        if remaining >= item_tokens:
            remaining -= item_tokens
            items.pop(index)
            continue
        items[index] = ContextElement(
            source_element_id=item.source_element_id,
            element_type=item.element_type,
            text=_truncate_to_tokens(item.text, item_tokens - remaining),
            page_number=item.page_number,
            source_page_id=item.source_page_id,
        )
        remaining = 0
    return tuple(items)


def _truncate_to_tokens(text: str, token_budget: int) -> str:
    if token_budget <= 0:
        return ""
    return text[: token_budget * 4].strip()


def _estimate_tokens(text: str) -> int:
    return ceil(len(text) / 4) if text else 0


def _window_id(version_id: str, record: _Record, chunk_index: int, text: str) -> str:
    payload = "\0".join((version_id, *record.element_ids, record.strategy, str(chunk_index), record.header, text))
    digest = sha256(payload.encode("utf-8")).hexdigest()
    return f"window_{digest}"


def _table_id(element: SourceElement) -> str | None:
    value = (element.metadata_ or {}).get("table_id")
    return str(value) if value else None


def _table_header_from_elements(elements: list[SourceElement]) -> str:
    parent = next((element for element in elements if element.kind.casefold() == "table"), None)
    if parent is not None:
        metadata = parent.metadata_ or {}
        headers = metadata.get("column_headers")
        if isinstance(headers, list) and headers and all(isinstance(item, str) for item in headers):
            table_header = "| " + " | ".join(headers) + " |"
            separator = "| " + " | ".join("---" for _ in headers) + " |"
            caption = metadata.get("caption")
            prefix = [str(caption).strip()] if isinstance(caption, str) and caption.strip() else []
            return "\n".join((*prefix, table_header, separator))
        lines = [line.strip() for line in (parent.text or "").splitlines() if line.strip()]
        if len(lines) >= 2:
            return "\n".join(lines[:2])
    header = next((element for element in elements if element.kind.casefold() == "table_header"), None)
    return (header.text or "").strip() if header is not None else ""
