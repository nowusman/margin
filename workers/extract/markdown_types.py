from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ElementLike:
    id: str
    kind: str
    text_content: str | None
    structural_path: str = ""
    heading: str | None = None
    source_page_id: str | None = None
    page_number: int | None = None
    metadata: dict[str, Any] | None = None
    char_start: int | None = None
    char_end: int | None = None
    bounding_box: dict[str, Any] | None = None
    asset_key: str | None = None
    checksum: str | None = None


@dataclass(frozen=True)
class FrontmatterField:
    raw_key: str
    value: str
