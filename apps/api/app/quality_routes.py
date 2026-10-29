from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authz import has_permission, require_any_domain_permission
from app.source_routes import get_session_factory
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock
from margin_db.relationship_models import RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_eval.evaluation import EvaluationMode, list_runs, load_run, run_evaluation
from margin_eval.gold_corpus import load_gold_corpus

router = APIRouter(prefix="/api/evaluations", tags=["quality"])

RUNS_DIR = Path(".margin/evaluation_runs")
DEFAULT_MANIFEST = Path("tests/fixtures/gold_corpus/manifest.json")


class EvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_path: str | None = Field(
        default=None, description="Path to the gold corpus manifest. Defaults to the repository gold manifest."
    )
    evaluation_mode: str = Field(
        default=EvaluationMode.DETERMINISTIC.value,
        description="deterministic (test precomputed) or operational (active configured model).",
    )
    model_id: str | None = None
    prompt_version: str | None = None
    index_version: str | None = None
    output_dir: str | None = None


@router.get("/runs")
def evaluation_runs(request: Request) -> dict[str, object]:
    _require_quality_read(request)
    runs = list_runs(RUNS_DIR)
    return {"runs": [_run_summary(load_run(run_id, RUNS_DIR)) for run_id in runs if load_run(run_id, RUNS_DIR)]}


@router.get("/runs/{run_id}")
def evaluation_run(run_id: str, request: Request) -> dict[str, object]:
    _require_quality_read(request)
    run = load_run(run_id, RUNS_DIR)
    if run is None:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    with get_session_factory(request)() as session:
        return {"run": _run_detail(run, session=session, permissions=request.state.permissions)}


@router.post("/runs")
def start_evaluation_run(body: EvaluationRunRequest, request: Request) -> dict[str, object]:
    _require_quality_read(request)
    manifest_path = Path(body.corpus_path) if body.corpus_path else DEFAULT_MANIFEST
    if not manifest_path.is_file():
        raise HTTPException(status_code=400, detail=f"gold corpus manifest does not exist: {manifest_path}")
    if body.evaluation_mode not in {mode.value for mode in EvaluationMode}:
        raise HTTPException(status_code=400, detail=f"unsupported evaluation_mode: {body.evaluation_mode!r}")
    output_dir = Path(body.output_dir) if body.output_dir else RUNS_DIR
    corpus = load_gold_corpus(manifest_path)
    with get_session_factory(request)() as session:
        run = _execute_evaluation(
            session,
            corpus,
            evaluation_mode=EvaluationMode(body.evaluation_mode),
            model_id=body.model_id,
            prompt_version=body.prompt_version,
            index_version=body.index_version,
            output_dir=output_dir,
        )
        return {"run": _run_detail(run, session=session, permissions=request.state.permissions)}


def _execute_evaluation(
    session: Session,
    corpus: Any,
    *,
    evaluation_mode: EvaluationMode,
    model_id: str | None,
    prompt_version: str | None,
    index_version: str | None,
    output_dir: Path,
) -> Any:
    return run_evaluation(
        session,
        corpus,
        evaluation_mode=evaluation_mode,
        model_id=model_id,
        prompt_version=prompt_version,
        index_version=index_version,
        output_dir=output_dir,
    )


def _require_quality_read(request: Request) -> None:
    require_any_domain_permission(
        permissions=request.state.permissions, action="read", detail="missing read access for evaluation runs"
    )


def _run_summary(run: Any) -> dict[str, object]:
    return {
        "id": run.run_id,
        "status": run.status,
        "corpus_version": run.corpus_version,
        "evaluation_mode": run.evaluation_mode,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat(),
        "metric_warning": (
            "operational mode requires the active configured extraction model"
            if run.evaluation_mode == EvaluationMode.OPERATIONAL.value and run.active_extraction_model_id is None
            else None
        ),
        "active_extraction_model_id": run.active_extraction_model_id,
    }


