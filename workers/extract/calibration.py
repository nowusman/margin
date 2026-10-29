from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from margin_eval.gold_corpus import GoldCorpus, load_gold_corpus


@dataclass(frozen=True)
class SemanticCalibrationCase:
    """One expected candidate and outcome from the gold semantic corpus."""

    case_id: str
    fixture_id: str
    domain_id: str
    expected_outcome: str
    protected: bool
    candidate_id: str | None = None
    source_id: str | None = None
    target_id: str | None = None


@dataclass(frozen=True)
class SemanticCalibrationConfig:
    """Immutable configuration captured alongside one calibration run."""

    embedding_version: str | None = None
    classifier_version: str | None = None
    candidate_limit: int | None = None
    deadline_ms: int | None = None
    review_threshold: float | None = None
    recall_target: float | None = None
    review_volume_target: int | None = None
    owner: str | None = None
    approval_date: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "embedding_version": self.embedding_version,
            "classifier_version": self.classifier_version,
            "candidate_limit": self.candidate_limit,
            "deadline_ms": self.deadline_ms,
            "review_threshold": self.review_threshold,
            "recall_target": self.recall_target,
            "review_volume_target": self.review_volume_target,
            "owner": self.owner,
            "approval_date": self.approval_date,
        }


@dataclass(frozen=True)
class SemanticCandidateObservation:
    """A candidate/result observed during an explicitly supplied calibration run."""

    case_id: str
    domain_id: str
    suggested_outcome: str
    action: str = "review"
    embedding_version: str | None = None
    classifier_version: str | None = None
    latency_ms: float | None = None
    peak_memory_bytes: int | None = None


@dataclass(frozen=True)
class SemanticReplayCandidate:
    """Runtime candidate identity used to map a replay result to a gold case."""

    domain_id: str
    suggested_outcome: str
    action: str = "review"
    case_id: str | None = None
    fixture_id: str | None = None
    candidate_id: str | None = None
    source_id: str | None = None
    target_id: str | None = None
    embedding_version: str | None = None
    classifier_version: str | None = None
    latency_ms: float | None = None
    peak_memory_bytes: int | None = None


def map_semantic_replay_candidates(
    candidates: Iterable[SemanticReplayCandidate | Mapping[str, object]],
    *,
    cases: Iterable[SemanticCalibrationCase] | None = None,
) -> tuple[SemanticCandidateObservation, ...]:
    """Map runtime candidate identities to the immutable gold-case IDs.

    A replay must provide either a gold case ID, a candidate ID, or both
    source/target claim IDs from the manifest. Pair identity is directional and
    must preserve the manifest's source/target order. Unknown identities are
    retained as stable false-positive observations instead of being dropped.
    """

    selected_cases = tuple(cases) if cases is not None else load_semantic_calibration_cases()
    by_case_id = {case.case_id: case for case in selected_cases}
    by_candidate_id: dict[str, list[SemanticCalibrationCase]] = {}
    by_pair: dict[tuple[str, str], list[SemanticCalibrationCase]] = {}
    for case in selected_cases:
        if case.candidate_id:
            by_candidate_id.setdefault(case.candidate_id, []).append(case)
        if case.source_id and case.target_id:
            by_pair.setdefault((case.source_id, case.target_id), []).append(case)

    observations: list[SemanticCandidateObservation] = []
    for index, raw_candidate in enumerate(candidates):
        candidate = _coerce_semantic_replay_candidate(raw_candidate)
        matched_case = _match_replay_candidate(
            candidate, by_case_id=by_case_id, by_candidate_id=by_candidate_id, by_pair=by_pair
        )
        case_id = matched_case.case_id if matched_case is not None else _unmapped_replay_case_id(candidate, index)
        observations.append(
            SemanticCandidateObservation(
                case_id=case_id,
                domain_id=candidate.domain_id,
                suggested_outcome=candidate.suggested_outcome,
                action=candidate.action,
                embedding_version=candidate.embedding_version,
                classifier_version=candidate.classifier_version,
                latency_ms=candidate.latency_ms,
                peak_memory_bytes=candidate.peak_memory_bytes,
            )
        )
    return tuple(observations)


