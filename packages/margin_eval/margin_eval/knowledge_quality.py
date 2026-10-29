"""Quality metrics, thresholds, evaluation run models, and serialization.

This module defines the data types, threshold specifications, and persistence
utilities for evidence-backed quality evaluation in Margin.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path

EVALUATION_THRESHOLDS: dict[str, float] = {
    "claim_recall": 0.95,
    "field_recall": 0.95,
    "relationship_precision": 0.98,
    "entity_accuracy": 0.98,
    "element_outcome_coverage": 1.0,
    "failed_outcome_max": 0,
    "deferred_outcome_max": 0,
    "unbacked_claim_max": 0,
    "citation_coverage": 0.95,
    "verified_evidence_coverage": 1.0,
    "inference_origin_candidate_max": 0,
    "relationship_consistency_error_max": 0,
    "ghost_record_max": 0,
    "page_reconciliation_coverage": 1.0,
}


class EvaluationMode(str, Enum):
    DETERMINISTIC = "deterministic"
    OPERATIONAL = "operational"


class MatchKind(str, Enum):
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class MatchRecord:
    """A single expected-to-actual match with traceable evidence IDs."""

    expected_id: str
    actual_ids: tuple[str, ...]
    kind: MatchKind
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "expected_id": self.expected_id,
            "actual_ids": list(self.actual_ids),
            "kind": self.kind.value,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class FixtureResult:
    fixture_id: str
    domain_id: str
    source_document_id: str | None
    claim_matches: tuple[MatchRecord, ...]
    field_matches: tuple[MatchRecord, ...]
    entity_matches: tuple[MatchRecord, ...]
    relationship_matches: tuple[MatchRecord, ...]
    no_claim_matches: tuple[MatchRecord, ...]
    failed_element_count: int
    deferred_element_count: int
    failed_attempt_count: int
    deferred_attempt_count: int
    terminal_outcome_count: int
    unaccounted_element_count: int
    unbacked_claim_count: int
    page_reconciliation_missing: tuple[int, ...]
    expected_pages_total: int
    expected_pages_reconciled: int
    verified_claims_with_evidence: int
    verified_relationships_with_evidence: int
    mapped_elements_with_outcome: int
    inference_origin_candidate_ids: tuple[str, ...]
    ghost_record_ids: tuple[str, ...]
    ambiguous_match_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "fixture_id": self.fixture_id,
            "domain_id": self.domain_id,
            "source_document_id": self.source_document_id,
            "claim_matches": [match.to_dict() for match in self.claim_matches],
            "field_matches": [match.to_dict() for match in self.field_matches],
            "entity_matches": [match.to_dict() for match in self.entity_matches],
            "relationship_matches": [match.to_dict() for match in self.relationship_matches],
            "no_claim_matches": [match.to_dict() for match in self.no_claim_matches],
            "failed_element_count": self.failed_element_count,
            "deferred_element_count": self.deferred_element_count,
            "failed_attempt_count": self.failed_attempt_count,
            "deferred_attempt_count": self.deferred_attempt_count,
            "terminal_outcome_count": self.terminal_outcome_count,
            "unaccounted_element_count": self.unaccounted_element_count,
            "unbacked_claim_count": self.unbacked_claim_count,
            "page_reconciliation_missing": list(self.page_reconciliation_missing),
            "expected_pages_total": self.expected_pages_total,
            "expected_pages_reconciled": self.expected_pages_reconciled,
            "verified_claims_with_evidence": self.verified_claims_with_evidence,
            "verified_relationships_with_evidence": (self.verified_relationships_with_evidence),
            "mapped_elements_with_outcome": self.mapped_elements_with_outcome,
            "inference_origin_candidate_ids": list(self.inference_origin_candidate_ids),
            "ghost_record_ids": list(self.ghost_record_ids),
            "ambiguous_match_count": self.ambiguous_match_count,
        }


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    claim_recall: float
    claim_precision: float
    field_recall: float
    relationship_precision: float
    entity_accuracy: float
    element_outcome_coverage: float
    citation_coverage: float
    verified_evidence_coverage: float
    page_reconciliation_coverage: float
    failed_element_count: int
    deferred_element_count: int
    failed_attempt_count: int
    deferred_attempt_count: int
    unbacked_claim_count: int
    inference_origin_candidate_count: int
    relationship_consistency_error_count: int
    ghost_record_count: int
    ambiguous_match_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def with_relationship_consistency_error_count(self, error_count: int) -> EvaluationMetrics:
        return EvaluationMetrics(
            claim_recall=self.claim_recall,
            claim_precision=self.claim_precision,
            field_recall=self.field_recall,
            relationship_precision=self.relationship_precision,
            entity_accuracy=self.entity_accuracy,
            element_outcome_coverage=self.element_outcome_coverage,
            citation_coverage=self.citation_coverage,
            verified_evidence_coverage=self.verified_evidence_coverage,
            page_reconciliation_coverage=self.page_reconciliation_coverage,
            failed_element_count=self.failed_element_count,
            deferred_element_count=self.deferred_element_count,
            failed_attempt_count=self.failed_attempt_count,
            deferred_attempt_count=self.deferred_attempt_count,
            unbacked_claim_count=self.unbacked_claim_count,
            inference_origin_candidate_count=self.inference_origin_candidate_count,
            relationship_consistency_error_count=error_count,
            ghost_record_count=self.ghost_record_count,
            ambiguous_match_count=self.ambiguous_match_count,
        )


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    run_id: str
    started_at: datetime
    completed_at: datetime
    corpus_version: str
    corpus_path: Path
    evaluation_mode: str
    model_id: str | None
    prompt_version: str | None
    index_version: str | None
    active_extraction_model_id: str | None
    metrics: EvaluationMetrics
    fixture_results: tuple[FixtureResult, ...]
    relationship_consistency_findings: tuple[dict[str, object], ...]
    gate_failures: tuple[str, ...]
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "corpus_version": self.corpus_version,
            "corpus_path": str(self.corpus_path),
            "evaluation_mode": self.evaluation_mode,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "index_version": self.index_version,
            "active_extraction_model_id": self.active_extraction_model_id,
            "metrics": self.metrics.to_dict(),
            "fixture_results": [result.to_dict() for result in self.fixture_results],
            "relationship_consistency_findings": list(self.relationship_consistency_findings),
            "gate_failures": list(self.gate_failures),
            "status": self.status,
        }


def gate_failures(metrics: EvaluationMetrics) -> tuple[str, ...]:
    failures: list[str] = []
    if metrics.claim_recall < EVALUATION_THRESHOLDS["claim_recall"]:
        failures.append(f"claim recall {metrics.claim_recall:.3f} < " f"{EVALUATION_THRESHOLDS['claim_recall']:.3f}")
    if metrics.field_recall < EVALUATION_THRESHOLDS["field_recall"]:
        failures.append(
            f"structured-field recall {metrics.field_recall:.3f} < " f"{EVALUATION_THRESHOLDS['field_recall']:.3f}"
        )
    if metrics.relationship_precision < EVALUATION_THRESHOLDS["relationship_precision"]:
        failures.append(
            f"verified relationship precision {metrics.relationship_precision:.3f} < "
            f"{EVALUATION_THRESHOLDS['relationship_precision']:.3f}"
        )
    if metrics.entity_accuracy < EVALUATION_THRESHOLDS["entity_accuracy"]:
        failures.append(
            f"entity-resolution accuracy {metrics.entity_accuracy:.3f} < "
            f"{EVALUATION_THRESHOLDS['entity_accuracy']:.3f}"
        )
    if metrics.element_outcome_coverage < EVALUATION_THRESHOLDS["element_outcome_coverage"]:
        failures.append(
            f"element outcome coverage {metrics.element_outcome_coverage:.3f} < "
            f"{EVALUATION_THRESHOLDS['element_outcome_coverage']:.3f}"
        )
    if metrics.failed_element_count > EVALUATION_THRESHOLDS["failed_outcome_max"]:
        failures.append(
            f"failed element outcomes {metrics.failed_element_count} > "
            f"{EVALUATION_THRESHOLDS['failed_outcome_max']}"
        )
    if metrics.deferred_element_count > EVALUATION_THRESHOLDS["deferred_outcome_max"]:
        failures.append(
            f"deferred element outcomes {metrics.deferred_element_count} > "
            f"{EVALUATION_THRESHOLDS['deferred_outcome_max']}"
        )
    if metrics.unbacked_claim_count > EVALUATION_THRESHOLDS["unbacked_claim_max"]:
        failures.append(
            f"unbacked verified claims {metrics.unbacked_claim_count} > "
            f"{EVALUATION_THRESHOLDS['unbacked_claim_max']}"
        )
    if metrics.citation_coverage < EVALUATION_THRESHOLDS["citation_coverage"]:
        failures.append(
            f"citation coverage {metrics.citation_coverage:.3f} < " f"{EVALUATION_THRESHOLDS['citation_coverage']:.3f}"
        )
    if metrics.verified_evidence_coverage < EVALUATION_THRESHOLDS["verified_evidence_coverage"]:
        failures.append(
            f"verified evidence coverage {metrics.verified_evidence_coverage:.3f} < "
            f"{EVALUATION_THRESHOLDS['verified_evidence_coverage']:.3f}"
        )
    if metrics.inference_origin_candidate_count > EVALUATION_THRESHOLDS["inference_origin_candidate_max"]:
        failures.append(
            f"inference-origin candidate entities {metrics.inference_origin_candidate_count} > "
            f"{EVALUATION_THRESHOLDS['inference_origin_candidate_max']}"
        )
    if metrics.relationship_consistency_error_count > EVALUATION_THRESHOLDS["relationship_consistency_error_max"]:
        failures.append(
            "relationship consistency errors "
            f"{metrics.relationship_consistency_error_count} > "
            f"{EVALUATION_THRESHOLDS['relationship_consistency_error_max']}"
        )
    if metrics.ghost_record_count > EVALUATION_THRESHOLDS["ghost_record_max"]:
        failures.append(
            f"ghost derived records {metrics.ghost_record_count} > " f"{EVALUATION_THRESHOLDS['ghost_record_max']}"
        )
    if metrics.page_reconciliation_coverage < EVALUATION_THRESHOLDS["page_reconciliation_coverage"]:
        failures.append(
            f"page reconciliation coverage {metrics.page_reconciliation_coverage:.3f} < "
            f"{EVALUATION_THRESHOLDS['page_reconciliation_coverage']:.3f}"
        )
    return tuple(failures)


def build_run_id(
    *,
    corpus_version: str,
    evaluation_mode: EvaluationMode,
    model_id: str | None,
    prompt_version: str | None,
    index_version: str | None,
    started_at: datetime,
) -> str:
    components = (
        corpus_version,
        evaluation_mode.value,
        model_id or "",
        prompt_version or "",
        index_version or "",
        started_at.isoformat(),
    )
    digest = sha256("|".join(components).encode("utf-8")).hexdigest()[:12]
    return f"eval-{started_at.strftime('%Y%m%dT%H%M%S')}-{digest}"


def persist_run(run: EvaluationRun, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{run.run_id}.json"
    path.write_text(json.dumps(run.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_run(run_id: str, output_dir: Path = Path(".margin/evaluation_runs")) -> EvaluationRun | None:
    path = output_dir / f"{run_id}.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return run_from_dict(payload)


def list_runs(output_dir: Path = Path(".margin/evaluation_runs")) -> tuple[str, ...]:
    if not output_dir.is_dir():
        return ()
    return tuple(sorted(path.stem for path in output_dir.glob("eval-*.json") if _is_current_run_file(path)))


def _is_current_run_file(path: Path) -> bool:
    """Accept only reports that match the current evaluation schema."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return False
    current_metrics = {field.name for field in fields(EvaluationMetrics)}
    return set(metrics) == current_metrics and isinstance(payload.get("relationship_consistency_findings"), list)