def _run_detail(run: Any, *, session: Session | None = None, permissions: Any = None) -> dict[str, object]:
    summary = _run_summary(run)
    summary["metrics"] = run.metrics.to_dict()
    summary["gate_failures"] = list(run.gate_failures)
    summary["fixture_count"] = len(run.fixture_results)
    summary["relationship_consistency_findings"] = list(run.relationship_consistency_findings)
    summary["false_positive_evidence"] = [
        {
            "fixture_id": result.fixture_id,
            "claim_false_positives": [
                match.expected_id for match in result.claim_matches if match.kind.value == "false_positive"
            ],
            "relationship_false_positives": [
                match.expected_id for match in result.relationship_matches if match.kind.value == "false_positive"
            ],
            "field_false_positives": [
                match.expected_id for match in result.field_matches if match.kind.value == "false_positive"
            ],
            "failed_element_count": result.failed_element_count,
            "deferred_element_count": result.deferred_element_count,
            "failed_attempt_count": result.failed_attempt_count,
            "deferred_attempt_count": result.deferred_attempt_count,
            "unbacked_claim_count": result.unbacked_claim_count,
            "page_reconciliation_missing": list(result.page_reconciliation_missing),
            "expected_pages_total": result.expected_pages_total,
            "expected_pages_reconciled": result.expected_pages_reconciled,
            "verified_claims_with_evidence": result.verified_claims_with_evidence,
            "verified_relationships_with_evidence": result.verified_relationships_with_evidence,
            "inference_origin_candidate_ids": list(result.inference_origin_candidate_ids),
            "ghost_record_ids": list(result.ghost_record_ids),
            "ambiguous_match_count": result.ambiguous_match_count,
        }
        for result in run.fixture_results
    ]
    summary["false_negative_evidence"] = [
        {
            "fixture_id": result.fixture_id,
            "claim_false_negatives": [
                match.expected_id for match in result.claim_matches if match.kind.value == "false_negative"
            ],
            "field_false_negatives": [
                match.expected_id for match in result.field_matches if match.kind.value == "false_negative"
            ],
            "entity_false_negatives": [
                match.expected_id for match in result.entity_matches if match.kind.value == "false_negative"
            ],
            "relationship_false_negatives": [
                match.expected_id for match in result.relationship_matches if match.kind.value == "false_negative"
            ],
            "no_claim_false_negatives": [
                match.expected_id for match in result.no_claim_matches if match.kind.value == "false_negative"
            ],
        }
        for result in run.fixture_results
    ]
    summary["false_positives"] = _evaluation_issues(
        run, session=session, permissions=permissions, kind="false_positive"
    )
    summary["false_negatives"] = _evaluation_issues(
        run, session=session, permissions=permissions, kind="false_negative"
    )
    summary["true_positive_evidence"] = [
        {
            "fixture_id": result.fixture_id,
            "claim_true_positives": [
                {
                    "expected_id": match.expected_id,
                    "actual_ids": list(match.actual_ids),
                    "evidence_ids": list(match.evidence_ids),
                }
                for match in result.claim_matches
                if match.kind.value == "true_positive"
            ],
            "relationship_true_positives": [
                {
                    "expected_id": match.expected_id,
                    "actual_ids": list(match.actual_ids),
                    "evidence_ids": list(match.evidence_ids),
                }
                for match in result.relationship_matches
                if match.kind.value == "true_positive"
            ],
            "entity_true_positives": [
                {
                    "expected_id": match.expected_id,
                    "actual_ids": list(match.actual_ids),
                    "evidence_ids": list(match.evidence_ids),
                }
                for match in result.entity_matches
                if match.kind.value == "true_positive"
            ],
            "no_claim_true_positives": [
                {
                    "expected_id": match.expected_id,
                    "actual_ids": list(match.actual_ids),
                    "evidence_ids": list(match.evidence_ids),
                }
                for match in result.no_claim_matches
                if match.kind.value == "true_positive"
            ],
        }
        for result in run.fixture_results
    ]
    return summary


def _evaluation_issues(run: Any, *, session: Session | None, permissions: Any, kind: str) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for result in run.fixture_results:
        for match_group in (
            result.claim_matches,
            result.field_matches,
            result.entity_matches,
            result.relationship_matches,
            result.no_claim_matches,
        ):
            for match in match_group:
                if match.kind.value != kind:
                    continue
                links = _source_links(session, result.source_document_id, match, permissions=permissions)
                issues.append(
                    {
                        "fixture_id": result.fixture_id,
                        "expected_id": match.expected_id,
                        "actual_ids": list(match.actual_ids),
                        "evidence_ids": list(match.evidence_ids),
                        "source_links": links,
                    }
                )
    return issues


def _source_links(
    session: Session | None, source_document_id: str | None, match: Any, *, permissions: Any
) -> list[dict[str, object]]:
    links: list[dict[str, object]] = []
    if source_document_id and _can_read_source(session, source_document_id, permissions):
        links.append(
            {
                "source_document_id": source_document_id,
                "source_element_id": None,
                "quote": None,
                "url": f"/sources/{source_document_id}",
            }
        )
    if session is None:
        return links
    evidence_ids = tuple(dict.fromkeys((*match.evidence_ids, *match.actual_ids)))
    for identifier in evidence_ids:
        evidence = session.get(ClaimEvidence, identifier)
        provenance = session.get(RelationshipProvenance, identifier)
        if evidence is None and provenance is None:
            block = session.get(IdeaBlock, identifier)
            if block is not None:
                evidence = session.scalar(
                    select(ClaimEvidence)
                    .where(ClaimEvidence.idea_block_id == block.id, ClaimEvidence.status == "active")
                    .order_by(ClaimEvidence.created_at, ClaimEvidence.id)
                    .limit(1)
                )
        element_id = (
            evidence.source_element_id if evidence is not None else provenance.source_element_id if provenance else None
        )
        quote = evidence.source_quote if evidence is not None else provenance.source_quote if provenance else None
        if element_id is None:
            continue
        element = session.get(SourceElement, element_id)
        if element is None:
            continue
        version = session.get(SourceDocumentVersion, element.source_version_id)
        if version is None:
            continue
        if not _can_read_source(session, version.source_document_id, permissions):
            continue
        link = {
            "source_document_id": version.source_document_id,
            "source_element_id": element.id,
            "quote": quote or element.text,
            "page": element.page_number,
            "url": f"/sources/{version.source_document_id}#{element.id}",
        }
        if link not in links:
            links.append(link)
    return links


def _can_read_source(session: Session | None, source_document_id: str, permissions: Any) -> bool:
    if session is None or permissions is None:
        return False
    document = session.get(SourceDocument, source_document_id)
    if document is None:
        return False
    return has_permission(
        permissions=permissions,
        domain_id=document.domain_id,
        category=document.category,
        sensitivity=document.sensitivity,
        action="read",
    )