def run_semantic_replay_calibration(
    candidates: Iterable[SemanticReplayCandidate | Mapping[str, object]],
    *,
    corpus: GoldCorpus | None = None,
    manifest_path: Path = Path("tests/fixtures/gold_corpus/manifest.json"),
    configuration: SemanticCalibrationConfig | None = None,
    latency_samples_ms: Iterable[float] | None = None,
    peak_memory_samples_bytes: Iterable[int] | None = None,
) -> SemanticCalibrationReport:
    """Calibrate an explicitly supplied runtime replay against the gold corpus.

    The caller is responsible for extracting gold fixture/candidate or claim
    pair identity into each replay record. Records that cannot be mapped are
    retained as false positives by :func:`map_semantic_replay_candidates`.
    """

    selected_corpus = corpus or load_gold_corpus(manifest_path)
    cases = load_semantic_calibration_cases(selected_corpus)
    observations = map_semantic_replay_candidates(candidates, cases=cases)
    return run_semantic_calibration(
        observations,
        corpus=selected_corpus,
        manifest_path=manifest_path,
        configuration=configuration,
        latency_samples_ms=latency_samples_ms,
        peak_memory_samples_bytes=peak_memory_samples_bytes,
    )


def _coerce_semantic_replay_candidate(
    candidate: SemanticReplayCandidate | Mapping[str, object]
) -> SemanticReplayCandidate:
    if isinstance(candidate, SemanticReplayCandidate):
        _validate_replay_candidate(candidate)
        return candidate
    required = {"domain_id", "suggested_outcome"}
    missing = sorted(name for name in required if not isinstance(candidate.get(name), str))
    if missing:
        raise ValueError("semantic replay candidate is missing: " + ", ".join(missing))

    def optional_string(name: str) -> str | None:
        value = candidate.get(name)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"semantic replay candidate field {name} must be a string")
        return value

    def optional_float(name: str) -> float | None:
        value = candidate.get(name)
        if value is None:
            return None
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ValueError(f"semantic replay candidate field {name} must be numeric")
        return float(value)

    def optional_int(name: str) -> int | None:
        value = candidate.get(name)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"semantic replay candidate field {name} must be an integer")
        return value

    case_id = optional_string("case_id")
    candidate_id = optional_string("candidate_id")
    source_id = optional_string("source_id")
    target_id = optional_string("target_id")

    replay_candidate = SemanticReplayCandidate(
        domain_id=optional_string("domain_id") or "",
        suggested_outcome=optional_string("suggested_outcome") or "",
        action=optional_string("action") or "review",
        case_id=case_id,
        fixture_id=optional_string("fixture_id"),
        candidate_id=candidate_id,
        source_id=source_id,
        target_id=target_id,
        embedding_version=optional_string("embedding_version"),
        classifier_version=optional_string("classifier_version"),
        latency_ms=optional_float("latency_ms"),
        peak_memory_bytes=optional_int("peak_memory_bytes"),
    )
    _validate_replay_candidate(replay_candidate)
    return replay_candidate


def _validate_replay_candidate(candidate: SemanticReplayCandidate) -> None:
    if not candidate.domain_id or not candidate.suggested_outcome:
        raise ValueError("semantic replay candidate must include domain_id and suggested_outcome")
    if not candidate.case_id and not candidate.candidate_id and not (candidate.source_id and candidate.target_id):
        raise ValueError("semantic replay candidate must include case_id, candidate_id, or source_id and target_id")
    if bool(candidate.source_id) != bool(candidate.target_id):
        raise ValueError("semantic replay candidate must include both source_id and target_id")


@dataclass(frozen=True)
class SemanticCalibrationDomainReport:
    domain_id: str
    embedding_version: str | None
    classifier_version: str | None
    status: str
    expected_candidate_count: int
    observed_candidate_count: int
    matched_candidate_count: int
    correct_outcome_count: int
    protected_case_count: int
    protected_cases_observed: int
    protected_case_failures: tuple[str, ...]
    review_volume: int
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    latency_p99_ms: float | None
    peak_memory_bytes: int | None

    @property
    def candidate_precision(self) -> float | None:
        if self.status != "measured":
            return None
        return _optional_ratio(self.matched_candidate_count, self.observed_candidate_count)

    @property
    def candidate_recall(self) -> float | None:
        if self.status != "measured":
            return None
        return _optional_ratio(self.matched_candidate_count, self.expected_candidate_count)

    @property
    def suggested_outcome_precision(self) -> float | None:
        if self.status != "measured":
            return None
        return _optional_ratio(self.correct_outcome_count, self.matched_candidate_count)

    def to_dict(self) -> dict[str, object]:
        return {
            "domain_id": self.domain_id,
            "embedding_version": self.embedding_version,
            "classifier_version": self.classifier_version,
            "status": self.status,
            "expected_candidate_count": self.expected_candidate_count,
            "observed_candidate_count": self.observed_candidate_count,
            "matched_candidate_count": self.matched_candidate_count,
            "correct_outcome_count": self.correct_outcome_count,
            "candidate_precision": _rounded(self.candidate_precision),
            "candidate_recall": _rounded(self.candidate_recall),
            "suggested_outcome_precision": _rounded(self.suggested_outcome_precision),
            "protected_case_count": self.protected_case_count,
            "protected_cases_observed": self.protected_cases_observed,
            "protected_case_failures": list(self.protected_case_failures),
            "review_volume": self.review_volume,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_p99_ms": self.latency_p99_ms,
            "peak_memory_bytes": self.peak_memory_bytes,
        }


