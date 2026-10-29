from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import distinct, false, func, select
from sqlalchemy.orm import Session

from margin_db.entity_models import EntityMention, EntityResolutionDecision
from margin_db.extraction_models import ExtractionRun, SpanExtractionAttempt
from margin_db.ingestion_observability import source_observability_metrics
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
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


class IngestionReceiptError(ValueError):
    """Raised when an ingestion receipt cannot be built."""


def build_ingestion_receipt(session: Session, *, source_document_id: str) -> dict[str, object]:
    document = session.get(SourceDocument, source_document_id)
    if document is None:
        raise IngestionReceiptError(f"source document not found: {source_document_id}")
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
        run = session.scalar(
            select(ExtractionRun)
            .where(ExtractionRun.source_document_id == source_document_id)
            .order_by(ExtractionRun.started_at.desc(), ExtractionRun.id.desc())
            .limit(1)
        )
        return {
            "source_document_id": document.id,
            "source_version_id": None,
            "source_status": document.status,
            "conversion": {"original": {}, "converted": {}, "warnings": []},
            "index": {"pages": 0, "elements": 0, "element_types": {}},
            "operational": {
                "run_id": run.id if run is not None else None,
                "status": run.status if run is not None else "not_started",
                "mode": run.mode if run is not None else None,
                "expected_windows": 0,
                "processed_windows": 0,
                "terminal_window_coverage": False,
                "attempted_elements": 0,
                "no_claim_elements": 0,
                "failed_elements": 0,
                "deferred_elements": 0,
                "unsupported_elements": 0,
                "outcomes": {},
                "input_tokens": 0,
                "output_tokens": 0,
                "warnings": [],
            },
            "knowledge": {},
            "observability": source_observability_metrics(session, source_document_id=source_document_id),
            "unresolved_elements": [],
            "completeness": {
                "operational": {"status": "requires_review"},
                "source": {"status": "requires_review"},
                "extraction": {"status": "requires_review"},
                "publication": {"status": "requires_review"},
            },
            "overall_status": "failed" if document.status == "failed" else "requires_review",
        }

    elements = tuple(
        session.scalars(
            select(SourceElement)
            .where(SourceElement.source_version_id == version.id)
            .order_by(SourceElement.ordinal, SourceElement.id)
        ).all()
    )
    element_ids = tuple(element.id for element in elements)
    citable_element_ids = tuple(element.id for element in elements if (element.text or "").strip())
    idea_blocks = tuple(
        session.scalars(
            select(IdeaBlock)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .where(ClaimEvidence.source_element_id.in_(element_ids))
            .distinct()
            if element_ids
            else select(IdeaBlock).where(false())
        ).all()
    )
    idea_block_ids = tuple(block.id for block in idea_blocks)
    entity_ids = tuple(
        session.scalars(
            select(distinct(IdeaBlockEntity.entity_id)).where(IdeaBlockEntity.idea_block_id.in_(idea_block_ids))
            if idea_block_ids
            else select(IdeaBlockEntity.entity_id).where(false())
        ).all()
    )
    relationship_count = _relationship_count(session, idea_block_ids)
    run = session.scalar(
        select(ExtractionRun)
        .where(ExtractionRun.source_version_id == version.id)
        .order_by(ExtractionRun.started_at.desc(), ExtractionRun.id.desc())
        .limit(1)
    )
    attempts = (
        tuple(
            session.scalars(
                select(SpanExtractionAttempt)
                .where(SpanExtractionAttempt.extraction_run_id == run.id)
                .order_by(SpanExtractionAttempt.window_index)
            ).all()
        )
        if run is not None
        else ()
    )
    attempt_counts = Counter(attempt.outcome for attempt in attempts)
    outcomes_by_element: dict[str, set[str]] = {}
    for attempt in attempts:
        outcomes_by_element.setdefault(attempt.source_element_id, set()).add(attempt.outcome)
    quality = session.scalar(
        select(ConversionQualityReport).where(ConversionQualityReport.source_version_id == version.id)
    )
    original_counts = _original_counts(document, version, quality)
    converted_counts = _converted_counts(version, elements)
    unresolved_entities = _unresolved_entity_count(session, idea_block_ids)
    terminal = bool(
        run is not None
        and run.status in {"completed", "completed_with_issues"}
        and run.expected_window_count == run.processed_window_count
        and set(outcomes_by_element) == set(citable_element_ids)
    )
    element_counts = Counter(element.kind for element in elements)
    knowledge_elements = tuple(element for element in elements if (element.text or "").strip())
    outcomes = (
        tuple(
            session.scalars(
                select(SourceElementOutcome)
                .where(SourceElementOutcome.extraction_run_id == run.id)
                .order_by(SourceElementOutcome.source_element_id)
            ).all()
        )
        if run is not None
        else ()
    )
    outcomes_by_element_model = {outcome.source_element_id: outcome for outcome in outcomes}
    page_outcomes = (
        tuple(
            session.scalars(
                select(SourcePageOutcome)
                .where(SourcePageOutcome.extraction_run_id == run.id)
                .order_by(SourcePageOutcome.source_page_id)
            ).all()
        )
        if run is not None
        else ()
    )
    pages = tuple(
        session.scalars(
            select(SourcePage).where(SourcePage.source_version_id == version.id).order_by(SourcePage.page_number)
        ).all()
    )
    unmapped_count = (
        session.scalar(
            select(func.count())
            .select_from(UnmappedExtraction)
            .where(UnmappedExtraction.source_version_id == version.id, UnmappedExtraction.status == "open")
        )
        or 0
    )
    completeness = _completeness_layers(
        run=run,
        attempts=attempts,
        pages=pages,
        page_outcomes=page_outcomes,
        expected_page_count=original_counts["pages"],
        elements=elements,
        knowledge_elements=knowledge_elements,
        outcomes=outcomes,
        outcomes_by_element=outcomes_by_element_model,
        idea_blocks=idea_blocks,
    )
    unresolved_elements = _unresolved_source_elements(
        session, elements=elements, outcomes_by_element=outcomes_by_element_model, attempts=attempts
    )

    return {
        "source_document_id": document.id,
        "source_version_id": version.id,
        "source_status": document.status,
        "conversion": {
            "original": original_counts,
            "converted": converted_counts,
            "warnings": list(quality.warnings or []) if quality is not None else [],
        },
        "index": {
            "pages": len(pages),
            "elements": len(elements),
            "element_types": dict(sorted(element_counts.items())),
        },
        "operational": {
            "run_id": run.id if run is not None else None,
            "status": run.status if run is not None else "not_started",
            "mode": run.mode if run is not None else None,
            "expected_windows": run.expected_window_count if run is not None else 0,
            "processed_windows": run.processed_window_count if run is not None else 0,
            "terminal_window_coverage": terminal,
            "attempted_elements": len(outcomes_by_element),
            "no_claim_elements": sum(
                outcomes_set <= {"no_claim", "duplicate"} for outcomes_set in outcomes_by_element.values()
            ),
            "failed_elements": sum("failed" in outcomes_set for outcomes_set in outcomes_by_element.values()),
            "deferred_elements": sum("deferred" in outcomes_set for outcomes_set in outcomes_by_element.values()),
            "unsupported_elements": sum("unsupported" in outcomes_set for outcomes_set in outcomes_by_element.values()),
            "outcomes": {outcome: attempt_counts[outcome] for outcome in _OUTCOMES},
            "input_tokens": run.input_tokens if run is not None else 0,
            "output_tokens": run.output_tokens if run is not None else 0,
            "warnings": list(run.warnings or []) if run is not None else [],
        },
        "knowledge": {
            "candidate_claims": sum(block.current_revision.publication_status == "candidate" for block in idea_blocks),
            "verified_claims": sum(block.current_revision.publication_status == "verified" for block in idea_blocks),
            "other_claims": sum(
                block.current_revision.publication_status not in {"candidate", "verified"} for block in idea_blocks
            ),
            "entities": len(entity_ids),
            "explicit_relationships": relationship_count,
            "unresolved_entities": unresolved_entities,
            "quarantined_extractions": unmapped_count,
        },
        "observability": source_observability_metrics(session, source_document_id=source_document_id),
        "unresolved_elements": unresolved_elements,
        "completeness": completeness,
        "overall_status": _overall_status(completeness),
    }