def _object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _object_mappings(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_object_mapping(item) for item in value if isinstance(item, dict))


def _object_items(value: object) -> tuple[object, ...]:
    return tuple(value) if isinstance(value, list) else ()


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"expected an integer-compatible value, got {type(value).__name__}")


def _float_value(value: object) -> float:
    if isinstance(value, int | float | str):
        return float(value)
    raise TypeError(f"expected a float-compatible value, got {type(value).__name__}")


def _metrics_from_dict(payload: dict[str, object]) -> EvaluationMetrics:
    return EvaluationMetrics(
        claim_recall=_float_value(payload["claim_recall"]),
        claim_precision=_float_value(payload["claim_precision"]),
        field_recall=_float_value(payload["field_recall"]),
        relationship_precision=_float_value(payload["relationship_precision"]),
        entity_accuracy=_float_value(payload["entity_accuracy"]),
        element_outcome_coverage=_float_value(payload["element_outcome_coverage"]),
        citation_coverage=_float_value(payload["citation_coverage"]),
        verified_evidence_coverage=_float_value(payload["verified_evidence_coverage"]),
        page_reconciliation_coverage=_float_value(payload["page_reconciliation_coverage"]),
        failed_element_count=_int_value(payload["failed_element_count"]),
        deferred_element_count=_int_value(payload["deferred_element_count"]),
        failed_attempt_count=_int_value(payload["failed_attempt_count"]),
        deferred_attempt_count=_int_value(payload["deferred_attempt_count"]),
        unbacked_claim_count=_int_value(payload["unbacked_claim_count"]),
        inference_origin_candidate_count=_int_value(payload["inference_origin_candidate_count"]),
        relationship_consistency_error_count=_int_value(payload["relationship_consistency_error_count"]),
        ghost_record_count=_int_value(payload["ghost_record_count"]),
        ambiguous_match_count=_int_value(payload["ambiguous_match_count"]),
    )