@dataclass(frozen=True)
class SemanticCalibrationReport:
    """Measured semantic candidate evidence without granting merge authority.

    ``observations=None`` in :func:`run_semantic_calibration` deliberately
    produces an ``unproven`` report. Incomplete observation coverage produces
    ``partial`` so recall/precision stay null. The report never treats a
    suggestion as an automatic merge, including for the exact-duplicate gold case.
    """

    corpus_version: str
    embedding_version: str | None
    classifier_version: str | None
    status: str
    runtime_evidence_status: str
    expected_candidate_count: int
    observed_candidate_count: int
    matched_candidate_count: int
    correct_outcome_count: int
    false_negative_case_ids: tuple[str, ...]
    false_positive_case_ids: tuple[str, ...]
    outcome_mismatch_case_ids: tuple[str, ...]
    false_equivalence_case_ids: tuple[str, ...]
    protected_case_count: int
    protected_cases_observed: int
    protected_case_unmeasured: tuple[str, ...]
    protected_case_failures: tuple[str, ...]
    semantic_auto_merge_violations: tuple[str, ...]
    review_volume: int
    latency_ms: float | None
    peak_memory_bytes: int | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    latency_p99_ms: float | None
    configuration: SemanticCalibrationConfig
    domain_reports: tuple[SemanticCalibrationDomainReport, ...]

    @property
    def candidate_precision(self) -> float | None:
        if self.status != "measured":
            return None
        return _optional_ratio(self.matched_candidate_count, self.observed_candidate_count)

    @property
    def candidate_recall(self) -> float | None:
        if self.status != "measured":
            return None
        return _optional_ratio(self.matched_candidate_count, self.expected_candidate_count)

    @property
    def suggested_outcome_precision(self) -> float | None:
        if self.status != "measured":
            return None
        return _optional_ratio(self.correct_outcome_count, self.matched_candidate_count)

    @property
    def protected_cases_pass(self) -> bool | None:
        if self.protected_case_failures:
            return False
        if not self.protected_case_count or not self.protected_cases_observed or self.protected_case_unmeasured:
            return None
        return True

    def to_dict(self) -> dict[str, object]:
        return {
            "corpus_version": self.corpus_version,
            "embedding_version": self.embedding_version,
            "classifier_version": self.classifier_version,
            "status": self.status,
            "runtime_evidence_status": self.runtime_evidence_status,
            "expected_candidate_count": self.expected_candidate_count,
            "observed_candidate_count": self.observed_candidate_count,
            "matched_candidate_count": self.matched_candidate_count,
            "correct_outcome_count": self.correct_outcome_count,
            "candidate_precision": _rounded(self.candidate_precision),
            "candidate_recall": _rounded(self.candidate_recall),
            "suggested_outcome_precision": _rounded(self.suggested_outcome_precision),
            "false_negative_case_ids": list(self.false_negative_case_ids),
            "false_positive_case_ids": list(self.false_positive_case_ids),
            "outcome_mismatch_case_ids": list(self.outcome_mismatch_case_ids),
            "false_equivalence_case_ids": list(self.false_equivalence_case_ids),
            "protected_case_outcomes": {
                "total": self.protected_case_count,
                "observed": self.protected_cases_observed,
                "unmeasured_case_ids": list(self.protected_case_unmeasured),
                "failures": list(self.protected_case_failures),
                "pass": self.protected_cases_pass,
            },
            "semantic_auto_merge_violations": list(self.semantic_auto_merge_violations),
            "policy": {"semantic_auto_merge_enabled": False},
            "review_volume": self.review_volume,
            "latency_ms": self.latency_ms,
            "latency_percentiles_ms": {
                "p50": self.latency_p50_ms,
                "p95": self.latency_p95_ms,
                "p99": self.latency_p99_ms,
            },
            "peak_memory_bytes": self.peak_memory_bytes,
            "configuration": self.configuration.to_dict(),
            "domain_metrics": [report.to_dict() for report in self.domain_reports],
        }


