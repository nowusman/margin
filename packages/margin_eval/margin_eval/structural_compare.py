"""Reusable gold-corpus structural hash and page-count compare.

Compares fixture source bytes and lightweight conversion page counts against
manifest expectations. Rich PDF/DOCX/PPTX fixtures are reported as blocked when
Docling is unavailable; they are never inventively scored.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from margin_eval.gold_corpus import GoldCorpus, GoldFixture, load_gold_corpus
from margin_ingest.converters import (
    LIGHTWEIGHT_EXTENSIONS,
    RICH_EXTENSIONS,
    ConversionError,
    ConversionUnavailable,
    LightweightSourceConverter,
)


@dataclass(frozen=True)
class StructuralCompareRow:
    fixture_id: str
    path: str
    status: str
    source_sha256: str
    expected_pages: int | None
    converted_pages: int | None
    detail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StructuralCompareReport:
    corpus_version: str
    rows: tuple[StructuralCompareRow, ...]
    measured_lightweight: int
    blocked_rich: int
    failed: int
    page_mismatches: int

    def to_dict(self) -> dict[str, object]:
        return {
            "corpus_version": self.corpus_version,
            "measured_lightweight": self.measured_lightweight,
            "blocked_rich": self.blocked_rich,
            "failed": self.failed,
            "page_mismatches": self.page_mismatches,
            "rows": [row.to_dict() for row in self.rows],
        }


def compare_gold_corpus_structural(
    path: Path = Path("tests/fixtures/gold_corpus/manifest.json"), *, corpus: GoldCorpus | None = None
) -> StructuralCompareReport:
    """Hash fixtures and page-compare the locally convertible text/diagram subset."""
    selected = corpus or load_gold_corpus(path)
    converter = LightweightSourceConverter()
    rows: list[StructuralCompareRow] = []
    for fixture in selected.fixtures:
        rows.append(_compare_fixture(fixture, converter))
    measured = sum(1 for row in rows if row.status == "measured")
    blocked = sum(1 for row in rows if row.status == "blocked_rich")
    failed = sum(1 for row in rows if row.status == "failed")
    mismatches = sum(1 for row in rows if row.status == "page_mismatch")
    return StructuralCompareReport(
        corpus_version=selected.version,
        rows=tuple(rows),
        measured_lightweight=measured,
        blocked_rich=blocked,
        failed=failed,
        page_mismatches=mismatches,
    )


def _compare_fixture(fixture: GoldFixture, converter: LightweightSourceConverter) -> StructuralCompareRow:
    source = fixture.path
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    suffix = source.suffix.lower()
    if suffix in RICH_EXTENSIONS:
        return StructuralCompareRow(
            fixture_id=fixture.fixture_id,
            path=str(source),
            status="blocked_rich",
            source_sha256=digest,
            expected_pages=fixture.expected_pages,
            converted_pages=None,
            detail="requires pinned Docling runtime for rich-document conversion",
        )
    if suffix not in LIGHTWEIGHT_EXTENSIONS:
        return StructuralCompareRow(
            fixture_id=fixture.fixture_id,
            path=str(source),
            status="failed",
            source_sha256=digest,
            expected_pages=fixture.expected_pages,
            converted_pages=None,
            detail=f"unsupported lightweight suffix: {suffix or 'unknown'}",
        )
    try:
        result = converter.convert(source)
    except ConversionUnavailable as exc:
        return StructuralCompareRow(
            fixture_id=fixture.fixture_id,
            path=str(source),
            status="failed",
            source_sha256=digest,
            expected_pages=fixture.expected_pages,
            converted_pages=None,
            detail=str(exc),
        )
    except ConversionError as exc:
        return StructuralCompareRow(
            fixture_id=fixture.fixture_id,
            path=str(source),
            status="failed",
            source_sha256=digest,
            expected_pages=fixture.expected_pages,
            converted_pages=None,
            detail=str(exc),
        )
    converted_pages = len(result.normalized_document.pages)
    if fixture.expected_pages is not None and converted_pages != fixture.expected_pages:
        return StructuralCompareRow(
            fixture_id=fixture.fixture_id,
            path=str(source),
            status="page_mismatch",
            source_sha256=digest,
            expected_pages=fixture.expected_pages,
            converted_pages=converted_pages,
            detail=f"expected {fixture.expected_pages} pages, converted {converted_pages}",
        )
    return StructuralCompareRow(
        fixture_id=fixture.fixture_id,
        path=str(source),
        status="measured",
        source_sha256=digest,
        expected_pages=fixture.expected_pages,
        converted_pages=converted_pages,
        detail="source hash recorded; lightweight page count matched or undeclared",
    )
