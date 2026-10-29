from __future__ import annotations

import json

from sqlalchemy import text

from margin_eval.cli import main as evaluation_main
from margin_eval.gold_corpus import REQUIRED_DOCUMENT_CLASSES, GoldCorpus, load_gold_corpus
from margin_eval.metrics import (
    RetrievalEvaluationInput,
    answer_relevance,
    evaluate_retrieval,
    evaluation_result_errors,
    load_evaluation_results,
    unsupported_claim_rate,
)
from margin_eval.pipeline import run_evaluation_pipeline
from margin_eval.thresholds import ReleaseStage, threshold_failures


def test_the_evaluation_pipeline_extracts_the_corpus_it_ingests(tmp_path) -> None:
    """A gate that measures an un-extracted corpus measures nothing."""

    corpus = load_gold_corpus()
    fixture = next(fixture for fixture in corpus.fixtures if fixture.fixture_id == "adr_postgres")
    single = GoldCorpus(version=corpus.version, fixtures=(fixture,), evaluation_modes=corpus.evaluation_modes)

    result = run_evaluation_pipeline(single, output_dir=tmp_path)

    with result.engine.connect() as connection:
        revisions = connection.execute(text("SELECT COUNT(*) FROM idea_block_revisions")).scalar_one()
        elements = connection.execute(text("SELECT COUNT(*) FROM source_elements")).scalar_one()
        outcomes = connection.execute(text("SELECT COUNT(*) FROM source_element_outcomes")).scalar_one()

    assert elements > 0
    assert outcomes > 0
    assert revisions > 0


def test_gold_corpus_derives_retrieval_expectations() -> None:
    expectations = load_gold_corpus().retrieval_expectations()

    assert len(expectations) >= 20
    assert all(expectation.query and expectation.expected_idea_block_ids for expectation in expectations)
    assert all(expectation.scope["domain_ids"] for expectation in expectations)


def test_gold_document_corpus_is_grounded_and_covers_required_formats() -> None:
    corpus = load_gold_corpus()

    assert REQUIRED_DOCUMENT_CLASSES <= {fixture.document_class for fixture in corpus.fixtures}
    assert all(fixture.path.is_file() and fixture.expected_assertions for fixture in corpus.fixtures)
    versioned = [fixture for fixture in corpus.fixtures if fixture.version_group == "customer-platform"]
    assert [fixture.version for fixture in versioned] == [1, 2]


def test_gold_corpus_retains_rich_fixture_expectations() -> None:
    fixture = next(
        fixture for fixture in load_gold_corpus().fixtures if fixture.fixture_id == "mixed_ocr_native_scanned"
    )

    assert fixture.expected_pages == 3
    assert fixture.expected_elements[0]["element_id"] == "mixed-p1-el1"
    assert fixture.evidence_locations[1]["page_number"] == 2
    assert fixture.entity_outcomes[0]["outcome"] == "canonical_resolved"
    assert fixture.semantic_decisions[0]["decision"] == "distinct"
    assert fixture.governance_counts == {"adrs": 0, "risks": 1, "waivers": 0, "debt": 0, "backlog": 0}
    assert fixture.search_expectations is not None
    assert fixture.search_expectations["top_result_id"] == "mixed_ocr_native_scanned::mixed_ocr_scanned_p2"
    assert fixture.citations[0]["page"] == 2


def test_retrieval_metrics_and_thresholds_are_deterministic() -> None:
    expectations = load_gold_corpus().retrieval_expectations()
    results = tuple(
        RetrievalEvaluationInput(
            fixture_id=expectation.fixture_id,
            retrieved_evidence_ids=expectation.expected_idea_block_ids,
            cited_evidence_ids=expectation.expected_idea_block_ids,
            answer_markdown="\n".join(
                f"- Evidence for {expectation.query} [{idea_block_id}]"
                for idea_block_id in expectation.expected_idea_block_ids
            ),
        )
        for expectation in expectations
    )

    report = evaluate_retrieval(expectations, results, k=10)

    assert report.expectation_count == len(expectations)
    assert report.recall_at_k == 1
    assert report.precision_at_k == 1
    assert report.context_recall == report.recall_at_k
    assert report.context_precision == report.precision_at_k
    assert report.citation_coverage == 1
    assert report.unsupported_claim_rate == 0
    assert report.faithfulness == 1
    assert report.answer_relevance > 0
    assert threshold_failures(report, ReleaseStage.MVP) == ()


def test_unsupported_claim_rate_counts_uncited_material_lines() -> None:
    markdown = "- Supported [ib_1]\n- Unsupported generated claim"

    assert unsupported_claim_rate(markdown, {"ib_1"}) == 0.5


def test_answer_relevance_uses_query_answer_token_overlap() -> None:
    assert answer_relevance("Why choose PostgreSQL?", "PostgreSQL is the record [ib_1]") > 0
    assert answer_relevance("Why choose PostgreSQL?", "") == 0
    assert answer_relevance("Why choose PostgreSQL?", "[span_postgresql]") == 0


def test_unsupported_claim_rate_rejects_spoofed_citation_with_empty_evidence() -> None:
    assert unsupported_claim_rate("- Spoofed [ib_fake]", set()) == 1


def test_evaluation_uses_retrieved_evidence_as_citation_boundary() -> None:
    expectation = load_gold_corpus().retrieval_expectations()[0]
    result = RetrievalEvaluationInput(
        fixture_id=expectation.fixture_id,
        retrieved_evidence_ids=(),
        cited_evidence_ids=expectation.expected_idea_block_ids,
        answer_markdown=f"- Unsupported citation [{expectation.expected_idea_block_ids[0]}]",
    )

    report = evaluate_retrieval((expectation,), (result,))

    assert report.unsupported_claim_rate == 1


def test_missing_result_penalizes_retrieval_recall() -> None:
    expectation = load_gold_corpus().retrieval_expectations()[0]

    report = evaluate_retrieval((expectation,), ())

    assert report.recall_at_k == 0


def test_result_file_loader_accepts_corpus_retrieval_results(tmp_path) -> None:
    expectation = load_gold_corpus().retrieval_expectations()[0]
    path = tmp_path / "results.json"
    path.write_text(
        json.dumps(
            [
                {
                    "fixture_id": expectation.fixture_id,
                    "retrieved_evidence_ids": list(expectation.expected_idea_block_ids),
                    "cited_evidence_ids": list(expectation.expected_idea_block_ids),
                    "answer_markdown": f"- Supported [{expectation.expected_idea_block_ids[0]}]",
                }
            ]
        ),
        encoding="utf-8",
    )

    results = load_evaluation_results(path)

    assert results[0].fixture_id == expectation.fixture_id
    assert results[0].retrieved_evidence_ids == expectation.expected_idea_block_ids


def test_evaluation_result_errors_reject_unknown_and_duplicate_results() -> None:
    expectation = load_gold_corpus().retrieval_expectations()[0]
    results = (
        RetrievalEvaluationInput("unknown", (), (), ""),
        RetrievalEvaluationInput(expectation.fixture_id, (), (), ""),
        RetrievalEvaluationInput(expectation.fixture_id, (), (), ""),
    )

    errors = evaluation_result_errors((expectation,), results)

    assert any("unknown fixture_id" in error for error in errors)
    assert any("duplicate result" in error for error in errors)


def test_evaluation_command_can_require_live_results(tmp_path) -> None:
    output = tmp_path / "report.json"

    exit_code = evaluation_main(["retrieval", "--require-results", "--output", str(output)])

    assert exit_code == 1
    assert "live retrieval results are required" in output.read_text(encoding="utf-8")
