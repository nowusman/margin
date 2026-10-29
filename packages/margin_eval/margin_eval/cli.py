from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from margin_eval.evaluation import EvaluationMode
from margin_eval.gold_corpus import GoldRetrievalExpectation, load_gold_corpus
from margin_eval.metrics import (
    RetrievalEvaluationInput,
    evaluate_retrieval,
    evaluation_result_errors,
    load_evaluation_results,
)
from margin_eval.parser_benchmark import benchmark_default_parsers, parser_benchmark_failures
from margin_eval.security import load_red_team_corpus, red_team_corpus_errors
from margin_eval.thresholds import ReleaseStage, threshold_failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="margin-evaluate")
    subparsers = parser.add_subparsers(dest="command")

    retrieval = subparsers.add_parser("retrieval", help="Run retrieval evaluation")
    retrieval.add_argument("--output", default=".margin/retrieval_evaluation_report.json")
    retrieval.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/fixtures/gold_corpus/manifest.json"),
        help="Canonical gold-corpus manifest that supplies retrieval expectations.",
    )
    retrieval.add_argument("--stage", choices=[stage.value for stage in ReleaseStage], default="mvp")
    retrieval.add_argument(
        "--results",
        type=Path,
        default=None,
        help="Optional JSON results from a real retrieval run over the canonical corpus.",
    )
    retrieval.add_argument(
        "--require-results",
        action="store_true",
        help=("Fail unless --results is supplied. Use for release gates that must measure live retrieval."),
    )

    corpus = subparsers.add_parser("corpus", help="Run gold-corpus evaluation")
    corpus.add_argument("--manifest", type=Path, default=Path("tests/fixtures/gold_corpus/manifest.json"))
    corpus.add_argument(
        "--mode", choices=[mode.value for mode in EvaluationMode], default=EvaluationMode.DETERMINISTIC.value
    )
    corpus.add_argument("--model-id", default=None)
    corpus.add_argument("--prompt-version", default=None)
    corpus.add_argument("--index-version", default=None)
    corpus.add_argument("--output-dir", type=Path, default=Path(".margin/evaluation_runs"))

    surfaces = subparsers.add_parser(
        "surfaces", help="Measure the graph, narrative, export and personal surfaces against the gold corpus"
    )
    surfaces.add_argument("--manifest", type=Path, default=Path("tests/fixtures/gold_corpus/manifest.json"))
    surfaces.add_argument("--stage", choices=[stage.value for stage in ReleaseStage], default="mvp")
    surfaces.add_argument("--output", default=".margin/surface_evaluation_report.json")

    args = parser.parse_args(argv)
    if args.command is None:
        retrieval.print_help()
        return 1
    if args.command == "retrieval":
        return _run_retrieval(retrieval, args)
    if args.command == "corpus":
        return _run_corpus(corpus, args)
    if args.command == "surfaces":
        return _run_surfaces(surfaces, args)
    return 1


def _run_surfaces(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Measure the surfaces over the gold corpus and report the gate."""

    from margin_eval.gold_corpus import load_gold_corpus
    from margin_eval.pipeline import run_evaluation_pipeline
    from margin_eval.surface_quality import evaluate_surface_quality
    from margin_eval.thresholds import surface_threshold_failures

    corpus = load_gold_corpus(args.manifest)
    result = run_evaluation_pipeline(corpus)
    factory = sessionmaker(bind=result.engine, expire_on_commit=False)
    with factory() as session:
        metrics = evaluate_surface_quality(session, corpus)
    failures = list(surface_threshold_failures(metrics, ReleaseStage(args.stage)))
    if metrics.graph_edges_expected == 0 and metrics.narrative_claims_expected == 0:
        failures.append("the corpus holds no extracted records, so the surfaces were not measured")
    payload = {
        "gold_corpus_version": corpus.version,
        "stage": args.stage,
        "surface_quality": metrics.as_dict(),
        "surface_gate_failures": list(failures),
    }
    _write_report(Path(args.output), payload)
    return 1 if failures else 0


def _run_retrieval(_parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    corpus = load_gold_corpus(args.manifest)
    expectations = corpus.retrieval_expectations()
    missing_results_failure = (
        ("live retrieval results are required but --results was not supplied",)
        if args.require_results and args.results is None
        else ()
    )
    if missing_results_failure:
        _write_report(
            Path(args.output),
            {
                "stage": args.stage,
                "evaluation_mode": "blocked_missing_results",
                "gold_corpus_version": corpus.version,
                "retrieval_expectation_count": len(expectations),
                "quality_gate_failures": list(missing_results_failure),
                "missing_results_failures": list(missing_results_failure),
            },
        )
        return 1
    evaluation_mode = "retrieval_results" if args.results else "oracle_baseline"
    results = load_evaluation_results(args.results) if args.results else _oracle_results(expectations)
    report = evaluate_retrieval(expectations, results)
    parser_rows = benchmark_default_parsers()
    parser_failures = parser_benchmark_failures(parser_rows)
    red_team_cases = load_red_team_corpus()
    red_team_failures = red_team_corpus_errors(red_team_cases)
    stage = ReleaseStage(args.stage)
    result_failures = evaluation_result_errors(expectations, results)
    metric_threshold_failures = threshold_failures(report, stage)
    quality_gate_failures = (
        *metric_threshold_failures,
        *missing_results_failure,
        *result_failures,
        *parser_failures,
        *red_team_failures,
    )

    payload = {
        "stage": stage.value,
        "evaluation_mode": evaluation_mode,
        "metric_warning": _metric_warning(evaluation_mode),
        "metrics": report.to_dict(),
        "threshold_failures": list(metric_threshold_failures),
        "quality_gate_failures": list(quality_gate_failures),
        "gold_corpus_version": corpus.version,
        "retrieval_expectation_count": len(expectations),
        "red_team_case_count": len(red_team_cases),
        "red_team_corpus_failures": list(red_team_failures),
        "parser_benchmarks": [row.to_dict() for row in parser_rows],
        "parser_benchmark_failures": list(parser_failures),
        "result_failures": list(result_failures),
        "missing_results_failures": list(missing_results_failure),
    }
    _write_report(Path(args.output), payload)
    return 1 if quality_gate_failures else 0


def _run_corpus(_parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from margin_eval.pipeline import run_evaluation_pipeline

    corpus = load_gold_corpus(args.manifest)
    result = run_evaluation_pipeline(
        corpus,
        evaluation_mode=EvaluationMode(args.mode),
        model_id=args.model_id,
        prompt_version=args.prompt_version,
        index_version=args.index_version,
        output_dir=args.output_dir,
    )
    print(f"wrote {result.run.run_id} (status={result.run.status})")
    return 0 if result.run.status == "passed" else 1


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {path}")


def _oracle_results(expectations: tuple[GoldRetrievalExpectation, ...]) -> tuple[RetrievalEvaluationInput, ...]:
    return tuple(
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


def _metric_warning(evaluation_mode: str) -> str | None:
    if evaluation_mode == "oracle_baseline":
        return (
            "No retrieval results file was supplied; metrics validate the gold-corpus "
            "schema and threshold machinery rather than live retriever quality."
        )
    return None


if __name__ == "__main__":
    raise SystemExit(main())
