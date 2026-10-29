from __future__ import annotations

import json
from pathlib import Path

import pytest

from workers.extract.calibration import (
    SemanticCalibrationConfig,
    SemanticCandidateObservation,
    SemanticReplayCandidate,
    export_deterministic_semantic_observations,
    load_semantic_calibration_cases,
    map_semantic_replay_candidates,
    run_semantic_calibration,
    run_semantic_replay_calibration,
    write_semantic_calibration_report,
)


def test_semantic_calibration_is_unproven_without_runtime_observations() -> None:
    report = run_semantic_calibration()

    assert report.status == "unproven"
    assert report.candidate_recall is None
    assert report.candidate_precision is None
    assert report.protected_cases_pass is None
    assert report.semantic_auto_merge_violations == ()
    assert report.to_dict()["policy"] == {"semantic_auto_merge_enabled": False}


def test_deterministic_semantic_observation_export_is_partial_and_honest() -> None:
    observations = export_deterministic_semantic_observations()
    cases = load_semantic_calibration_cases()
    report = run_semantic_calibration(observations)

    assert observations
    assert len(observations) < len(cases)
    assert all(observation.action != "semantic_auto_merge" for observation in observations)
    assert report.status == "partial"
    assert report.candidate_recall is None
    assert report.candidate_precision is None
    assert report.matched_candidate_count == len(observations)
    assert report.false_negative_case_ids
    assert report.protected_cases_pass is None
    assert report.to_dict()["policy"] == {"semantic_auto_merge_enabled": False}
    assert any(observation.action == "exact_attach" for observation in observations)
    assert any(observation.suggested_outcome == "contradicts" for observation in observations)


def test_semantic_calibration_measures_gold_candidates_and_protected_cases() -> None:
    cases = load_semantic_calibration_cases()
    observations = tuple(
        SemanticCandidateObservation(
            case_id=case.case_id, domain_id=case.domain_id, suggested_outcome=case.expected_outcome
        )
        for case in cases
    )

    report = run_semantic_calibration(
        observations,
        embedding_version="test-index",
        classifier_version="test-classifier",
        latency_ms=12.5,
        peak_memory_bytes=1024,
    )

    assert report.status == "measured"
    assert report.runtime_evidence_status == "measured"
    assert report.candidate_recall == 1.0
    assert report.candidate_precision == 1.0
    assert report.suggested_outcome_precision == 1.0
    assert report.protected_cases_pass is True
    assert report.protected_case_unmeasured == ()
    assert report.semantic_auto_merge_violations == ()
    assert report.domain_reports[0].candidate_recall == 1.0


