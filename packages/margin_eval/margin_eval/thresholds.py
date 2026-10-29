from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from margin_eval.metrics import EvaluationReport

if TYPE_CHECKING:
    from margin_eval.surface_quality import SurfaceQualityMetrics


class ReleaseStage(StrEnum):
    MVP = "mvp"
    PRODUCTION = "production"


THRESHOLDS: dict[ReleaseStage, dict[str, float]] = {
    ReleaseStage.MVP: {
        "citation_coverage": 0.95,
        "unsupported_claim_rate_max": 0.05,
        "recall_at_k": 0.75,
        "exact_id_lookup_success": 0.95,
    },
    ReleaseStage.PRODUCTION: {
        "citation_coverage": 0.99,
        "unsupported_claim_rate_max": 0.01,
        "recall_at_k": 0.90,
        "exact_id_lookup_success": 0.99,
    },
}

SURFACE_THRESHOLDS: dict[ReleaseStage, dict[str, float]] = {
    ReleaseStage.MVP: {
        "graph_traversal_accuracy": 0.95,
        "narrative_faithfulness": 0.95,
        "narrative_stray_statements_max": 0.0,
        "export_completeness": 0.95,
        "export_unexpected_records_max": 0.0,
        "personal_isolation": 1.0,
    },
    ReleaseStage.PRODUCTION: {
        "graph_traversal_accuracy": 1.0,
        "narrative_faithfulness": 1.0,
        "narrative_stray_statements_max": 0.0,
        "export_completeness": 1.0,
        "export_unexpected_records_max": 0.0,
        "personal_isolation": 1.0,
    },
}


def surface_threshold_failures(metrics: SurfaceQualityMetrics, stage: ReleaseStage) -> tuple[str, ...]:
    """Report the surface properties that fall below the agreed thresholds."""

    thresholds = SURFACE_THRESHOLDS[stage]
    failures: list[str] = []
    checks = (
        ("graph_traversal_accuracy", metrics.graph_traversal_accuracy, "min"),
        ("narrative_faithfulness", metrics.narrative_faithfulness, "min"),
        ("export_completeness", metrics.export_completeness, "min"),
        ("personal_isolation", metrics.personal_isolation, "min"),
    )
    for name, value, kind in checks:
        threshold = thresholds[name]
        if kind == "min" and value < threshold:
            failures.append(f"{name} {value:.3f} < {threshold:.3f}")
    maxima = (
        ("narrative_stray_statements_max", metrics.narrative_stray_statements),
        ("export_unexpected_records_max", metrics.export_records_unexpected),
    )
    for name, value in maxima:
        threshold = thresholds[name]
        if value > threshold:
            failures.append(f"{name} {value} > {threshold}")
    return tuple(failures)


def threshold_failures(report: EvaluationReport, stage: ReleaseStage) -> tuple[str, ...]:
    thresholds = THRESHOLDS[stage]
    failures: list[str] = []
    if report.citation_coverage < thresholds["citation_coverage"]:
        failures.append("citation_coverage " f"{report.citation_coverage:.3f} < {thresholds['citation_coverage']:.3f}")
    if report.unsupported_claim_rate > thresholds["unsupported_claim_rate_max"]:
        failures.append(
            "unsupported_claim_rate "
            f"{report.unsupported_claim_rate:.3f} > "
            f"{thresholds['unsupported_claim_rate_max']:.3f}"
        )
    if report.recall_at_k < thresholds["recall_at_k"]:
        failures.append(f"recall_at_k {report.recall_at_k:.3f} < {thresholds['recall_at_k']:.3f}")
    if report.exact_id_lookup_success < thresholds["exact_id_lookup_success"]:
        failures.append(
            "exact_id_lookup_success "
            f"{report.exact_id_lookup_success:.3f} < "
            f"{thresholds['exact_id_lookup_success']:.3f}"
        )
    return tuple(failures)