def _completeness_layers(
    *,
    run: ExtractionRun | None,
    attempts: tuple[SpanExtractionAttempt, ...],
    pages: tuple[SourcePage, ...],
    page_outcomes: tuple[SourcePageOutcome, ...],
    expected_page_count: int | None,
    elements: tuple[SourceElement, ...],
    knowledge_elements: tuple[SourceElement, ...],
    outcomes: tuple[SourceElementOutcome, ...],
    outcomes_by_element: dict[str, SourceElementOutcome],
    idea_blocks: tuple[IdeaBlock, ...],
) -> dict[str, dict[str, object]]:
    failed_attempt_ids = [attempt.id for attempt in attempts if attempt.outcome == "failed"]
    deferred_attempt_ids = [attempt.id for attempt in attempts if attempt.outcome == "deferred"]
    processed_windows = run.processed_window_count if run is not None else 0
    operational_pending = max(0, (run.expected_window_count if run else 0) - processed_windows)
    operational_complete = bool(
        run
        and run.status in {"completed", "completed_with_issues"}
        and run.expected_window_count == run.processed_window_count
        and not failed_attempt_ids
        and not deferred_attempt_ids
    )

    expected_pages = expected_page_count or len(pages)
    observed_pages = {page.page_number for page in pages}
    missing_page_ids = [f"page:{number}" for number in range(1, expected_pages + 1) if number not in observed_pages]
    page_outcomes_by_id = {outcome.source_page_id: outcome for outcome in page_outcomes}
    failed_page_ids = [
        page.id
        for page in pages
        if page_outcomes_by_id.get(page.id, None) and page_outcomes_by_id[page.id].outcome == "failed"
    ]
    deferred_page_ids: list[str] = []
    pending_page_ids = [page.id for page in pages if page.id not in page_outcomes_by_id]
    quarantined_page_ids = [page.id for page in pages if page.classification == "unsupported"]
    quarantined_page_ids.extend(
        page.id
        for page in pages
        if page_outcomes_by_id.get(page.id, None)
        and page_outcomes_by_id[page.id].outcome == "quarantined"
        and page.id not in quarantined_page_ids
    )
    source_complete = not any(
        (missing_page_ids, failed_page_ids, deferred_page_ids, pending_page_ids, quarantined_page_ids)
    )

    missing_outcome_ids = [element.id for element in knowledge_elements if element.id not in outcomes_by_element]
    candidate_no_claim_ids = (
        list(missing_outcome_ids) if any(attempt.outcome in {"no_claim", "duplicate"} for attempt in attempts) else []
    )
    failed_element_ids = [outcome.source_element_id for outcome in outcomes if outcome.outcome == "failed"]
    quarantined_ids = [outcome.source_element_id for outcome in outcomes if outcome.outcome == "quarantined"]
    unaudited_ids = [
        outcome.source_element_id
        for outcome in outcomes
        if outcome.outcome == "verified_no_claim" and outcome.audit_status == "selected"
    ]
    passing_outcomes = {"extracted", "verified_no_claim", "intentionally_ignored"}
    extraction_complete = (
        len(outcomes_by_element) == len(knowledge_elements)
        and all(outcome.outcome in passing_outcomes for outcome in outcomes)
        and not failed_attempt_ids
        and not deferred_attempt_ids
        and not unaudited_ids
    )

    verified_claims = [
        block
        for block in idea_blocks
        if block.current_revision is not None and block.current_revision.publication_status == "verified"
    ]
    publication_complete = all(block.current_revision is not None for block in verified_claims)
    return {
        "operational": {
            "status": (
                "complete"
                if operational_complete
                else (
                    "failed"
                    if failed_attempt_ids or (run is not None and run.status == "failed")
                    else "requires_review"
                )
            ),
            "expected_windows": run.expected_window_count if run else 0,
            "processed_windows": processed_windows,
            "pending_count": operational_pending,
            "deferred_count": len(deferred_attempt_ids),
            "failed_ids": failed_attempt_ids,
            "deferred_ids": deferred_attempt_ids,
        },
        "source": {
            "status": (
                "complete"
                if source_complete
                else ("failed" if missing_page_ids or failed_page_ids else "requires_review")
            ),
            "expected_pages": expected_pages,
            "reconciled_pages": sum(
                page_outcomes_by_id.get(page.id, None) is not None
                and page_outcomes_by_id[page.id].outcome in {"extracted", "verified_no_claim", "intentionally_ignored"}
                for page in pages
            ),
            "missing_page_ids": missing_page_ids,
            "pending_ids": pending_page_ids,
            "deferred_ids": deferred_page_ids,
            "failed_ids": failed_page_ids,
            "quarantined_ids": quarantined_page_ids,
        },
        "extraction": {
            "status": (
                "complete"
                if extraction_complete
                else ("failed" if failed_element_ids or failed_attempt_ids else "requires_review")
            ),
            "expected_elements": len(knowledge_elements),
            "terminal_outcomes": len(outcomes_by_element),
            "outcomes": dict(sorted(Counter(outcome.outcome for outcome in outcomes).items())),
            "pending_ids": missing_outcome_ids,
            "deferred_ids": deferred_attempt_ids,
            "candidate_no_claim_ids": candidate_no_claim_ids,
            "quarantined_ids": quarantined_ids,
            "failed_ids": failed_element_ids,
            "unaudited_verified_no_claim_ids": unaudited_ids,
        },
        "knowledge": {
            "status": "complete" if publication_complete else "requires_review",
            "verified_claims": len(verified_claims),
        },
    }


