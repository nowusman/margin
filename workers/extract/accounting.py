from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from margin_db.extraction_models import ExtractionRun, SpanExtractionAttempt
from margin_db.ingestion_observability import log_source_observability_metrics
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.relationship_models import RelationshipProvenance
from margin_db.review_models import ReviewTask
from margin_db.source_models import (
    ConversionQualityReport,
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourceElementOutcome,
    SourcePage,
    SourcePageOutcome,
    UnmappedExtraction,
)
from margin_schema.extraction import ExtractedGovernanceRecordCandidate, ExtractedIdeaBlock
from margin_schema.ontology import ONTOLOGY_VERSION
from margin_schema.text_normalization import normalize_statement
from workers.extract.windows import ExtractionWindow, build_extraction_windows

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WindowExtractionResult:
    window: ExtractionWindow
    outcome: str
    extractor: str
    idea_blocks: tuple[ExtractedIdeaBlock, ...] = ()
    governance_records: tuple[ExtractedGovernanceRecordCandidate, ...] = ()
    model_id: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    attempt_count: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None
    residual_re_extraction: bool = False


def start_extraction_run(
    session: Session,
    *,
    ingestion_job_id: str | None = None,
    source_document_id: str,
    source_version_id: str,
    mode: str,
    extractor: str,
    model_id: str | None,
    prompt_version: str | None,
    prompt_hash: str | None,
    expected_window_count: int,
    metadata: dict[str, object] | None = None,
) -> ExtractionRun:
    run_id = _run_id(source_document_id, source_version_id, mode)
    run = ExtractionRun(
        id=run_id,
        ingestion_job_id=ingestion_job_id,
        source_document_id=source_document_id,
        source_version_id=source_version_id,
        mode=mode,
        status="running",
        extractor=extractor,
        model_id=model_id,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        expected_window_count=expected_window_count,
        processed_window_count=0,
        input_tokens=0,
        output_tokens=0,
        warnings=[],
        ontology_version=ONTOLOGY_VERSION,
        metadata_=dict(metadata or {}),
    )
    session.add(run)
    session.flush()
    return run