def semantic_case_id(fixture_id: str, decision: Mapping[str, object]) -> str:
    """Return a stable opaque ID for one manifest semantic decision."""

    candidate_id = decision.get("candidate_id")
    if isinstance(candidate_id, str) and candidate_id:
        return f"{fixture_id}::candidate::{candidate_id}"
    source_id = decision.get("source_id")
    target_id = decision.get("target_id")
    if isinstance(source_id, str) and isinstance(target_id, str) and source_id and target_id:
        return f"{fixture_id}::pair::{source_id}::{target_id}"
    raise ValueError(f"semantic decision in {fixture_id} must contain candidate_id or source_id and target_id")


def _match_replay_candidate(
    candidate: SemanticReplayCandidate,
    *,
    by_case_id: Mapping[str, SemanticCalibrationCase],
    by_candidate_id: Mapping[str, list[SemanticCalibrationCase]],
    by_pair: Mapping[tuple[str, str], list[SemanticCalibrationCase]],
) -> SemanticCalibrationCase | None:
    if candidate.case_id:
        return by_case_id.get(candidate.case_id)
    if candidate.candidate_id:
        matches = by_candidate_id.get(candidate.candidate_id, [])
        if candidate.fixture_id:
            matches = [case for case in matches if case.fixture_id == candidate.fixture_id]
        if len(matches) == 1:
            return matches[0]
    if candidate.source_id and candidate.target_id:
        matches = by_pair.get((candidate.source_id, candidate.target_id), [])
        if candidate.fixture_id:
            matches = [case for case in matches if case.fixture_id == candidate.fixture_id]
        if len(matches) == 1:
            return matches[0]
    return None


def _unmapped_replay_case_id(candidate: SemanticReplayCandidate, index: int) -> str:
    identity = candidate.case_id or candidate.candidate_id
    if not identity and candidate.source_id and candidate.target_id:
        identity = f"{candidate.source_id}::{candidate.target_id}"
    return f"unmapped::{identity or index}"


def load_semantic_calibration_cases(
    corpus: GoldCorpus | None = None, *, manifest_path: Path = Path("tests/fixtures/gold_corpus/manifest.json")
) -> tuple[SemanticCalibrationCase, ...]:
    """Load candidate/outcome expectations from the existing gold manifest."""

    selected_corpus = corpus or load_gold_corpus(manifest_path)
    cases: list[SemanticCalibrationCase] = []
    seen_ids: set[str] = set()
    for fixture in selected_corpus.fixtures:
        for decision in fixture.semantic_decisions:
            case_id = semantic_case_id(fixture.fixture_id, decision)
            if case_id in seen_ids:
                raise ValueError(f"gold corpus contains duplicate semantic calibration case: {case_id}")
            seen_ids.add(case_id)
            expected_outcome = decision.get("decision")
            if not isinstance(expected_outcome, str) or not expected_outcome:
                raise ValueError(f"semantic calibration case {case_id} is missing decision")
            protected = expected_outcome != "equivalent" or any(
                bool(decision.get(flag)) for flag in ("guarded_review", "separate_blocks", "conflict_detected")
            )
            cases.append(
                SemanticCalibrationCase(
                    case_id=case_id,
                    fixture_id=fixture.fixture_id,
                    domain_id=fixture.domain_id,
                    expected_outcome=expected_outcome,
                    protected=protected,
                    candidate_id=(
                        str(decision["candidate_id"]) if isinstance(decision.get("candidate_id"), str) else None
                    ),
                    source_id=(str(decision["source_id"]) if isinstance(decision.get("source_id"), str) else None),
                    target_id=(str(decision["target_id"]) if isinstance(decision.get("target_id"), str) else None),
                )
            )
    return tuple(cases)


def export_deterministic_semantic_observations(
    corpus: GoldCorpus | None = None, *, manifest_path: Path = Path("tests/fixtures/gold_corpus/manifest.json")
) -> tuple[SemanticCandidateObservation, ...]:
    """Export observations for gold cases decidable without a live extraction provider.

    Exact-attach duplicates and polarity-guard contradictions are mapped from the
    gold claim text alone. Cases that need a live extractor/classifier remain
    unmapped so callers can keep overall P3.9 status honest.
    """

    selected_corpus = corpus or load_gold_corpus(manifest_path)
    observations: list[SemanticCandidateObservation] = []
    for fixture in selected_corpus.fixtures:
        claims_by_id = {claim.id: claim for claim in fixture.claims}
        for decision in fixture.semantic_decisions:
            observation = _deterministic_observation_for_decision(
                fixture_id=fixture.fixture_id, domain_id=fixture.domain_id, decision=decision, claims_by_id=claims_by_id
            )
            if observation is not None:
                observations.append(observation)
    return tuple(observations)