def run_from_dict(payload: dict[str, object]) -> EvaluationRun:
    metrics_payload = _object_mapping(payload.get("metrics"))
    fixtures_payload = _object_mappings(payload.get("fixture_results"))
    lint_payload = _object_mappings(payload.get("relationship_consistency_findings"))
    return EvaluationRun(
        run_id=str(payload["run_id"]),
        started_at=datetime.fromisoformat(str(payload["started_at"])),
        completed_at=datetime.fromisoformat(str(payload["completed_at"])),
        corpus_version=str(payload["corpus_version"]),
        corpus_path=Path(str(payload["corpus_path"])),
        evaluation_mode=str(payload["evaluation_mode"]),
        model_id=_optional_string(payload.get("model_id")),
        prompt_version=_optional_string(payload.get("prompt_version")),
        index_version=_optional_string(payload.get("index_version")),
        active_extraction_model_id=_optional_string(payload.get("active_extraction_model_id")),
        metrics=(
            _metrics_from_dict(metrics_payload)
            if metrics_payload
            else EvaluationMetrics(
                claim_recall=1.0,
                claim_precision=1.0,
                field_recall=1.0,
                relationship_precision=1.0,
                entity_accuracy=1.0,
                element_outcome_coverage=1.0,
                citation_coverage=1.0,
                verified_evidence_coverage=1.0,
                page_reconciliation_coverage=1.0,
                failed_element_count=0,
                deferred_element_count=0,
                failed_attempt_count=0,
                deferred_attempt_count=0,
                unbacked_claim_count=0,
                inference_origin_candidate_count=0,
                relationship_consistency_error_count=0,
                ghost_record_count=0,
                ambiguous_match_count=0,
            )
        ),
        fixture_results=tuple(fixture_result_from_dict(row) for row in fixtures_payload),
        relationship_consistency_findings=tuple(dict(item) for item in lint_payload),
        gate_failures=tuple(str(item) for item in _object_items(payload.get("gate_failures"))),
        status=str(payload.get("status", "unknown")),
    )