def finish_extraction_run(
    session: Session,
    *,
    run: ExtractionRun,
    source_document: SourceDocument,
    results: tuple[WindowExtractionResult, ...],
    expected_source_element_ids: set[str],
) -> None:
    if len(results) != run.expected_window_count:
        raise ValueError(
            f"extraction run expected {run.expected_window_count} windows but received {len(results)} results"
        )
    window_ids = {result.window.window_id for result in results}
    if len(window_ids) != len(results):
        raise ValueError("extraction run returned duplicate window results")
    processed_element_ids: set[str] = set()
    for result in results:
        for element_id in (result.window.source_element_id, *result.window.record_element_ids):
            processed_element_ids.add(element_id)
    missing_ids = expected_source_element_ids - processed_element_ids
    missing_elements = (
        list(session.scalars(select(SourceElement).where(SourceElement.id.in_(missing_ids))).all())
        if missing_ids
        else []
    )
    intentionally_ignored_kinds = {
        "caption",
        "figure_caption",
        "image",
        "image_crop",
        "table_caption",
        "table_cell",
        "table_header",
    }
    unaccounted_missing = sorted(
        element.id for element in missing_elements if element.kind.casefold() not in intentionally_ignored_kinds
    )
    if unaccounted_missing or len(missing_elements) != len(missing_ids):
        unexpected = sorted(processed_element_ids - expected_source_element_ids)
        missing = sorted(missing_ids)
        raise ValueError(f"extraction run element coverage mismatch; missing={missing}, unexpected={unexpected}")
    processed_element_ids.update(element.id for element in missing_elements)
    warnings: list[str] = []
    recorded_outcomes: list[str] = []
    for result in results:
        idea_block_ids = sorted(
            {persisted_id for block in result.idea_blocks for persisted_id in _persisted_idea_block_ids(session, block)}
        )
        entity_ids = (
            sorted(
                set(
                    session.scalars(
                        select(IdeaBlockEntity.entity_id).where(IdeaBlockEntity.idea_block_id.in_(idea_block_ids))
                    ).all()
                )
            )
            if idea_block_ids
            else []
        )
        relationship_ids = (
            sorted(
                set(
                    session.scalars(
                        select(RelationshipProvenance.relationship_id).where(
                            RelationshipProvenance.idea_block_id.in_(idea_block_ids)
                        )
                    ).all()
                )
            )
            if idea_block_ids
            else []
        )
        result_warnings = [*result.warnings]
        if result.error:
            result_warnings.append(result.error)
        effective_outcome = result.outcome
        if result.outcome == "extracted" and len(idea_block_ids) < len(result.idea_blocks):
            effective_outcome = "unsupported"
            result_warnings.append("one or more extracted outputs were not persisted as source-supported knowledge")
        recorded_outcomes.append(effective_outcome)
        warnings.extend(result_warnings)
        metadata = {
            "content_type": result.window.content_type,
            "window_strategy": result.window.strategy,
            "page_number": result.window.page_number,
            "section_path": result.window.section_path,
            "estimated_tokens": result.window.estimated_tokens,
            "residual_re_extraction": result.residual_re_extraction,
            "ontology_version": result.window.ontology_version,
            "candidate_entity_ids": [
                str(candidate["canonical_id"])
                for candidate in result.window.governed_candidates
                if candidate.get("canonical_id")
            ],
            "governance_record_candidate_count": len(result.governance_records),
        }
        if effective_outcome == "no_claim":
            metadata["no_claim_reason"] = "no_extractable_claims_found"
            _ensure_open_review_task(
                session,
                task_type="no_claim_window_triage",
                target_type="source_element",
                target_id=result.window.source_element_id,
                reason="no_claim_window_triage",
                priority="low",
            )
        recorded_elements = list(dict.fromkeys((result.window.source_element_id, *result.window.record_element_ids)))
        for record_index, element_id in enumerate(recorded_elements):
            attempt_id = _attempt_id(run.id, result.window.window_id, record_index, element_id)
            attempt = session.get(SpanExtractionAttempt, attempt_id)
            if attempt is None:
                attempt = SpanExtractionAttempt(
                    id=attempt_id,
                    extraction_run_id=run.id,
                    source_element_id=element_id,
                    window_id=result.window.window_id,
                    window_index=result.window.window_index,
                )
                session.add(attempt)
            attempt.outcome = effective_outcome
            attempt.extractor = result.extractor
            attempt.model_id = result.model_id
            attempt.prompt_version = result.prompt_version
            attempt.prompt_hash = result.prompt_hash
            attempt.attempt_count = result.attempt_count
            attempt.input_tokens = result.input_tokens
            attempt.output_tokens = result.output_tokens
            attempt.warnings = result_warnings
            attempt.output_idea_block_ids = idea_block_ids
            attempt.output_entity_ids = entity_ids
            attempt.output_relationship_ids = relationship_ids
            attempt.context_element_ids = list(result.window.context_element_ids)
            attempt.metadata_ = metadata

    session.flush()

    issue_outcomes = {"failed", "deferred", "unsupported"}
    has_issues = any(outcome in issue_outcomes for outcome in recorded_outcomes)
    run.processed_window_count = len(results)
    run.input_tokens = sum(result.input_tokens for result in results)
    run.output_tokens = sum(result.output_tokens for result in results)
    run.warnings = list(dict.fromkeys(warnings))
    run.status = "completed_with_issues" if has_issues else "completed"
    run.completed_at = datetime.now(UTC)
    source_document.status = "review_required" if has_issues else "knowledge_ready"
    accounted_elements, expected_elements = _record_source_element_outcomes(
        session, run=run, source_document=source_document
    )
    _record_source_page_outcomes(session, run=run)
    _update_conversion_quality_report(session, run=run)
    if accounted_elements != expected_elements:
        source_document.status = "review_required"
    source_document.metadata_ = {**(source_document.metadata_ or {}), "latest_extraction_run_id": run.id}
    session.flush()
    outcomes = Counter(recorded_outcomes)
    logger.info(
        "extraction_run_metrics %s",
        json.dumps(
            {
                "run_id": run.id,
                "source_document_id": run.source_document_id,
                "source_version_id": run.source_version_id,
                "mode": run.mode,
                "status": run.status,
                "expected_windows": run.expected_window_count,
                "processed_windows": run.processed_window_count,
                "outcomes": dict(sorted(outcomes.items())),
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "warning_count": len(run.warnings),
            },
            sort_keys=True,
        ),
        extra={"extraction_run_id": run.id, "extraction_metrics": True},
    )
    log_source_observability_metrics(
        session, source_document_id=run.source_document_id, event="extraction_run_completed"
    )


