from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from margin_db.audit_models import AuditEvent
from margin_db.embeddings import embedding_record_is_current, required_embedding_index_config
from margin_db.extraction_models import ExtractionRun, SpanExtractionAttempt
from margin_db.knowledge_models import ClaimEvidence, EmbeddingJob, IdeaBlockEntity
from margin_db.review_models import ReviewTask
from margin_db.semantic_resolution import SEMANTIC_RESOLUTION_TASK_TYPE
from margin_db.source_models import SourceDocumentVersion, SourceElement, SourceElementOutcome, UnmappedExtraction
from margin_embedding.text import embedding_text_for_source_element

logger = logging.getLogger("margin_db.source_ingestion")


def source_observability_metrics(session: Session, *, source_document_id: str) -> dict[str, Any]:
    """Build source metrics from persisted ingestion and governance records."""

    version = session.scalar(
        select(SourceDocumentVersion)
        .where(SourceDocumentVersion.source_document_id == source_document_id, SourceDocumentVersion.status == "active")
        .order_by(SourceDocumentVersion.created_at.desc(), SourceDocumentVersion.id.desc())
        .limit(1)
    )
    if version is None:
        version = session.scalar(
            select(SourceDocumentVersion)
            .where(SourceDocumentVersion.source_document_id == source_document_id)
            .order_by(SourceDocumentVersion.created_at.desc(), SourceDocumentVersion.id.desc())
            .limit(1)
        )
    if version is None:
        return {
            "native_elements": 0,
            "mapped_elements": 0,
            "unsupported_elements": 0,
            "previous_unsupported_elements": 0,
            "unsupported_element_growth": 0,
            "element_outcomes": {},
            "attempt_outcomes": {},
            "exact_claims": 0,
            "quarantined_facts": 0,
            "no_claim_audit_results": {"selected": 0, "passed": 0, "pending": 0, "not_selected": 0},
            "catalog_entity_actions": 0,
            "catalog_entity_retirements": 0,
            "semantic_decisions": {
                "equivalent": 0,
                "distinct": 0,
                "refinement": 0,
                "contradiction": 0,
                "supersession": 0,
                "review_tasks": 0,
            },
            "embedding_gate": {"status": "not_started"},
        }

    elements = tuple(session.scalars(select(SourceElement).where(SourceElement.source_version_id == version.id)).all())
    previous_version_id = session.scalar(
        select(SourceDocumentVersion.id)
        .where(
            SourceDocumentVersion.source_document_id == source_document_id,
            SourceDocumentVersion.id != version.id,
            SourceDocumentVersion.created_at <= version.created_at,
        )
        .order_by(SourceDocumentVersion.created_at.desc(), SourceDocumentVersion.id.desc())
        .limit(1)
    )
    element_ids = tuple(element.id for element in elements)
    # A source version has one current extraction run in normal operation. Use
    # the latest run explicitly so retries cannot inflate source metrics.
    latest_run = session.scalar(
        select(ExtractionRun)
        .where(ExtractionRun.source_version_id == version.id)
        .order_by(ExtractionRun.started_at.desc(), ExtractionRun.id.desc())
        .limit(1)
    )
    latest_run_id = latest_run.id if latest_run is not None else None
    outcomes = (
        tuple(
            session.scalars(
                select(SourceElementOutcome).where(
                    SourceElementOutcome.extraction_run_id == latest_run_id,
                    SourceElementOutcome.source_element_id.in_(element_ids),
                )
            ).all()
        )
        if latest_run_id is not None and element_ids
        else ()
    )
    previous_unsupported = (
        session.scalar(
            select(func.count(SourceElementOutcome.id))
            .join(ExtractionRun, ExtractionRun.id == SourceElementOutcome.extraction_run_id)
            .where(
                ExtractionRun.source_version_id == previous_version_id, SourceElementOutcome.outcome == "quarantined"
            )
        )
        if previous_version_id is not None
        else 0
    ) or 0
    unsupported = sum(outcome.outcome == "quarantined" for outcome in outcomes)
    outcome_counts: dict[str, int] = {}
    for outcome in outcomes:
        outcome_counts[outcome.outcome] = outcome_counts.get(outcome.outcome, 0) + 1
    attempt_counts: dict[str, int] = {}
    if latest_run_id is not None:
        for outcome in session.scalars(
            select(SpanExtractionAttempt.outcome).where(SpanExtractionAttempt.extraction_run_id == latest_run_id)
        ):
            attempt_counts[outcome] = attempt_counts.get(outcome, 0) + 1

    exact_claims = (
        session.scalar(
            select(func.count(distinct(ClaimEvidence.idea_block_id))).where(
                ClaimEvidence.source_element_id.in_(element_ids),
                ClaimEvidence.status == "active",
                func.length(func.trim(ClaimEvidence.source_quote)) > 0,
            )
        )
        or 0
    )
    source_idea_block_ids: tuple[str, ...] = tuple(
        session.scalars(
            select(distinct(ClaimEvidence.idea_block_id)).where(
                ClaimEvidence.source_element_id.in_(element_ids), ClaimEvidence.status == "active"
            )
        ).all()
    )
    quarantined_facts = (
        session.scalar(
            select(func.count(UnmappedExtraction.id)).where(
                UnmappedExtraction.source_version_id == version.id, UnmappedExtraction.status == "open"
            )
        )
        or 0
    )
    no_claim_selected = sum(
        outcome.outcome == "verified_no_claim" and outcome.audit_status == "selected" for outcome in outcomes
    )
    no_claim_passed = sum(
        outcome.outcome == "verified_no_claim" and outcome.audit_status == "passed" for outcome in outcomes
    )
    no_claim_not_selected = sum(
        outcome.outcome == "verified_no_claim" and outcome.audit_status == "not_selected" for outcome in outcomes
    )

    entity_ids = tuple(
        session.scalars(
            select(IdeaBlockEntity.entity_id).where(IdeaBlockEntity.idea_block_id.in_(source_idea_block_ids)).distinct()
        ).all()
    )
    entity_events = (
        session.scalars(
            select(AuditEvent).where(AuditEvent.target_type == "entity", AuditEvent.target_id.in_(entity_ids))
        ).all()
        if entity_ids
        else ()
    )
    source_idea_block_id_set = set(source_idea_block_ids)
    semantic_decisions = {
        "equivalent": 0,
        "distinct": 0,
        "refinement": 0,
        "contradiction": 0,
        "supersession": 0,
        "review_tasks": 0,
    }
    for event in session.scalars(select(AuditEvent).where(AuditEvent.action.like("semantic_review.%"))).all():
        before = event.before if isinstance(event.before, dict) else {}
        if not source_idea_block_id_set.intersection(before.get("idea_block_ids", ())):
            continue
        action = event.action.split(".", 1)[1]
        outcome = {
            "confirm_equivalent": "equivalent",
            "approve_distinct": "distinct",
            "mark_refinement": "refinement",
            "mark_contradiction": "contradiction",
            "mark_supersession": "supersession",
        }.get(action)
        if outcome is not None:
            semantic_decisions[outcome] += 1
    semantic_review_tasks = session.scalars(
        select(ReviewTask).where(
            ReviewTask.task_type == SEMANTIC_RESOLUTION_TASK_TYPE,
            ReviewTask.status.in_(("open", "assigned", "needs_evidence")),
        )
    ).all()
    semantic_decisions["review_tasks"] = sum(
        bool(source_idea_block_id_set.intersection((task.metadata_ or {}).get("idea_block_ids", ())))
        for task in semantic_review_tasks
    )

    embedding_config = required_embedding_index_config()
    eligible_elements = tuple(element for element in elements if embedding_text_for_source_element(element))
    missing_embeddings = tuple(
        element
        for element in eligible_elements
        if not embedding_record_is_current(
            session,
            target_type="source_element",
            target_id=element.id,
            model_id=embedding_config.model_id,
            index_version=embedding_config.index_version,
        )
    )
    failed_embedding_ids = (
        set(
            session.scalars(
                select(EmbeddingJob.id).where(
                    EmbeddingJob.target_type == "source_element",
                    EmbeddingJob.target_id.in_([element.id for element in eligible_elements]),
                    EmbeddingJob.model_id == embedding_config.model_id,
                    EmbeddingJob.index_version == embedding_config.index_version,
                    EmbeddingJob.status == "failed",
                )
            ).all()
        )
        if eligible_elements
        else set()
    )
    embedding_gate = {
        "status": (
            "not_started"
            if not eligible_elements
            else "complete" if not missing_embeddings else "failed" if failed_embedding_ids else "pending"
        ),
        "model_id": embedding_config.model_id,
        "index_version": embedding_config.index_version,
        "eligible": len(eligible_elements),
        "missing": len(missing_embeddings),
    }

    return {
        "native_elements": len(elements),
        "mapped_elements": sum(outcome.outcome in ("extracted", "verified_no_claim") for outcome in outcomes),
        "unsupported_elements": unsupported,
        "previous_unsupported_elements": int(previous_unsupported),
        "unsupported_element_growth": max(0, unsupported - int(previous_unsupported)),
        "element_outcomes": dict(sorted(outcome_counts.items())),
        "attempt_outcomes": dict(sorted(attempt_counts.items())),
        "exact_claims": int(exact_claims),
        "quarantined_facts": int(quarantined_facts),
        "no_claim_audit_results": {
            "selected": no_claim_selected + no_claim_passed,
            "passed": no_claim_passed,
            "pending": no_claim_selected,
            "not_selected": no_claim_not_selected,
        },
        "catalog_entity_actions": len(entity_events),
        "catalog_entity_retirements": sum(event.action == "entity_retire" for event in entity_events),
        "semantic_decisions": semantic_decisions,
        "embedding_gate": embedding_gate,
    }


