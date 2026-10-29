from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from margin_eval.gold_corpus import GoldRetrievalExpectation
from margin_schema.text_normalization import tokenize_keywords


@dataclass(frozen=True)
class RetrievalEvaluationInput:
    fixture_id: str
    retrieved_evidence_ids: tuple[str, ...]
    cited_evidence_ids: tuple[str, ...]
    answer_markdown: str


def load_evaluation_results(path: Path) -> tuple[RetrievalEvaluationInput, ...]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("evaluation results must be a JSON list")
    return tuple(_result_from_dict(row) for row in rows)


def evaluation_result_errors(
    expectations: tuple[GoldRetrievalExpectation, ...], results: tuple[RetrievalEvaluationInput, ...]
) -> tuple[str, ...]:
    fixture_ids = {expectation.fixture_id for expectation in expectations}
    seen: set[str] = set()
    errors: list[str] = []
    for result in results:
        if result.fixture_id in seen:
            errors.append(f"duplicate result for fixture_id {result.fixture_id}")
        seen.add(result.fixture_id)
        if result.fixture_id not in fixture_ids:
            errors.append(f"result references unknown fixture_id {result.fixture_id}")
    return tuple(errors)


@dataclass(frozen=True)
class EvaluationReport:
    expectation_count: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    context_recall: float
    context_precision: float
    citation_coverage: float
    unsupported_claim_rate: float
    faithfulness: float
    answer_relevance: float
    exact_id_lookup_success: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_retrieval(
    expectations: tuple[GoldRetrievalExpectation, ...], results: tuple[RetrievalEvaluationInput, ...], *, k: int = 10
) -> EvaluationReport:
    result_by_id = {result.fixture_id: result for result in results}
    if not expectations:
        raise ValueError("at least one gold-corpus retrieval expectation is required")

    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    citation_coverages: list[float] = []
    unsupported_rates: list[float] = []
    answer_relevances: list[float] = []
    exact_successes: list[float] = []

    for expectation in expectations:
        result = result_by_id.get(expectation.fixture_id) or RetrievalEvaluationInput(
            fixture_id=expectation.fixture_id, retrieved_evidence_ids=(), cited_evidence_ids=(), answer_markdown=""
        )
        expected = set(expectation.expected_idea_block_ids)
        retrieved = tuple(result.retrieved_evidence_ids[:k])
        retrieved_expected = expected & set(retrieved)
        recalls.append(len(retrieved_expected) / len(expected))
        precisions.append(len(retrieved_expected) / max(len(retrieved), 1))
        reciprocal_ranks.append(_reciprocal_rank(retrieved, expected))

        cited = set(result.cited_evidence_ids) | citations_from_markdown(result.answer_markdown)
        citation_coverages.append(len(expected & cited) / len(expected))
        unsupported_rates.append(unsupported_claim_rate(result.answer_markdown, set(result.retrieved_evidence_ids)))
        answer_relevances.append(answer_relevance(expectation.query, result.answer_markdown))
        exact_successes.append(float(bool(expected & (set(retrieved) | cited))))

    recall_at_k = _mean(recalls)
    precision_at_k = _mean(precisions)
    unsupported_claim_rate_value = _mean(unsupported_rates)
    return EvaluationReport(
        expectation_count=len(expectations),
        recall_at_k=recall_at_k,
        precision_at_k=precision_at_k,
        mrr=_mean(reciprocal_ranks),
        context_recall=recall_at_k,
        context_precision=precision_at_k,
        citation_coverage=_mean(citation_coverages),
        unsupported_claim_rate=unsupported_claim_rate_value,
        faithfulness=max(0.0, 1.0 - unsupported_claim_rate_value),
        answer_relevance=_mean(answer_relevances),
        exact_id_lookup_success=_mean(exact_successes) if exact_successes else 1.0,
    )


def citations_from_markdown(markdown: str) -> set[str]:
    return set(re.findall(r"\[([A-Za-z0-9_.:-]+)\]", markdown))


def unsupported_claim_rate(markdown: str, known_citations: set[str] | None = None) -> float:
    known = citations_from_markdown(markdown) if known_citations is None else known_citations
    claim_lines = 0
    unsupported = 0
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.lower().startswith("query:"):
            continue
        claim_lines += 1
        line_citations = citations_from_markdown(line)
        if not line_citations or not line_citations <= known:
            unsupported += 1
    if claim_lines == 0:
        return 0.0
    return unsupported / claim_lines


def answer_relevance(question: str, answer_markdown: str) -> float:
    answer_tokens = _content_tokens(answer_markdown)
    question_tokens = _content_tokens(question)
    if not answer_tokens:
        return 0.0
    if not question_tokens:
        return 1.0
    return len(question_tokens & answer_tokens) / len(question_tokens)


def _reciprocal_rank(retrieved: tuple[str, ...], expected: set[str]) -> float:
    for index, evidence_id in enumerate(retrieved, start=1):
        if evidence_id in expected:
            return 1 / index
    return 0.0


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _content_tokens(value: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "by",
        "for",
        "in",
        "is",
        "of",
        "or",
        "the",
        "to",
        "what",
        "where",
        "which",
        "why",
    }
    without_evidence_ids = re.sub(r"\b(?:ib|span)_[A-Za-z0-9_.:-]+", " ", value)
    return set(tokenize_keywords(without_evidence_ids, stopwords=frozenset(stopwords)))


def _result_from_dict(row: object) -> RetrievalEvaluationInput:
    if not isinstance(row, dict):
        raise ValueError("evaluation result entries must be objects")
    return RetrievalEvaluationInput(
        fixture_id=_required_string(row, "fixture_id"),
        retrieved_evidence_ids=_string_tuple(row.get("retrieved_evidence_ids")),
        cited_evidence_ids=_string_tuple(row.get("cited_evidence_ids")),
        answer_markdown=str(row.get("answer_markdown") or ""),
    )


def _required_string(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"evaluation result missing required string field: {key}")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(item for item in value if isinstance(item, str))
    raise ValueError("evidence id fields must be lists of strings")
