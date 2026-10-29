from __future__ import annotations

from pathlib import Path

import pytest

from scripts.performance.evaluate_search_backend_gate import decision_from_reports, read_retrieval_p95


def _write_report(
    path: Path,
    p95: int,
    *,
    environment: str = "production",
    corpus_size: int = 10000,
    command: str = "py scripts/performance/run_operational_load_suite.py",
    timestamp: str = "2026-09-06T19:00:00Z",
    measured_samples: int = 20,
    measurement_mode: str = "live-measured",
) -> None:
    lines = [
        f"# Load Report {path.stem}",
        f"environment: {environment}",
        f"corpus size: {corpus_size} ideablocks",
        f"command: {command}",
        f"timestamp: {timestamp}",
        f"measured samples: {measured_samples}",
        f"measurement mode: {measurement_mode}",
        f"retrieval p95 ms: {p95}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_decision_benchmark_when_three_consecutive_above_threshold(tmp_path: Path) -> None:
    paths = []
    for idx, p95 in enumerate((950, 980, 1005), start=1):
        path = tmp_path / f"load-report-{idx}.md"
        _write_report(path, p95, corpus_size=idx * 10000)
        paths.append(path)
    assert decision_from_reports(tuple(paths)) == "benchmark_search_backend"


def test_decision_stays_postgres_when_threshold_not_crossed(tmp_path: Path) -> None:
    paths = []
    for idx, p95 in enumerate((850, 920, 880), start=1):
        path = tmp_path / f"load-report-{idx}.md"
        _write_report(path, p95, corpus_size=idx * 10000)
        paths.append(path)
    assert decision_from_reports(tuple(paths)) == "stay_postgres_fts"


def test_decision_rejects_synthetic_report(tmp_path: Path) -> None:
    path = tmp_path / "load-report-synthetic.md"
    _write_report(path, 950, measurement_mode="synthetic-ci-probe")
    with pytest.raises(ValueError, match="synthetic report rejected"):
        read_retrieval_p95(path)


def test_decision_rejects_missing_metadata_label(tmp_path: Path) -> None:
    path = tmp_path / "load-report-missing-env.md"
    path.write_text("retrieval p95 ms: 500\ncorpus size: 10000\n", encoding="utf-8")
    with pytest.raises(ValueError, match="required label 'environment' missing"):
        read_retrieval_p95(path)