def _deterministic_observation_for_decision(
    *, fixture_id: str, domain_id: str, decision: Mapping[str, object], claims_by_id: Mapping[str, object]
) -> SemanticCandidateObservation | None:
    from margin_db.semantic_resolution import classify_semantic_pair

    case_id = semantic_case_id(fixture_id, decision)
    action = decision.get("action")
    if isinstance(action, str) and action.casefold() == "exact_attach":
        return SemanticCandidateObservation(
            case_id=case_id, domain_id=domain_id, suggested_outcome="equivalent", action="exact_attach"
        )

    source_id = decision.get("source_id")
    target_id = decision.get("target_id")
    if not isinstance(source_id, str) or not isinstance(target_id, str):
        return None
    left_claim = claims_by_id.get(source_id)
    right_claim = claims_by_id.get(target_id)
    if left_claim is None or right_claim is None:
        return None

    left = _gold_claim_revision_view(left_claim)
    right = _gold_claim_revision_view(right_claim)
    result = classify_semantic_pair(left, right, classifier=None)
    if result.outcome == "equivalent" and left.normalized_statement == right.normalized_statement:
        return SemanticCandidateObservation(
            case_id=case_id, domain_id=domain_id, suggested_outcome="equivalent", action="exact_attach"
        )
    if result.outcome == "contradicts" and "polarity" in result.protected_field_differences:
        return SemanticCandidateObservation(
            case_id=case_id, domain_id=domain_id, suggested_outcome="contradicts", action="review"
        )
    return None


@dataclass(frozen=True)
class _GoldClaimRevisionView:
    statement: str
    normalized_statement: str
    type: str
    polarity: str
    qualifiers: dict[str, object]
    valid_from: object = None
    valid_to: object = None
    metadata_: dict[str, object] = field(default_factory=dict)


def _gold_claim_revision_view(claim: object) -> _GoldClaimRevisionView:
    from margin_schema.text_normalization import normalize_statement

    statement = str(getattr(claim, "statement", ""))
    claim_type = str(getattr(claim, "claim_type", "fact") or "fact")
    polarity = str(getattr(claim, "polarity", "supports") or "supports")
    qualifiers = dict(getattr(claim, "qualifiers", {}) or {})
    return _GoldClaimRevisionView(
        statement=statement,
        normalized_statement=normalize_statement(statement),
        type=claim_type,
        polarity=polarity,
        qualifiers=qualifiers,
    )