def create_source_element_triage_tasks(session: Session, *, source_version_id: str) -> int:
    """Turn unmapped / unsupported element outcomes into actionable review tasks."""
    created = 0
    outcomes = session.scalars(
        select(SourceElementOutcome)
        .join(SourceElement, SourceElement.id == SourceElementOutcome.source_element_id)
        .where(SourceElement.source_version_id == source_version_id, SourceElementOutcome.outcome == "quarantined")
    ).all()
    for outcome in outcomes:
        existing = session.scalar(
            select(ReviewTask).where(
                ReviewTask.task_type == "source_element_triage",
                ReviewTask.target_type == "source_element",
                ReviewTask.target_id == outcome.source_element_id,
                ReviewTask.status == "open",
            )
        )
        if existing is not None:
            continue
        session.add(
            ReviewTask(
                task_type="source_element_triage",
                target_type="source_element",
                target_id=outcome.source_element_id,
                reason=outcome.reason or f"element outcome: {outcome.outcome}",
                status="open",
                priority="medium",
            )
        )
        created += 1
    session.flush()
    return created


def _record_source_element_outcomes(
    session: Session, *, run: ExtractionRun, source_document: SourceDocument
) -> tuple[int, int]:
    elements = list(
        session.scalars(select(SourceElement).where(SourceElement.source_version_id == run.source_version_id)).all()
    )
    for element in elements:
        evidence_exists = session.scalar(
            select(ClaimEvidence.id)
            .where(ClaimEvidence.extraction_run_id == run.id, ClaimEvidence.source_element_id == element.id)
            .limit(1)
        )
        attempts = list(
            session.scalars(
                select(SpanExtractionAttempt)
                .where(
                    SpanExtractionAttempt.extraction_run_id == run.id,
                    SpanExtractionAttempt.source_element_id == element.id,
                )
                .order_by(SpanExtractionAttempt.window_index)
            ).all()
        )
        outcome: str | None = None
        reason: str | None = None
        verification_method: str | None = None
        verifier_version: str | None = None
        if evidence_exists is not None:
            outcome = "extracted"
        elif session.scalar(
            select(UnmappedExtraction.id)
            .where(
                UnmappedExtraction.extraction_run_id == run.id,
                UnmappedExtraction.source_element_id == element.id,
                UnmappedExtraction.status == "open",
            )
            .limit(1)
        ):
            outcome = "quarantined"
            reason = "supported knowledge could not map to the active ontology"
        elif (element.metadata_ or {}).get("quarantined"):
            outcome = "quarantined"
            reason = str((element.metadata_ or {}).get("error_message") or "visual processing was quarantined")
        elif (element.metadata_ or {}).get("summary_only"):
            outcome = "intentionally_ignored"
            reason = "table parent is reserved for evidence-backed whole-table summaries"
        elif element.kind.casefold() in {"table_header", "table_cell"}:
            outcome = "intentionally_ignored"
            reason = "structured table context is covered by its row evidence"
        elif any(attempt.outcome == "failed" for attempt in attempts):
            outcome = "failed"
            reason = "extraction retry policy exhausted"
        elif any(attempt.outcome == "unsupported" for attempt in attempts):
            outcome = "quarantined"
            reason = "configured extraction pipeline could not interpret the element"
        elif attempts and all(attempt.outcome in {"no_claim", "duplicate"} for attempt in attempts):
            outcome = "verified_no_claim"
            reason = "extraction produced no supported claim"
            verification_method = "deterministic_or_residual_extraction"
            verifier_version = run.prompt_version or run.extractor
        elif not attempts:
            outcome = "intentionally_ignored"
            reason = "element has no citable text or extraction window"

        if outcome is None:
            continue
        outcome_id = "element_outcome_" + sha256(f"{run.id}\0{element.id}".encode()).hexdigest()
        outcome_row = session.get(SourceElementOutcome, outcome_id)
        if outcome_row is None:
            outcome_row = SourceElementOutcome(id=outcome_id, extraction_run_id=run.id, source_element_id=element.id)
            session.add(outcome_row)
        outcome_row.outcome = outcome
        outcome_row.reason = reason
        outcome_row.extractor = run.extractor
        outcome_row.model_id = run.model_id
        outcome_row.prompt_version = run.prompt_version
        outcome_row.prompt_hash = run.prompt_hash
        outcome_row.verification_method = verification_method
        outcome_row.verifier_version = verifier_version
        outcome_row.attempt_metadata = {
            "attempt_ids": [attempt.id for attempt in attempts],
            "source_category": source_document.category_id,
            "language": (source_document.metadata_ or {}).get("source_language"),
            "section_type": element.kind,
            "extraction_strategy": sorted(
                {
                    str((attempt.metadata_ or {}).get("window_strategy"))
                    for attempt in attempts
                    if (attempt.metadata_ or {}).get("window_strategy")
                }
            ),
        }
        if outcome == "quarantined":
            session.add(
                ReviewTask(
                    task_type="source_element_outcome_review",
                    target_id=element.id,
                    target_type="source_element",
                    reason=reason or outcome,
                    status="open",
                    priority="high",
                )
            )
    session.flush()
    accounted = (
        session.scalar(
            select(func.count())
            .select_from(SourceElementOutcome)
            .where(SourceElementOutcome.extraction_run_id == run.id)
        )
        or 0
    )
    return accounted, len(elements)


