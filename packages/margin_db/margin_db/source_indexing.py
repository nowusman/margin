from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol

from margin_schema.text_normalization import normalize_entity_name


@dataclass(frozen=True)
class IndexedSpan:
    kind: str
    text: str
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    page_number: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class IndexedSection:
    source_tree_node_id: str | None
    parent_source_tree_node_id: str | None
    heading: str
    section_path: str
    start_offset: int
    end_offset: int
    order_index: int
    spans: tuple[IndexedSpan, ...]


class SourceIndexer(Protocol):
    def build(
        self, text: str, *, default_heading: str, metadata: dict[str, Any] | None = None
    ) -> tuple[tuple[IndexedSection, ...], dict]:
        """Build citable sections and spans from normalized source text."""


class SourceIndex:
    """Small deterministic local source-tree index used by lightweight tests."""

    heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

    def build(
        self, text: str, *, default_heading: str, metadata: dict[str, Any] | None = None
    ) -> tuple[tuple[IndexedSection, ...], dict]:
        del metadata
        line_starts = self._line_starts(text)
        lines = text.splitlines()
        headings: list[tuple[int, int, str]] = []
        for line_number, line in enumerate(lines, start=1):
            match = self.heading_pattern.match(line)
            if match:
                headings.append((line_number, line_starts[line_number - 1], match.group(2).strip()))

        if not headings:
            spans = self._spans_for_text(text, line_starts=line_starts, start_line=1, base_offset=0)
            unheaded_sections = (
                IndexedSection(
                    source_tree_node_id="local:0",
                    parent_source_tree_node_id=None,
                    heading=default_heading,
                    section_path=default_heading,
                    start_offset=0,
                    end_offset=len(text),
                    order_index=0,
                    spans=spans,
                ),
            )
            quality = self._quality_report(text=text, sections=1, spans=len(spans))
            quality["native_elements"] = _indexed_element_manifest(unheaded_sections)
            return unheaded_sections, quality

        sections: list[IndexedSection] = []
        for index, (line_number, offset, heading) in enumerate(headings):
            next_offset = headings[index + 1][1] if index + 1 < len(headings) else len(text)
            section_text = text[offset:next_offset]
            spans = self._spans_for_text(
                section_text, line_starts=line_starts, start_line=line_number, base_offset=offset, skip_first_line=True
            )
            sections.append(
                IndexedSection(
                    source_tree_node_id=f"local:{index}",
                    parent_source_tree_node_id=None,
                    heading=heading,
                    section_path=f"{default_heading} / {heading}",
                    start_offset=offset,
                    end_offset=next_offset,
                    order_index=index,
                    spans=spans,
                )
            )

        quality = self._quality_report(
            text=text, sections=len(sections), spans=sum(len(section.spans) for section in sections)
        )
        quality["native_elements"] = _indexed_element_manifest(tuple(sections))
        return tuple(sections), quality

    def _spans_for_text(
        self, text: str, *, line_starts: list[int], start_line: int, base_offset: int, skip_first_line: bool = False
    ) -> tuple[IndexedSpan, ...]:
        spans: list[IndexedSpan] = []
        cursor = 0
        paragraphs = re.finditer(r"(?ms)(?:^|\n)([^\n](?:.*?))(?:\n\s*\n|$)", text)
        for match in paragraphs:
            content = match.group(1).strip()
            if skip_first_line and match.start(1) == 0:
                content_lines = content.splitlines()
                content = "\n".join(content_lines[1:]).strip()
                if not content:
                    continue
                cursor = text.find(content, match.start(1))

            local_start = text.find(content, cursor)
            if local_start < 0:
                local_start = match.start(1)
            local_end = local_start + len(content)
            cursor = local_end
            char_start = base_offset + local_start
            char_end = base_offset + local_end
            line_start = self._line_number_for_offset(line_starts, char_start)
            line_end = self._line_number_for_offset(line_starts, max(char_start, char_end - 1))
            spans.append(
                IndexedSpan(
                    kind=self._span_kind(content),
                    text=content,
                    line_start=max(start_line, line_start),
                    line_end=max(start_line, line_end),
                    char_start=char_start,
                    char_end=char_end,
                )
            )
        return tuple(spans)

    def _quality_report(self, *, text: str, sections: int, spans: int) -> dict[str, Any]:
        stripped = text.strip()
        has_table = any(line.strip().startswith("|") and line.strip().endswith("|") for line in text.splitlines())
        has_diagram_hint = any(
            token in text.lower() for token in ("@startuml", "sequenceDiagram".lower(), "graph td", "workspace")
        )
        return {
            "source_index_adapter": "local-source-tree-lightweight",
            "source_index_behavior": "deterministic local source tree",
            "text_coverage": 1.0 if stripped else 0.0,
            "table_coverage": 1.0 if has_table else 0.0,
            "image_or_diagram_detected": has_diagram_hint,
            "ocr_needed": not bool(stripped),
            "section_tree_confidence": 0.9 if sections > 1 else 0.65,
            "section_count": sections,
            "span_count": spans,
            "warnings": [] if stripped else ["no extractable text"],
        }

    @staticmethod
    def _line_starts(text: str) -> list[int]:
        starts = [0]
        for index, char in enumerate(text):
            if char == "\n":
                starts.append(index + 1)
        return starts

    @staticmethod
    def _line_number_for_offset(line_starts: list[int], offset: int) -> int:
        line_number = 1
        for index, start in enumerate(line_starts, start=1):
            if start > offset:
                break
            line_number = index
        return line_number

    @staticmethod
    def _span_kind(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```") or stripped.startswith("@startuml"):
            return "code_block"
        if "\n|" in stripped or stripped.startswith("|"):
            return "table"
        return "paragraph"


class LocalSourceTreeSourceIndex:
    """Default source index backed by the local source-tree adapter."""

    def build(
        self, text: str, *, default_heading: str, metadata: dict[str, Any] | None = None
    ) -> tuple[tuple[IndexedSection, ...], dict]:
        from margin_ingest.indexing import LocalSourceTreeAdapter
        from margin_ingest.quality import native_element_manifest, page_manifest, score_conversion_quality

        source_index = LocalSourceTreeAdapter().build(text, document_id=stable_document_id(default_heading))
        spans_by_section: dict[str, list] = defaultdict(list)
        for span in source_index.spans:
            spans_by_section[span.section_id].append(span)

        sections: list[IndexedSection] = []
        for section in source_index.sections:
            spans = tuple(
                IndexedSpan(
                    kind=worker_span_kind(span.span_type),
                    text=span.text,
                    line_start=span.start_line,
                    line_end=span.end_line,
                    char_start=span.start_offset,
                    char_end=span.end_offset,
                    page_number=span.page,
                    metadata={**span.metadata, "source_tree_span_id": span.span_id},
                )
                for span in spans_by_section.get(section.section_id, [])
            )
            sections.append(
                IndexedSection(
                    source_tree_node_id=section.section_id,
                    parent_source_tree_node_id=section.parent_id,
                    heading=section.title,
                    section_path=" / ".join(section.path),
                    start_offset=section.start_offset,
                    end_offset=section.end_offset,
                    order_index=section.ordinal - 1,
                    spans=spans,
                )
            )

        quality = score_conversion_quality(text, source_index, metadata=metadata)
        return tuple(sections), {
            "source_index_adapter": LocalSourceTreeAdapter.name,
            "source_index_behavior": "deterministic local source tree",
            "score": quality.score,
            "text_coverage": quality.text_coverage,
            "table_coverage": quality.table_coverage,
            "image_or_diagram_detected": quality.has_images_or_diagrams,
            "ocr_needed": quality.ocr_needed,
            "section_tree_confidence": quality.section_tree_confidence,
            "warnings": list(quality.warnings),
            "native_elements": native_element_manifest(source_index, metadata or {}),
            "pages": page_manifest(source_index, metadata or {}),
            **quality.details,
        }


def stable_document_id(value: str) -> str:
    return normalize_entity_name(value).replace(" ", "-") or "document"


def worker_span_kind(span_type: str) -> str:
    if span_type == "code":
        return "code_block"
    return span_type


def _indexed_element_manifest(sections: tuple[IndexedSection, ...]) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for section in sections:
        for ordinal, span in enumerate(section.spans):
            elements.append(
                {
                    "native_id": f"{section.source_tree_node_id or section.order_index}:{ordinal}",
                    "kind": span.kind,
                    "text": span.text,
                    "page_number": span.page_number,
                    "char_start": span.char_start,
                    "char_end": span.char_end,
                    "bounding_box": span.metadata,
                    "structural_path": f"{section.section_path}/{ordinal}",
                    "occurrence_ordinal": ordinal,
                    "source_element_position": (section.order_index, ordinal),
                }
            )
    return elements