def log_source_observability_metrics(
    session: Session, *, source_document_id: str, event: str = "source_observability"
) -> dict[str, Any]:
    """Emit a structured per-source metric event and return its payload."""

    metrics = source_observability_metrics(session, source_document_id=source_document_id)
    payload = {"event": event, "source_document_id": source_document_id, **metrics}
    logger.info(
        "source_observability %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        extra={"source_observability": payload},
    )
    return payload


def upload_request_payload(
    *,
    filename: str,
    media_type: str | None,
    size_bytes: int | None,
    domain_id: str | None,
    category: str | None,
    sensitivity: str,
    actor: str | None,
    auto_process: bool,
) -> dict[str, Any]:
    return {
        "filename": filename,
        "media_type": media_type,
        "size_bytes": size_bytes,
        "domain_id": domain_id,
        "category": category,
        "sensitivity": sensitivity,
        "actor": actor,
        "auto_process": auto_process,
    }


def upload_result_payload(result: Any) -> dict[str, Any]:
    return {
        "source_document_id": result.source_document.id,
        "ingestion_job_id": str(result.ingestion_job.id) if result.ingestion_job is not None else None,
        "status": result.source_document.status,
        "cached": result.cached,
    }


def process_job_request_payload(job: Any, document: Any, *, filename: str) -> dict[str, Any]:
    metadata = document.metadata_ if isinstance(document.metadata_, dict) else {}
    return {
        "job_id": str(job.id),
        "source_document_id": document.id,
        "source_version_id": job.source_version_id,
        "status": job.status,
        "filename": filename,
        "media_type": document.media_type,
        "size_bytes": document.size_bytes,
        "sha256": document.sha256,
        "domain_id": document.domain_id,
        "category": document.category,
        "sensitivity": document.sensitivity,
        "extraction_mode": metadata.get("extraction_mode"),
    }