def _update_conversion_quality_report(session: Session, *, run: ExtractionRun) -> None:
    report = session.scalar(
        select(ConversionQualityReport).where(ConversionQualityReport.source_version_id == run.source_version_id)
    )
    if report is None:
        return
    element_outcomes = list(
        session.scalars(select(SourceElementOutcome).where(SourceElementOutcome.extraction_run_id == run.id)).all()
    )
    page_outcomes = list(
        session.scalars(select(SourcePageOutcome).where(SourcePageOutcome.extraction_run_id == run.id)).all()
    )
    element_counts = Counter(outcome.outcome for outcome in element_outcomes)
    page_counts = Counter(outcome.outcome for outcome in page_outcomes)
    terminal_outcomes = {"extracted", "verified_no_claim", "intentionally_ignored"}
    status = (
        "complete"
        if element_outcomes
        and page_outcomes
        and all(outcome.outcome in terminal_outcomes for outcome in element_outcomes)
        and all(outcome.outcome in terminal_outcomes for outcome in page_outcomes)
        else "requires_review"
    )
    report.metadata_ = {
        **(report.metadata_ or {}),
        "accounting": {
            "extraction_run_id": run.id,
            "status": status,
            "element_outcomes": dict(sorted(element_counts.items())),
            "page_outcomes": dict(sorted(page_counts.items())),
            "element_count": len(element_outcomes),
            "page_count": len(page_outcomes),
        },
    }