def run_semantic_calibration(
    observations: Iterable[SemanticCandidateObservation] | None = None,
    *,
    corpus: GoldCorpus | None = None,
    manifest_path: Path = Path("tests/fixtures/gold_corpus/manifest.json"),
    configuration: SemanticCalibrationConfig | None = None,
    embedding_version: str | None = None,
    classifier_version: str | None = None,
    candidate_limit: int | None = None,
    deadline_ms: int | None = None,
    review_threshold: float | None = None,
    recall_target: float | None = None,
    review_volume_target: int | None = None,
    owner: str | None = None,
    approval_date: str | None = None,
    latency_ms: float | None = None,
    peak_memory_bytes: int | None = None,
    latency_samples_ms: Iterable[float] | None = None,
    peak_memory_samples_bytes: Iterable[int] | None = None,
) -> SemanticCalibrationReport:
    """Measure supplied candidate evidence against the gold semantic cases.

    This function does not invoke candidate retrieval, a classifier, or a
    merge operation.  Callers must supply observations from the run they want
    to measure; omitted observations are reported as ``unproven``.
    """

    selected_corpus = corpus or load_gold_corpus(manifest_path)
    cases = load_semantic_calibration_cases(selected_corpus)
    expected_by_id = {case.case_id: case for case in cases}
    observations_supplied = observations is not None
    selected_observations = tuple(observations or ())
    duplicate_case_ids = _duplicate_observation_case_ids(selected_observations)
    if duplicate_case_ids:
        raise ValueError(
            "semantic calibration observations must contain one observation per case: " + ", ".join(duplicate_case_ids)
        )
    selected_configuration = _calibration_configuration(
        configuration,
        embedding_version=embedding_version,
        classifier_version=classifier_version,
        candidate_limit=candidate_limit,
        deadline_ms=deadline_ms,
        review_threshold=review_threshold,
        recall_target=recall_target,
        review_volume_target=review_volume_target,
        owner=owner,
        approval_date=approval_date,
    )
    selected_configuration = replace(
        selected_configuration,
        embedding_version=_resolve_observation_version(
            selected_observations, "embedding_version", selected_configuration.embedding_version
        ),
        classifier_version=_resolve_observation_version(
            selected_observations, "classifier_version", selected_configuration.classifier_version
        ),
    )
    selected_latency_samples = _runtime_samples(
        latency_samples_ms, (observation.latency_ms for observation in selected_observations), scalar=latency_ms
    )
    selected_memory_samples = _runtime_samples(
        peak_memory_samples_bytes,
        (observation.peak_memory_bytes for observation in selected_observations),
        scalar=peak_memory_bytes,
    )
    _validate_runtime_measurements(
        latency_ms=latency_ms,
        peak_memory_bytes=peak_memory_bytes,
        latency_samples_ms=selected_latency_samples,
        peak_memory_samples_bytes=selected_memory_samples,
    )
    matched_case_ids: set[str] = set()
    false_positive_case_ids: list[str] = []
    outcome_mismatch_case_ids: list[str] = []
    false_equivalence_case_ids: list[str] = []
    correct_outcome_count = 0
    auto_merge_violations: list[str] = []
    protected_case_failures: set[str] = set()
    observed_protected_case_ids: set[str] = set()

    for observation in selected_observations:
        case = expected_by_id.get(observation.case_id)
        if case is None or case.domain_id != observation.domain_id:
            false_positive_case_ids.append(observation.case_id)
            if _is_semantic_auto_merge(observation.action):
                auto_merge_violations.append(observation.case_id)
            continue
        matched_case_ids.add(case.case_id)
        if observation.suggested_outcome == case.expected_outcome:
            correct_outcome_count += 1
        else:
            outcome_mismatch_case_ids.append(case.case_id)
            if case.expected_outcome != "equivalent" and observation.suggested_outcome == "equivalent":
                false_equivalence_case_ids.append(case.case_id)
        if case.protected:
            observed_protected_case_ids.add(case.case_id)
            if (
                observation.suggested_outcome != case.expected_outcome
                or not _is_review_or_separate_action(observation.action)
                or _is_semantic_auto_merge(observation.action)
            ):
                protected_case_failures.add(case.case_id)
        if _is_semantic_auto_merge(observation.action):
            auto_merge_violations.append(case.case_id)

    false_negative_case_ids = tuple(sorted(set(expected_by_id) - matched_case_ids))
    protected_cases = {case.case_id for case in cases if case.protected}
    protected_case_unmeasured = tuple(sorted(protected_cases - observed_protected_case_ids))
    if not observations_supplied:
        status = "unproven"
    elif not cases:
        status = "blocked"
    elif false_negative_case_ids:
        # Incomplete coverage stays partial so recall/precision stay null until every case is observed.
        status = "partial"
    else:
        status = "measured"
    runtime_evidence_status = "measured" if selected_latency_samples and selected_memory_samples else "unproven"
    latency_p50_ms = _percentile(selected_latency_samples, 0.50)
    latency_p95_ms = _percentile(selected_latency_samples, 0.95)
    latency_p99_ms = _percentile(selected_latency_samples, 0.99)
    reported_peak_memory_bytes = peak_memory_bytes
    if reported_peak_memory_bytes is None and selected_memory_samples:
        reported_peak_memory_bytes = int(max(selected_memory_samples))
    domain_reports = _domain_reports(
        cases,
        selected_observations,
        matched_case_ids=matched_case_ids,
        protected_case_failures=protected_case_failures,
        observed_protected_case_ids=observed_protected_case_ids,
        status=status,
        configuration=selected_configuration,
    )
    return SemanticCalibrationReport(
        corpus_version=selected_corpus.version,
        embedding_version=selected_configuration.embedding_version,
        classifier_version=selected_configuration.classifier_version,
        status=status,
        runtime_evidence_status=runtime_evidence_status,
        expected_candidate_count=len(cases),
        observed_candidate_count=len(selected_observations),
        matched_candidate_count=len(matched_case_ids),
        correct_outcome_count=correct_outcome_count,
        false_negative_case_ids=false_negative_case_ids,
        false_positive_case_ids=tuple(false_positive_case_ids),
        outcome_mismatch_case_ids=tuple(sorted(set(outcome_mismatch_case_ids))),
        false_equivalence_case_ids=tuple(sorted(set(false_equivalence_case_ids))),
        protected_case_count=len(protected_cases),
        protected_cases_observed=len(observed_protected_case_ids),
        protected_case_unmeasured=protected_case_unmeasured,
        protected_case_failures=tuple(sorted(protected_case_failures)),
        semantic_auto_merge_violations=tuple(sorted(set(auto_merge_violations))),
        review_volume=sum(1 for observation in selected_observations if _is_review_action(observation.action)),
        latency_ms=latency_ms,
        peak_memory_bytes=reported_peak_memory_bytes,
        latency_p50_ms=latency_p50_ms,
        latency_p95_ms=latency_p95_ms,
        latency_p99_ms=latency_p99_ms,
        configuration=selected_configuration,
        domain_reports=domain_reports,
    )