def _unresolved_source_elements(
    session: Session,
    *,
    elements: tuple[SourceElement, ...],
    outcomes_by_element: dict[str, SourceElementOutcome],
    attempts: tuple[SpanExtractionAttempt, ...],
) -> list[dict[str, object]]:
    element_ids = tuple(element.id for element in elements)
    if not element_ids:
        return []
    attempts_by_element: dict[str, list[SpanExtractionAttempt]] = {}
    for attempt in attempts:
        attempts_by_element.setdefault(attempt.source_element_id, []).append(attempt)
    review_tasks = session.scalars(
        select(ReviewTask).where(
            ReviewTask.target_type == "source_element",
            ReviewTask.target_id.in_(element_ids),
            ReviewTask.status.in_(("open", "assigned", "needs_evidence")),
        )
    ).all()
    review_task_by_element = {task.target_id: task for task in review_tasks}
    vision_quarantine_by_element = {
        task.target_id: task for task in review_tasks if task.task_type == "vision_quarantine"
    }
    unresolved: list[dict[str, object]] = []
    for element in elements:
        outcome = outcomes_by_element.get(element.id)
        metadata = element.metadata_ or {}
        is_metadata_quarantined = bool(metadata.get("quarantined"))
        vision_task = vision_quarantine_by_element.get(element.id)
        has_text = bool((element.text or "").strip())
        if not has_text and not is_metadata_quarantined and vision_task is None:
            continue
        if outcome is not None and outcome.outcome in {"extracted", "verified_no_claim", "intentionally_ignored"}:
            continue
        linked_attempts = attempts_by_element.get(element.id, [])
        latest_attempt = max(linked_attempts, key=lambda item: (item.window_index, item.id), default=None)
        review_task = vision_task or review_task_by_element.get(element.id)
        if outcome is not None and outcome.outcome:
            reported_outcome = outcome.outcome
        elif is_metadata_quarantined or vision_task is not None:
            reported_outcome = "quarantined"
        else:
            reported_outcome = "pending"
        reason = None
        if outcome is not None and outcome.reason:
            reason = outcome.reason
        elif metadata.get("quarantine_reason"):
            reason = str(metadata["quarantine_reason"])
        elif metadata.get("error_message"):
            reason = str(metadata["error_message"])
        elif vision_task is not None and vision_task.reason:
            reason = vision_task.reason
        elif latest_attempt and latest_attempt.warnings:
            reason = latest_attempt.warnings[0]
        unresolved.append(
            {
                "id": element.id,
                "kind": element.kind,
                "page": element.page_number,
                "reason": reason,
                "outcome": reported_outcome,
                "attempt": (
                    {
                        "id": latest_attempt.id,
                        "outcome": latest_attempt.outcome,
                        "attempt_count": latest_attempt.attempt_count,
                    }
                    if latest_attempt is not None
                    else None
                ),
                "review_task_id": str(review_task.id) if review_task is not None else None,
            }
        )
    return unresolved