def fixture_result_from_dict(payload: dict[str, object]) -> FixtureResult:
    source_document_id = payload.get("source_document_id")
    return FixtureResult(
        fixture_id=str(payload["fixture_id"]),
        domain_id=str(payload["domain_id"]),
        source_document_id=(source_document_id if isinstance(source_document_id, str) else None),
        claim_matches=tuple(match_from_dict(row) for row in _object_mappings(payload.get("claim_matches"))),
        field_matches=tuple(match_from_dict(row) for row in _object_mappings(payload.get("field_matches"))),
        entity_matches=tuple(match_from_dict(row) for row in _object_mappings(payload.get("entity_matches"))),
        relationship_matches=tuple(
            match_from_dict(row) for row in _object_mappings(payload.get("relationship_matches"))
        ),
        no_claim_matches=tuple(match_from_dict(row) for row in _object_mappings(payload.get("no_claim_matches"))),
        failed_element_count=_int_value(payload.get("failed_element_count", 0)),
        deferred_element_count=_int_value(payload.get("deferred_element_count", 0)),
        failed_attempt_count=_int_value(payload.get("failed_attempt_count", 0)),
        deferred_attempt_count=_int_value(payload.get("deferred_attempt_count", 0)),
        terminal_outcome_count=_int_value(payload.get("terminal_outcome_count", 0)),
        unaccounted_element_count=_int_value(payload.get("unaccounted_element_count", 0)),
        unbacked_claim_count=_int_value(payload.get("unbacked_claim_count", 0)),
        page_reconciliation_missing=tuple(
            _int_value(item) for item in _object_items(payload.get("page_reconciliation_missing"))
        ),
        expected_pages_total=_int_value(payload.get("expected_pages_total", 0)),
        expected_pages_reconciled=_int_value(payload.get("expected_pages_reconciled", 0)),
        verified_claims_with_evidence=_int_value(payload.get("verified_claims_with_evidence", 0)),
        verified_relationships_with_evidence=_int_value(payload.get("verified_relationships_with_evidence", 0)),
        mapped_elements_with_outcome=_int_value(payload.get("mapped_elements_with_outcome", 0)),
        inference_origin_candidate_ids=tuple(
            str(item) for item in _object_items(payload.get("inference_origin_candidate_ids"))
        ),
        ghost_record_ids=tuple(str(item) for item in _object_items(payload.get("ghost_record_ids"))),
        ambiguous_match_count=_int_value(payload.get("ambiguous_match_count", 0)),
    )


