"""Evaluate the search backend decision gate from load reports.

Consumes only load reports labeled with:
- environment
- corpus size (or dataset size)
- command
- timestamp
- measured samples
"""

from __future__ import annotations

import re
from pathlib import Path

REQUIRED_FIELD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("environment", re.compile(r"^environment:\s*(.+)$", re.IGNORECASE | re.MULTILINE)),
    ("corpus_size", re.compile(r"^(?:corpus|dataset)\s*size:\s*([0-9]+)", re.IGNORECASE | re.MULTILINE)),
    ("command", re.compile(r"^command:\s*(.+)$", re.IGNORECASE | re.MULTILINE)),
    ("timestamp", re.compile(r"^timestamp:\s*(.+)$", re.IGNORECASE | re.MULTILINE)),
    ("measured_samples", re.compile(r"^measured\s*samples:\s*([0-9]+)", re.IGNORECASE | re.MULTILINE)),
)

SYNTHETIC_PATTERN = re.compile(
    r"(synthetic-ci-probe|measurement\s*mode:\s*synthetic|not\s*established\s*by\s*synthetic)",
    re.IGNORECASE,
)

P95_PATTERN = re.compile(r"retrieval\s*p95(?:\s*ms)?:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def validate_report_content(content: str, filename: str = "report") -> dict[str, str]:
    """Validate that report contains all required labels and is not synthetic."""
    if SYNTHETIC_PATTERN.search(content):
        raise ValueError(f"synthetic report rejected in {filename}")

    fields: dict[str, str] = {}
    for name, pattern in REQUIRED_FIELD_PATTERNS:
        match = pattern.search(content)
        if not match:
            raise ValueError(f"required label '{name}' missing in {filename}")
        fields[name] = match.group(1).strip()
    return fields


def read_retrieval_p95(path: Path) -> int:
    """Read retrieval p95 from a verified load report.

    Raises ValueError if report lacks required labels, is synthetic, or lacks p95.
    """
    content = path.read_text(encoding="utf-8")
    validate_report_content(content, path.name)
    match = P95_PATTERN.search(content)
    if not match:
        raise ValueError(f"retrieval p95 metric missing in {path}")
    return int(float(match.group(1)))


def decision_from_reports(report_paths: tuple[Path, ...], threshold_ms: int = 900) -> str:
    """Evaluate decision from a sequence of reports, filtering for valid labeled reports."""
    valid_p95_values: list[int] = []
    for path in report_paths:
        try:
            val = read_retrieval_p95(path)
            valid_p95_values.append(val)
        except (ValueError, OSError):
            continue

    if len(valid_p95_values) < 3:
        return "insufficient_data"
    latest_three = valid_p95_values[-3:]
    if all(value > threshold_ms for value in latest_three):
        return "benchmark_search_backend"
    return "stay_postgres_fts"


if __name__ == "__main__":
    reports_dir = Path("docs/operations/performance")
    reports = tuple(sorted(reports_dir.glob("load-report-*.md"), key=lambda p: p.stat().st_mtime_ns))
    print(decision_from_reports(reports))