def _record_source_page_outcomes(session: Session, *, run: ExtractionRun) -> None:
    pages = list(
        session.scalars(
            select(SourcePage)
            .where(SourcePage.source_version_id == run.source_version_id)
            .order_by(SourcePage.page_number)
        ).all()
    )
    ingest_outcomes = (
        {
            outcome.source_page_id: outcome
            for outcome in session.scalars(
                select(SourcePageOutcome).where(
                    SourcePageOutcome.extraction_run_id.is_(None),
                    SourcePageOutcome.source_page_id.in_([p.id for p in pages]),
                )
            ).all()
        }
        if pages
        else {}
    )
    element_outcomes = {
        outcome.source_element_id: outcome
        for outcome in session.scalars(
            select(SourceElementOutcome).where(SourceElementOutcome.extraction_run_id == run.id)
        ).all()
    }
    for page in pages:
        prior = ingest_outcomes.get(page.id)
        page_elements = list(
            session.scalars(
                select(SourceElement).where(
                    SourceElement.source_version_id == run.source_version_id, SourceElement.source_page_id == page.id
                )
            ).all()
        )
        page_outcome_id = "page_outcome_" + sha256(f"{run.id}\0{page.id}".encode()).hexdigest()
        if page.classification == "unsupported":
            outcome = "quarantined"
            reason = "configured pipeline could not interpret the source page"
        elif not page_elements:
            outcome = "intentionally_ignored"
            reason = "no content elements detected on page"
        else:
            page_element_outcomes = [
                element_outcomes[elem.id].outcome for elem in page_elements if elem.id in element_outcomes
            ]
            if len(page_element_outcomes) != len(page_elements):
                outcome = "failed"
                reason = "one or more page elements has no extraction outcome"
            elif "failed" in page_element_outcomes:
                outcome = "failed"
                reason = "one or more elements on page failed extraction"
            elif "quarantined" in page_element_outcomes:
                outcome = "quarantined"
                reason = "unmapped extractions quarantined on page"
            elif page_element_outcomes and all(
                value in {"verified_no_claim", "intentionally_ignored"} for value in page_element_outcomes
            ):
                outcome = "verified_no_claim"
                reason = "all page elements were verified without an extractable claim"
            else:
                outcome = "extracted"
                reason = None

        existing = session.get(SourcePageOutcome, page_outcome_id)
        if existing is not None:
            existing.outcome = outcome
            existing.reason = reason
            if prior is not None:
                existing.attempt_count = prior.attempt_count
                existing.processor = prior.processor
                existing.processor_version = prior.processor_version
                existing.idempotency_key = prior.idempotency_key
                existing.result = prior.result
        else:
            session.add(
                SourcePageOutcome(
                    id=page_outcome_id,
                    extraction_run_id=run.id,
                    source_page_id=page.id,
                    outcome=outcome,
                    reason=reason,
                    attempt_count=prior.attempt_count if prior is not None else 1,
                    processor=prior.processor if prior is not None else None,
                    processor_version=prior.processor_version if prior is not None else None,
                    idempotency_key=prior.idempotency_key if prior is not None else None,
                    result=prior.result if prior is not None else None,
                    metadata_={
                        **(prior.metadata_ if prior and isinstance(prior.metadata_, dict) else {}),
                        "extraction_run_id": run.id,
                    },
                )
            )
    session.flush()


def pass_verified_no_claim_audit(session: Session, *, outcome_id: str) -> None:
    outcome = session.get(SourceElementOutcome, outcome_id)
    if outcome is None or outcome.outcome != "verified_no_claim":
        raise ValueError("verified no-claim outcome not found")
    outcome.audit_status = "passed"
    task = session.scalar(
        select(ReviewTask).where(
            ReviewTask.task_type == "verified_no_claim_audit",
            ReviewTask.target_id == outcome.id,
            ReviewTask.status == "open",
        )
    )
    if task is not None:
        task.status = "closed"
        task.resolution = "No-claim audit passed."
        task.closed_at = datetime.now(UTC)
    session.flush()


def fail_extraction_run(run: ExtractionRun, source_document: SourceDocument, error: Exception) -> None:
    run.status = "failed"
    run.warnings = list(dict.fromkeys([*(run.warnings or []), str(error)]))
    run.completed_at = datetime.now(UTC)
    source_document.status = "failed"