def aggregate_metrics(results: tuple[FixtureResult, ...]) -> EvaluationMetrics:
    claim_tp = claim_fp = claim_fn = 0
    field_tp = field_fn = 0
    relationship_tp = relationship_fp = 0
    entity_tp = entity_fn = 0
    failed = deferred = failed_attempts = deferred_attempts = 0
    terminal = unbacked = citation_hits = citation_total = 0
    verified_claims_with_evidence = verified_relationships_with_evidence = 0
    expected_pages_total = expected_pages_reconciled = 0
    inference_origin_candidates = ghost_records = 0
    ambiguous_matches = 0

    for result in results:
        for match in result.claim_matches:
            if match.kind == MatchKind.TRUE_POSITIVE:
                claim_tp += 1
                citation_hits += len(match.evidence_ids)
            elif match.kind == MatchKind.FALSE_POSITIVE:
                claim_fp += 1
            elif match.kind == MatchKind.FALSE_NEGATIVE:
                claim_fn += 1
            citation_total += 1
        for match in result.field_matches:
            if match.kind == MatchKind.TRUE_POSITIVE:
                field_tp += 1
            elif match.kind == MatchKind.FALSE_NEGATIVE:
                field_fn += 1
        for match in result.entity_matches:
            if match.kind == MatchKind.TRUE_POSITIVE:
                entity_tp += 1
            elif match.kind == MatchKind.FALSE_NEGATIVE:
                entity_fn += 1
        for match in result.relationship_matches:
            if match.kind == MatchKind.TRUE_POSITIVE:
                relationship_tp += 1
            elif match.kind == MatchKind.FALSE_POSITIVE:
                relationship_fp += 1
        failed += result.failed_element_count
        deferred += result.deferred_element_count
        failed_attempts += result.failed_attempt_count
        deferred_attempts += result.deferred_attempt_count
        terminal += result.terminal_outcome_count
        unbacked += result.unbacked_claim_count
        verified_claims_with_evidence += result.verified_claims_with_evidence
        verified_relationships_with_evidence += result.verified_relationships_with_evidence
        expected_pages_total += result.expected_pages_total
        expected_pages_reconciled += result.expected_pages_reconciled
        inference_origin_candidates += len(result.inference_origin_candidate_ids)
        ghost_records += len(result.ghost_record_ids)
        ambiguous_matches += result.ambiguous_match_count

    claim_recall = _ratio(claim_tp, claim_tp + claim_fn)
    claim_precision = _ratio(claim_tp, claim_tp + claim_fp)
    field_recall = _ratio(field_tp, field_tp + field_fn)
    relationship_precision = _ratio(relationship_tp, relationship_tp + relationship_fp)
    entity_accuracy = _ratio(entity_tp, entity_tp + entity_fn)
    coverage = _ratio(terminal, terminal + sum(r.unaccounted_element_count for r in results))
    citation_coverage = _ratio(citation_hits, citation_total)
    verified_total = verified_claims_with_evidence + verified_relationships_with_evidence
    verified_evidence_coverage = _ratio(verified_total, claim_tp + relationship_tp)
    page_reconciliation_coverage = _ratio(expected_pages_reconciled, expected_pages_total)
    return EvaluationMetrics(
        claim_recall=claim_recall,
        claim_precision=claim_precision,
        field_recall=field_recall,
        relationship_precision=relationship_precision,
        entity_accuracy=entity_accuracy,
        element_outcome_coverage=coverage,
        citation_coverage=citation_coverage,
        verified_evidence_coverage=verified_evidence_coverage,
        page_reconciliation_coverage=page_reconciliation_coverage,
        failed_element_count=failed,
        deferred_element_count=deferred,
        failed_attempt_count=failed_attempts,
        deferred_attempt_count=deferred_attempts,
        unbacked_claim_count=unbacked,
        inference_origin_candidate_count=inference_origin_candidates,
        relationship_consistency_error_count=0,
        ghost_record_count=ghost_records,
        ambiguous_match_count=ambiguous_matches,
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def match_from_dict(payload: dict[str, object]) -> MatchRecord:
    return MatchRecord(
        expected_id=str(payload["expected_id"]),
        actual_ids=tuple(str(item) for item in _object_items(payload.get("actual_ids"))),
        kind=MatchKind(str(payload.get("kind", "true_positive"))),
        evidence_ids=tuple(str(item) for item in _object_items(payload.get("evidence_ids"))),
    )
