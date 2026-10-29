from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path

from margin_ingest.converters import (
    ConversionError,
    Converter,
    DoclingPrimaryConverter,
    LightweightSourceConverter,
    MarkItDownSecondaryConverter,
)
from margin_ingest.indexing import build_source_index
from margin_ingest.quality import score_conversion_quality
from margin_schema.text_normalization import normalize_canonical_text


@dataclass(frozen=True)
class ParserFixture:
    path: Path
    fixture_type: str
    expected_tokens: tuple[str, ...]


@dataclass(frozen=True)
class ParserBenchmarkRow:
    parser: str
    fixture: str
    fixture_type: str
    status: str
    latency_ms: int
    text_length: int
    section_count: int
    span_count: int
    table_span_count: int
    table_coverage: float
    has_images_or_diagrams: bool
    ocr_needed: bool
    section_tree_confidence: float
    quality_score: float
    expected_token_coverage: float
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def default_parser_fixtures(root: Path = Path("tests/fixtures/source_ingestion")) -> tuple[ParserFixture, ...]:
    return (
        ParserFixture(root / "minimal.pdf", "pdf", ("Minimal",)),
        ParserFixture(root / "minimal.docx", "docx", ("Minimal",)),
        ParserFixture(root / "minimal.pptx", "pptx", ("Minimal",)),
        ParserFixture(root / "architecture.md", "tables", ("Customer Platform", "|")),
        ParserFixture(root / "flow.mmd", "diagram", ("flowchart",)),
    )


def benchmark_default_parsers(fixtures: tuple[ParserFixture, ...] | None = None) -> tuple[ParserBenchmarkRow, ...]:
    parser_candidates: tuple[tuple[str, Converter], ...] = (
        ("docling-primary", DoclingPrimaryConverter()),
        ("markitdown-secondary", MarkItDownSecondaryConverter()),
        ("lightweight-source", LightweightSourceConverter()),
    )
    rows: list[ParserBenchmarkRow] = []
    selected_fixtures = fixtures or default_parser_fixtures()
    for parser_name, converter in parser_candidates:
        for fixture in selected_fixtures:
            started = time.perf_counter()
            try:
                result = converter.convert(fixture.path)
            except ConversionError as exc:
                rows.append(_skipped_row(parser_name, fixture, str(exc)))
                continue
            latency_ms = int((time.perf_counter() - started) * 1000)
            source_index = build_source_index(result.markdown, document_id=fixture.path.stem)
            quality = score_conversion_quality(
                result.markdown, source_index, source_bytes=fixture.path.read_bytes(), metadata=result.metadata
            )
            rows.append(
                ParserBenchmarkRow(
                    parser=parser_name,
                    fixture=str(fixture.path),
                    fixture_type=fixture.fixture_type,
                    status="ok",
                    latency_ms=latency_ms,
                    text_length=len(result.markdown),
                    section_count=quality.details["section_count"],
                    span_count=quality.details["span_count"],
                    table_span_count=quality.details["table_span_count"],
                    table_coverage=quality.table_coverage,
                    has_images_or_diagrams=quality.has_images_or_diagrams,
                    ocr_needed=quality.ocr_needed,
                    section_tree_confidence=quality.section_tree_confidence,
                    quality_score=quality.score,
                    expected_token_coverage=_token_coverage(result.markdown, fixture.expected_tokens),
                    warnings=tuple(dict.fromkeys((*result.warnings, *quality.warnings))),
                )
            )
    return tuple(rows)


def parser_benchmark_failures(rows: tuple[ParserBenchmarkRow, ...]) -> tuple[str, ...]:
    failures: list[str] = []
    required_fixture_types = {"tables", "diagram"}
    for fixture_type in sorted(required_fixture_types):
        ok_rows = [
            row
            for row in rows
            if row.fixture_type == fixture_type and row.status == "ok" and row.expected_token_coverage > 0
        ]
        if not ok_rows:
            failures.append(f"no successful parser benchmark row for {fixture_type}")
    for row in rows:
        if row.status == "ok" and row.text_length == 0:
            failures.append(f"{row.parser} produced empty output for {row.fixture}")
    return tuple(failures)


def _skipped_row(parser_name: str, fixture: ParserFixture, reason: str) -> ParserBenchmarkRow:
    return ParserBenchmarkRow(
        parser=parser_name,
        fixture=str(fixture.path),
        fixture_type=fixture.fixture_type,
        status=f"skipped:{reason}",
        latency_ms=0,
        text_length=0,
        section_count=0,
        span_count=0,
        table_span_count=0,
        table_coverage=0.0,
        has_images_or_diagrams=False,
        ocr_needed=False,
        section_tree_confidence=0.0,
        quality_score=0.0,
        expected_token_coverage=0.0,
        warnings=(),
    )


def _token_coverage(markdown: str, expected_tokens: tuple[str, ...]) -> float:
    if not expected_tokens:
        return 1.0
    text = normalize_canonical_text(markdown)
    hits = sum(1 for token in expected_tokens if normalize_canonical_text(token) in text)
    return hits / len(expected_tokens)