def _overall_status(completeness: dict[str, dict[str, object]]) -> str:
    statuses = {str(layer["status"]) for layer in completeness.values()}
    if "failed" in statuses:
        return "failed"
    if statuses == {"complete"}:
        return "complete"
    return "requires_review"


def _relationship_count(session: Session, idea_block_ids: tuple[str, ...]) -> int:
    if not idea_block_ids:
        return 0
    return (
        session.scalar(
            select(func.count(distinct(EntityRelationship.id)))
            .join(RelationshipProvenance, RelationshipProvenance.relationship_id == EntityRelationship.id)
            .where(RelationshipProvenance.idea_block_id.in_(idea_block_ids))
        )
        or 0
    )


def _unresolved_entity_count(session: Session, idea_block_ids: tuple[str, ...]) -> int:
    if not idea_block_ids:
        return 0
    return (
        session.scalar(
            select(func.count(distinct(EntityMention.id)))
            .select_from(EntityMention)
            .join(EntityResolutionDecision, EntityResolutionDecision.mention_id == EntityMention.id)
            .join(IdeaBlockRevision, IdeaBlockRevision.id == EntityMention.idea_block_revision_id)
            .where(
                IdeaBlockRevision.idea_block_id.in_(idea_block_ids),
                EntityResolutionDecision.is_current.is_(True),
                EntityResolutionDecision.status == "review_required",
            )
        )
        or 0
    )


