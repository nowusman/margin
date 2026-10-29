"""Small helper functions used by the deterministic Markdown extractor.

These were extracted from ``workers/extract/markdown.py`` to keep the main
file under the repository size limit.
"""

from __future__ import annotations

import re
from typing import Any

from margin_schema.ontology import normalize_relationship_type
from workers.extract.markdown_types import ElementLike

MARKDOWN_HEADING_PATTERN = re.compile(r"^#{1,6}\s+(?P<heading>.+?)\s*$")
PROPOSITION_VERB_PATTERN = re.compile(
    r"\b(is|are|has|have|uses|requires|must|should|can|depends|contains|provides|publishes|"
    r"consumes|owns|supports|stores|exposes|produces|secures)\b",
    re.IGNORECASE,
)


def _category_for_element(element: ElementLike | None, default_category: str) -> str:
    path = ((element.structural_path if element else "") or "").casefold()
    if "adr" in path or "decision" in path:
        return "ADR"
    if "risk" in path:
        return "Risk"
    if "waiver" in path:
        return "Waiver"
    if "debt" in path:
        return "Technical Debt"
    return default_category


def _block_type(element: ElementLike | None, text: str) -> str:
    heading = (((element.heading or element.structural_path) if element else "") or "").casefold()
    lowered = text.casefold()
    first_heading = _first_markdown_heading(text)
    if first_heading:
        heading = f"{heading} {first_heading.casefold()}".strip()
    if "context" in heading:
        return "context"
    if "decision" in heading or lowered.startswith("use "):
        return "decision"
    if "consequence" in heading:
        return "consequence"
    if "status:" in lowered:
        return "status"
    if "risk" in heading:
        return "risk"
    return "statement"


def _first_markdown_heading(text: str) -> str | None:
    for line in text.splitlines():
        match = MARKDOWN_HEADING_PATTERN.match(line.strip())
        if match:
            return match.group("heading")
    return None


def _looks_like_complete_proposition(text: str) -> bool:
    """Allow a heading through only when it carries its own predicate."""

    cleaned = text.strip().lstrip("#").strip()
    return bool(PROPOSITION_VERB_PATTERN.search(cleaned) and len(cleaned.split()) >= 4)


def _statement_for_text(text: str, *, block_type: str) -> str:
    cleaned = text.strip().rstrip(".")
    if block_type == "status" and cleaned.casefold().startswith("status:"):
        return f"ADR status is {cleaned.split(':', 1)[1].strip()}."
    return f"{cleaned}."


def _tags_for_text(text: str) -> list[str]:
    tags = []
    lowered = text.casefold()
    for tag in ("adr", "risk", "api", "event", "postgresql", "llm"):
        if tag in lowered:
            tags.append(tag)
    return tags


def _looks_like_status_line(text: str) -> bool:
    return text.casefold().startswith("status:")


def _normalize_relation(value: str) -> str | None:
    return normalize_relationship_type(value)


def _table_int(value: str, *, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except ValueError:
        lookup = {"low": 2, "medium": 3, "high": 4, "critical": 5}
        parsed = lookup.get(str(value).strip().casefold(), default)
    return max(1, min(parsed, 5))


def _clean_bullet(line: str) -> str:
    return re.sub(r"^\s*(?:[-*]|\d+[\.\)]|\([0-9a-zA-Z]+\))\s+", "", line.strip())


def _element_like(element: Any) -> ElementLike:
    text = getattr(element, "text", None)
    if text is None:
        text = getattr(element, "text_content", None)
    raw_metadata = getattr(element, "metadata_", None)
    if not isinstance(raw_metadata, dict):
        raw_metadata = getattr(element, "metadata", None)
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    structural_path = (
        getattr(element, "structural_path", None)
        or getattr(element, "section_path", "")
        or metadata.get("structural_path", "")
        or ""
    )
    heading = getattr(element, "heading", None)
    if not heading and structural_path:
        parts = [
            p.strip() for p in re.split(r"\s*(?:/|>)\s*", structural_path) if p.strip() and not p.strip().isdigit()
        ]
        heading = parts[-1] if parts else None
    return ElementLike(
        id=str(getattr(element, "id", "")),
        kind=str(getattr(element, "kind", "paragraph")),
        text_content=text,
        structural_path=structural_path,
        heading=heading,
        source_page_id=getattr(element, "source_page_id", None),
        page_number=getattr(element, "page_number", None),
        metadata=metadata,
        char_start=getattr(element, "char_start", None),
        char_end=getattr(element, "char_end", None),
        bounding_box=getattr(element, "bounding_box", None),
        asset_key=getattr(element, "asset_key", None),
        checksum=getattr(element, "checksum", None),
    )