def process_job_response_payload(job: Any) -> dict[str, Any]:
    quality_report = (job.metadata_ or {}).get("quality_report", {})
    return {
        "job_id": str(job.id),
        "source_document_id": job.source_document_id,
        "source_version_id": job.source_version_id,
        "status": job.status,
        "error_message": job.error_message,
        "quality_report": _quality_report_log_payload(quality_report),
    }


def log_ingestion_interaction(
    *,
    event: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any] | None,
    start: datetime,
    error: str | None = None,
) -> None:
    end = datetime.now(UTC)
    payload = {
        "event": event,
        "request": request_payload,
        "response": response_payload,
        "error": error,
        "start_timestamp": start.isoformat(),
        "end_timestamp": end.isoformat(),
        "duration_ms": round((end - start).total_seconds() * 1000, 3),
    }
    logger.info(
        "source_ingestion_interaction %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        extra={"source_ingestion_interaction": payload},
    )


def _quality_report_log_payload(quality_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": quality_report.get("status"),
        "converter": quality_report.get("converter"),
        "converter_version": quality_report.get("converter_version"),
        "failure_reason": quality_report.get("failure_reason"),
        "text_coverage": quality_report.get("text_coverage"),
        "table_coverage": quality_report.get("table_coverage"),
        "section_count": quality_report.get("section_count"),
        "span_count": quality_report.get("span_count"),
        "warnings": quality_report.get("warnings", []),
    }