def _original_counts(
    document: SourceDocument, version: SourceDocumentVersion, quality: ConversionQualityReport | None
) -> dict[str, int | None]:
    metadata = _with_source_metadata(
        _merged_metadata(document.metadata_, version.metadata_, quality.metadata_ if quality else None)
    )
    return {
        "pages": _optional_count(metadata, "original_page_count", "page_count"),
        "blocks": _optional_count(metadata, "original_block_count"),
        "tables": _optional_count(metadata, "original_table_count"),
        "images": _optional_count(metadata, "original_image_count"),
    }


def _converted_counts(version: SourceDocumentVersion, elements: tuple[SourceElement, ...]) -> dict[str, int | None]:
    metadata = _with_source_metadata(version.metadata_ or {})
    page_numbers = {elem.page_number for elem in elements if elem.page_number is not None}
    converted_pages = _optional_count(metadata, "converted_page_count")
    return {
        "pages": converted_pages if converted_pages is not None else (len(page_numbers) if page_numbers else None),
        "blocks": len(elements),
        "tables": sum(elem.kind == "table" for elem in elements),
        "images": sum(elem.kind in {"figure", "image", "picture"} for elem in elements),
    }


def _merged_metadata(*values: dict | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            result.update(value)
    return result


def _with_source_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    nested = metadata.get("source_metadata")
    return {**nested, **metadata} if isinstance(nested, dict) else metadata


def _optional_count(metadata: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


_OUTCOMES = ("extracted", "no_claim", "failed", "deferred", "unsupported", "duplicate")