def write_semantic_calibration_report(report: SemanticCalibrationReport, path: Path) -> None:
    """Write one deterministic JSON evidence artifact for a calibration run."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _domain_reports(
    cases: tuple[SemanticCalibrationCase, ...],
    observations: tuple[SemanticCandidateObservation, ...],
    *,
    matched_case_ids: set[str],
    protected_case_failures: set[str],
    observed_protected_case_ids: set[str],
    status: str,
    configuration: SemanticCalibrationConfig,
) -> tuple[SemanticCalibrationDomainReport, ...]:
    domains = sorted({case.domain_id for case in cases} | {observation.domain_id for observation in observations})
    expected_by_domain = {domain: [case for case in cases if case.domain_id == domain] for domain in domains}
    observed_by_domain = {
        domain: [observation for observation in observations if observation.domain_id == domain] for domain in domains
    }
    expected_lookup = {case.case_id: case for case in cases}
    reports: list[SemanticCalibrationDomainReport] = []
    for domain in domains:
        domain_cases = expected_by_domain[domain]
        domain_observations = observed_by_domain[domain]
        domain_case_ids = {case.case_id for case in domain_cases}
        domain_matched = matched_case_ids & domain_case_ids
        domain_correct = sum(
            1
            for observation in domain_observations
            if observation.case_id in domain_case_ids
            and observation.suggested_outcome == expected_lookup[observation.case_id].expected_outcome
        )
        domain_protected = {case.case_id for case in domain_cases if case.protected}
        domain_latency_samples = tuple(
            observation.latency_ms for observation in domain_observations if observation.latency_ms is not None
        )
        domain_memory_samples = tuple(
            observation.peak_memory_bytes
            for observation in domain_observations
            if observation.peak_memory_bytes is not None
        )
        reports.append(
            SemanticCalibrationDomainReport(
                domain_id=domain,
                embedding_version=configuration.embedding_version,
                classifier_version=configuration.classifier_version,
                status=status,
                expected_candidate_count=len(domain_cases),
                observed_candidate_count=len(domain_observations),
                matched_candidate_count=len(domain_matched),
                correct_outcome_count=domain_correct,
                protected_case_count=len(domain_protected),
                protected_cases_observed=len(observed_protected_case_ids & domain_protected),
                protected_case_failures=tuple(sorted(protected_case_failures & domain_protected)),
                review_volume=sum(1 for observation in domain_observations if _is_review_action(observation.action)),
                latency_p50_ms=_percentile(domain_latency_samples, 0.50),
                latency_p95_ms=_percentile(domain_latency_samples, 0.95),
                latency_p99_ms=_percentile(domain_latency_samples, 0.99),
                peak_memory_bytes=max(domain_memory_samples, default=None),
            )
        )
    return tuple(reports)


def _optional_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _calibration_configuration(
    configuration: SemanticCalibrationConfig | None,
    *,
    embedding_version: str | None,
    classifier_version: str | None,
    candidate_limit: int | None,
    deadline_ms: int | None,
    review_threshold: float | None,
    recall_target: float | None,
    review_volume_target: int | None,
    owner: str | None,
    approval_date: str | None,
) -> SemanticCalibrationConfig:
    if configuration is None:
        configuration = SemanticCalibrationConfig(
            embedding_version=embedding_version,
            classifier_version=classifier_version,
            candidate_limit=candidate_limit,
            deadline_ms=deadline_ms,
            review_threshold=review_threshold,
            recall_target=recall_target,
            review_volume_target=review_volume_target,
            owner=owner,
            approval_date=approval_date,
        )
    else:
        for name, value in (
            ("embedding_version", embedding_version),
            ("classifier_version", classifier_version),
            ("candidate_limit", candidate_limit),
            ("deadline_ms", deadline_ms),
            ("review_threshold", review_threshold),
            ("recall_target", recall_target),
            ("review_volume_target", review_volume_target),
            ("owner", owner),
            ("approval_date", approval_date),
        ):
            configured_value = getattr(configuration, name)
            if value is not None and configured_value is not None and value != configured_value:
                raise ValueError(f"calibration configuration conflict for {name}")
        configuration = SemanticCalibrationConfig(
            embedding_version=(
                configuration.embedding_version if configuration.embedding_version is not None else embedding_version
            ),
            classifier_version=(
                configuration.classifier_version if configuration.classifier_version is not None else classifier_version
            ),
            candidate_limit=(
                configuration.candidate_limit if configuration.candidate_limit is not None else candidate_limit
            ),
            deadline_ms=configuration.deadline_ms if configuration.deadline_ms is not None else deadline_ms,
            review_threshold=(
                configuration.review_threshold if configuration.review_threshold is not None else review_threshold
            ),
            recall_target=configuration.recall_target if configuration.recall_target is not None else recall_target,
            review_volume_target=(
                configuration.review_volume_target
                if configuration.review_volume_target is not None
                else review_volume_target
            ),
            owner=configuration.owner if configuration.owner is not None else owner,
            approval_date=configuration.approval_date if configuration.approval_date is not None else approval_date,
        )
    _validate_calibration_configuration(configuration)
    return configuration


def _resolve_observation_version(
    observations: tuple[SemanticCandidateObservation, ...], field_name: str, configured_value: str | None
) -> str | None:
    observed_values = {value for observation in observations if (value := getattr(observation, field_name)) is not None}
    if configured_value is not None and observed_values - {configured_value}:
        raise ValueError(f"calibration observations conflict with configured {field_name}")
    if len(observed_values) > 1:
        raise ValueError(f"calibration observations contain multiple {field_name} values")
    return configured_value or next(iter(observed_values), None)


def _runtime_samples(
    explicit_samples: Iterable[float | int] | None,
    observed_samples: Iterable[float | int | None],
    *,
    scalar: float | int | None,
) -> tuple[float | int, ...]:
    if explicit_samples is not None:
        return tuple(explicit_samples)
    observed_values = tuple(value for value in observed_samples if value is not None)
    if observed_values:
        return observed_values
    return (scalar,) if scalar is not None else ()


def _percentile(values: Iterable[float | int], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight, 4)


def _rounded(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _is_semantic_auto_merge(action: str) -> bool:
    return action.casefold() in {"auto_merge", "merge", "semantic_auto_merge"}


def _is_review_action(action: str) -> bool:
    return action.casefold() in {"review", "review_required"}


def _is_review_or_separate_action(action: str) -> bool:
    return _is_review_action(action) or action.casefold() in {"keep_separate", "separate"}


def _validate_calibration_configuration(configuration: SemanticCalibrationConfig) -> None:
    if configuration.candidate_limit is not None and configuration.candidate_limit <= 0:
        raise ValueError("candidate_limit must be positive")
    if configuration.deadline_ms is not None and configuration.deadline_ms <= 0:
        raise ValueError("deadline_ms must be positive")
    for name in ("review_threshold", "recall_target"):
        value = getattr(configuration, name)
        if value is not None and not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
    if configuration.review_volume_target is not None and configuration.review_volume_target < 0:
        raise ValueError("review_volume_target must be non-negative")


def _validate_runtime_measurements(
    *,
    latency_ms: float | None,
    peak_memory_bytes: int | None,
    latency_samples_ms: Iterable[float | int],
    peak_memory_samples_bytes: Iterable[float | int],
) -> None:
    if latency_ms is not None and (latency_ms < 0 or not math.isfinite(latency_ms)):
        raise ValueError("latency_ms must be a finite, non-negative number")
    if peak_memory_bytes is not None and peak_memory_bytes < 0:
        raise ValueError("peak_memory_bytes must be non-negative")
    if any(value < 0 or not math.isfinite(float(value)) for value in latency_samples_ms):
        raise ValueError("latency samples must be finite and non-negative")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in peak_memory_samples_bytes):
        raise ValueError("peak memory samples must be non-negative integers")


def _duplicate_observation_case_ids(observations: tuple[SemanticCandidateObservation, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for observation in observations:
        if observation.case_id in seen:
            duplicates.add(observation.case_id)
        seen.add(observation.case_id)
    return tuple(sorted(duplicates))