def test_semantic_calibration_rejects_protected_semantic_auto_merge(tmp_path: Path) -> None:
    protected_case = next(case for case in load_semantic_calibration_cases() if case.expected_outcome == "contradicts")
    report = run_semantic_calibration(
        [
            SemanticCandidateObservation(
                case_id=protected_case.case_id,
                domain_id=protected_case.domain_id,
                suggested_outcome=protected_case.expected_outcome,
                action="semantic_auto_merge",
            )
        ]
    )
    output_path = tmp_path / "semantic-calibration.json"

    write_semantic_calibration_report(report, output_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert report.protected_cases_pass is False
    assert report.protected_case_failures == (protected_case.case_id,)
    assert report.semantic_auto_merge_violations == (protected_case.case_id,)
    assert report.false_equivalence_case_ids == ()
    assert payload["policy"]["semantic_auto_merge_enabled"] is False
    assert payload["protected_case_outcomes"]["pass"] is False


def test_semantic_calibration_records_false_equivalence_suggestions() -> None:
    protected_case = next(case for case in load_semantic_calibration_cases() if case.expected_outcome == "distinct")

    report = run_semantic_calibration(
        [
            SemanticCandidateObservation(
                case_id=protected_case.case_id, domain_id=protected_case.domain_id, suggested_outcome="equivalent"
            )
        ]
    )

    assert report.false_equivalence_case_ids == (protected_case.case_id,)
    assert report.protected_case_failures == (protected_case.case_id,)


def test_semantic_calibration_rejects_duplicate_case_observations() -> None:
    case = load_semantic_calibration_cases()[0]
    observation = SemanticCandidateObservation(
        case_id=case.case_id, domain_id=case.domain_id, suggested_outcome=case.expected_outcome
    )

    with pytest.raises(ValueError, match="one observation per case"):
        run_semantic_calibration([observation, observation])


def test_semantic_replay_candidates_map_identity_and_retain_unknowns() -> None:
    cases = load_semantic_calibration_cases()
    candidate_case = next(case for case in cases if case.candidate_id)
    pair_case = next(case for case in cases if case.source_id and case.target_id)

    observations = map_semantic_replay_candidates(
        [
            SemanticReplayCandidate(
                domain_id=candidate_case.domain_id,
                candidate_id=candidate_case.candidate_id,
                fixture_id=candidate_case.fixture_id,
                suggested_outcome=candidate_case.expected_outcome,
            ),
            {
                "domain_id": pair_case.domain_id,
                "source_id": pair_case.source_id,
                "target_id": pair_case.target_id,
                "fixture_id": pair_case.fixture_id,
                "suggested_outcome": pair_case.expected_outcome,
            },
            {"domain_id": "platform", "candidate_id": "not-in-gold", "suggested_outcome": "distinct"},
        ],
        cases=cases,
    )

    assert observations[0].case_id == candidate_case.case_id
    assert observations[1].case_id == pair_case.case_id
    assert observations[2].case_id.startswith("unmapped::")


def test_semantic_replay_mapping_does_not_reverse_directional_pairs() -> None:
    pair_case = next(case for case in load_semantic_calibration_cases() if case.source_id and case.target_id)

    observations = map_semantic_replay_candidates(
        [
            SemanticReplayCandidate(
                domain_id=pair_case.domain_id,
                source_id=pair_case.target_id,
                target_id=pair_case.source_id,
                fixture_id=pair_case.fixture_id,
                suggested_outcome=pair_case.expected_outcome,
            )
        ],
        cases=load_semantic_calibration_cases(),
    )

    assert observations[0].case_id.startswith("unmapped::")


def test_semantic_replay_mapping_rejects_missing_identity() -> None:
    with pytest.raises(ValueError, match="must include case_id"):
        map_semantic_replay_candidates(
            [{"domain_id": "platform", "suggested_outcome": "distinct"}], cases=load_semantic_calibration_cases()
        )
    with pytest.raises(ValueError, match="must include case_id"):
        map_semantic_replay_candidates(
            [SemanticReplayCandidate(domain_id="platform", suggested_outcome="distinct")],
            cases=load_semantic_calibration_cases(),
        )


def test_semantic_calibration_captures_configuration_and_percentiles() -> None:
    cases = load_semantic_calibration_cases()
    observations = tuple(
        SemanticCandidateObservation(
            case_id=case.case_id,
            domain_id=case.domain_id,
            suggested_outcome=case.expected_outcome,
            latency_ms=float(index + 1),
            peak_memory_bytes=1000 + index,
        )
        for index, case in enumerate(cases)
    )
    configuration = SemanticCalibrationConfig(
        embedding_version="test-index",
        classifier_version="test-classifier",
        candidate_limit=20,
        deadline_ms=500,
        review_threshold=0.5,
        recall_target=1.0,
        review_volume_target=25,
        owner="quality@example.test",
        approval_date="2026-09-10",
    )

    report = run_semantic_calibration(observations, configuration=configuration)

    assert report.runtime_evidence_status == "measured"
    assert report.latency_p50_ms == 12.5
    assert report.latency_p95_ms == 22.85
    assert report.latency_p99_ms == 23.77
    assert report.peak_memory_bytes == 1023
    assert report.configuration.to_dict() == configuration.to_dict()
    assert all(domain.embedding_version == "test-index" for domain in report.domain_reports)
    assert all(domain.classifier_version == "test-classifier" for domain in report.domain_reports)
    assert all(domain.latency_p95_ms is not None for domain in report.domain_reports)


def test_semantic_replay_calibration_maps_runtime_payloads() -> None:
    case = load_semantic_calibration_cases()[0]

    report = run_semantic_replay_calibration(
        [
            {
                "domain_id": case.domain_id,
                "fixture_id": case.fixture_id,
                "candidate_id": case.candidate_id,
                "suggested_outcome": case.expected_outcome,
                "latency_ms": 4.5,
                "peak_memory_bytes": 2048,
            }
        ],
        configuration=SemanticCalibrationConfig(
            embedding_version="runtime-index", classifier_version="runtime-classifier"
        ),
    )

    assert report.status == "partial"
    assert report.runtime_evidence_status == "measured"
    assert report.matched_candidate_count == 1
    assert report.latency_p50_ms == 4.5
    assert report.peak_memory_bytes == 2048