def record_failed_extraction_run(
    session: Session,
    *,
    ingestion_job_id: str | None = None,
    source_document_id: str,
    mode: str,
    extractor: str,
    model_id: str | None,
    prompt_version: str | None,
    prompt_hash_value: str | None,
    max_window_tokens: int,
    neighbor_tokens: int,
    error: Exception,
) -> ExtractionRun | None:
    document = session.get(SourceDocument, source_document_id)
    version = session.scalar(
        select(SourceDocumentVersion)
        .where(SourceDocumentVersion.source_document_id == source_document_id, SourceDocumentVersion.status == "active")
        .order_by(SourceDocumentVersion.created_at.desc(), SourceDocumentVersion.id.desc())
        .limit(1)
    )
    if document is None or version is None:
        return None
    elements = list(
        session.scalars(
            select(SourceElement)
            .where(SourceElement.source_version_id == version.id)
            .order_by(SourceElement.ordinal, SourceElement.id)
        ).all()
    )
    windows = build_extraction_windows(
        document=document,
        version=version,
        elements=elements,
        max_tokens=max_window_tokens,
        neighbor_tokens=neighbor_tokens,
    )
    run = start_extraction_run(
        session,
        ingestion_job_id=ingestion_job_id,
        source_document_id=source_document_id,
        source_version_id=version.id,
        mode=mode,
        extractor=extractor,
        model_id=model_id,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash_value,
        expected_window_count=len(windows),
    )
    fail_extraction_run(run, document, error)
    _record_failed_element_outcomes(session, run=run, error=error)
    document.metadata_ = {**(document.metadata_ or {}), "latest_extraction_run_id": run.id}
    session.flush()
    return run


def _record_failed_element_outcomes(session: Session, *, run: ExtractionRun, error: Exception) -> None:
    elements = session.scalars(
        select(SourceElement).where(
            SourceElement.source_version_id == run.source_version_id, SourceElement.text.is_not(None)
        )
    ).all()
    for element in elements:
        if not (element.text or "").strip():
            continue
        outcome_id = "element_outcome_" + sha256(f"{run.id}\0{element.id}".encode()).hexdigest()
        outcome = session.get(SourceElementOutcome, outcome_id)
        if outcome is None:
            outcome = SourceElementOutcome(id=outcome_id, extraction_run_id=run.id, source_element_id=element.id)
            session.add(outcome)
        outcome.outcome = "failed"
        outcome.reason = str(error)
        outcome.extractor = run.extractor
        outcome.model_id = run.model_id
        outcome.prompt_version = run.prompt_version
        outcome.prompt_hash = run.prompt_hash
        outcome.attempt_metadata = {"retry_policy_exhausted": True}
    session.flush()


def _ensure_open_review_task(
    session: Session, *, task_type: str, target_type: str, target_id: str, reason: str, priority: str
) -> ReviewTask:
    existing = session.scalar(
        select(ReviewTask).where(
            ReviewTask.task_type == task_type,
            ReviewTask.target_type == target_type,
            ReviewTask.target_id == target_id,
            ReviewTask.status == "open",
        )
    )
    if existing is not None:
        return existing
    task = ReviewTask(
        task_type=task_type,
        target_id=target_id,
        target_type=target_type,
        reason=reason,
        status="open",
        priority=priority,
    )
    session.add(task)
    return task


def prompt_hash(prompt: str) -> str:
    return sha256(prompt.encode("utf-8")).hexdigest()


def _run_id(source_document_id: str, source_version_id: str, mode: str) -> str:
    timestamp = datetime.now(UTC).isoformat(timespec="microseconds")
    digest = sha256("\0".join((source_document_id, source_version_id, mode, timestamp)).encode("utf-8")).hexdigest()
    return f"run_{digest}"


def _attempt_id(run_id: str, window_id: str, record_index: int = 0, element_id: str = "") -> str:
    digest = sha256("\0".join((run_id, window_id, str(record_index), element_id)).encode("utf-8")).hexdigest()
    return f"attempt_{digest}"


def _persisted_idea_block_ids(session: Session, candidate: ExtractedIdeaBlock) -> tuple[str, ...]:
    evidence_element_ids = {evidence.source_element_id for evidence in candidate.evidence}
    return tuple(
        session.scalars(
            select(IdeaBlock.id)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .where(
                IdeaBlockRevision.normalized_statement == normalize_statement(candidate.statement),
                IdeaBlockRevision.type == candidate.type,
                IdeaBlockRevision.polarity == candidate.polarity,
                IdeaBlockRevision.qualifiers == candidate.qualifiers,
                IdeaBlockRevision.valid_from == candidate.valid_from,
                IdeaBlockRevision.valid_to == candidate.valid_to,
                ClaimEvidence.source_element_id.in_(evidence_element_ids),
            )
            .distinct()
        ).all()
    )
