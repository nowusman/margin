"""Review-only handling for unified governance candidates emitted during extraction."""

from __future__ import annotations

import json
from collections.abc import Iterable
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.evidence_mapping import validate_claim_evidence
from margin_db.governance_models import GovernanceRecord
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision
from margin_db.review_models import ReviewTask
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_schema.extraction import ExtractedGovernanceRecordCandidate

GOVERNANCE_RECORD_REVIEW_TASK_TYPE = "governance_record_review"
GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE = "governance_record_candidate"
_OPEN_REVIEW_STATES = ("open", "assigned", "needs_evidence")


class GovernanceExtractionReviewError(ValueError):
    """Raised when a governance candidate has no valid source provenance."""


def create_governance_review_tasks(
    session: Session,
    *,
    candidates: Iterable[ExtractedGovernanceRecordCandidate],
    source_document_id: str,
    domain_id: str,
) -> tuple[ReviewTask, ...]:
    """Persist review tasks without creating or changing governance records.

    A business key is an identity assertion, never a creation instruction.  The
    only lookup is the immutable ``(domain_id, business_key)`` pair; titles and
    semantic similarity intentionally do not participate in matching.
    """

    source_document = session.get(SourceDocument, source_document_id)
    if source_document is None:
        raise GovernanceExtractionReviewError(f"source document not found: {source_document_id}")
    if source_document.domain_id != domain_id:
        raise GovernanceExtractionReviewError("governance candidate domain must match its source document")

    created_or_updated: list[ReviewTask] = []
    for candidate in candidates:
        elements = _validated_source_elements(session, candidate=candidate, source_document_id=source_document_id)
        records, identity_status = _matched_records(session, candidate=candidate, domain_id=domain_id)
        matched_record = records[0] if len(records) == 1 else None
        candidate_payload = candidate.model_dump(mode="json")
        target_id = _candidate_target_id(
            source_document_id=source_document_id, domain_id=domain_id, candidate_payload=candidate_payload
        )
        metadata = {
            "domain_id": domain_id,
            "source_document_id": source_document_id,
            "governance_candidate": candidate_payload,
            "identity_status": identity_status,
            "matched_record_id": matched_record.id if matched_record is not None else None,
            "idea_block_ids": list(_related_idea_block_ids(session, candidate=candidate, domain_id=domain_id)),
        }
        task = session.scalar(
            select(ReviewTask).where(
                ReviewTask.task_type == GOVERNANCE_RECORD_REVIEW_TASK_TYPE,
                ReviewTask.target_type == GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE,
                ReviewTask.target_id == target_id,
                ReviewTask.status.in_(_OPEN_REVIEW_STATES),
            )
        )
        if task is None:
            task = ReviewTask(
                task_type=GOVERNANCE_RECORD_REVIEW_TASK_TYPE,
                target_type=GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE,
                target_id=target_id,
                reason=f"governance candidate requires authorized review; identity={identity_status}",
                priority="high" if candidate.proposed_lifecycle_action is not None else "normal",
                recommendation="use an authorized governance record operation",
                evidence=[item.model_dump(mode="json") for item in candidate.evidence],
                metadata_=metadata,
            )
            session.add(task)
        else:
            task.reason = f"governance candidate requires authorized review; identity={identity_status}"
            task.priority = "high" if candidate.proposed_lifecycle_action is not None else "normal"
            task.recommendation = "use an authorized governance record operation"
            task.evidence = [item.model_dump(mode="json") for item in candidate.evidence]
            task.metadata_ = metadata
        created_or_updated.append(task)

        # ``elements`` is intentionally resolved before every task write so an
        # invalid or cross-document evidence reference can never become a
        # governance-change proposal.
        del elements
    session.flush()
    return tuple(created_or_updated)


def _validated_source_elements(
    session: Session, *, candidate: ExtractedGovernanceRecordCandidate, source_document_id: str
) -> dict[str, SourceElement]:
    source_element_ids = {evidence.source_element_id for evidence in candidate.evidence}
    rows = session.execute(
        select(SourceElement, SourceDocumentVersion.source_document_id)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .where(SourceElement.id.in_(source_element_ids), SourceDocumentVersion.status == "active")
    ).all()
    elements = {element.id: element for element, document_id in rows if document_id == source_document_id}
    if len(elements) != len(source_element_ids):
        raise GovernanceExtractionReviewError("governance candidate references an unknown or foreign source element")
    for evidence in candidate.evidence:
        try:
            validate_claim_evidence(evidence, elements[evidence.source_element_id])
        except ValueError as exc:
            raise GovernanceExtractionReviewError(str(exc)) from exc
    return elements


def _matched_records(
    session: Session, *, candidate: ExtractedGovernanceRecordCandidate, domain_id: str
) -> tuple[list[GovernanceRecord], str]:
    if candidate.business_key is None:
        return [], "missing_business_key"
    records = list(
        session.scalars(
            select(GovernanceRecord)
            .where(GovernanceRecord.domain_id == domain_id, GovernanceRecord.business_key == candidate.business_key)
            .limit(2)
        ).all()
    )
    if not records:
        return records, "new_business_key"
    if len(records) > 1:
        return records, "ambiguous_business_key"
    if records[0].record_type != candidate.record_type:
        return records, "record_type_mismatch"
    return records, "matched"


def _candidate_target_id(*, source_document_id: str, domain_id: str, candidate_payload: dict[str, object]) -> str:
    canonical = json.dumps(
        {"source_document_id": source_document_id, "domain_id": domain_id, "candidate": candidate_payload},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"governance_candidate:{sha256(canonical.encode()).hexdigest()}"


def _related_idea_block_ids(
    session: Session, *, candidate: ExtractedGovernanceRecordCandidate, domain_id: str
) -> tuple[str, ...]:
    source_element_ids = {evidence.source_element_id for evidence in candidate.evidence}
    source_quotes = {evidence.source_quote for evidence in candidate.evidence}
    if not source_element_ids or not source_quotes:
        return ()
    return tuple(
        sorted(
            set(
                session.scalars(
                    select(ClaimEvidence.idea_block_id)
                    .join(IdeaBlock, IdeaBlock.id == ClaimEvidence.idea_block_id)
                    .join(
                        IdeaBlockRevision,
                        (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                        & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
                    )
                    .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
                    .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
                    .where(
                        IdeaBlock.domain_id == domain_id,
                        IdeaBlock.merged_into_id.is_(None),
                        IdeaBlock.tombstoned_at.is_(None),
                        IdeaBlock.archived_at.is_(None),
                        ClaimEvidence.status == "active",
                        ClaimEvidence.idea_block_revision_id == IdeaBlockRevision.id,
                        SourceDocumentVersion.status == "active",
                        ClaimEvidence.source_element_id.in_(source_element_ids),
                        ClaimEvidence.source_quote.in_(source_quotes),
                    )
                ).all()
            )
        )
    )
