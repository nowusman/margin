from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from app.authz import (
    permitted_governance_domains,
    permitted_governance_record_types,
    permitted_source_element_ids,
    require_any_domain_permission,
    require_entity_permission,
    require_governance_permission,
    require_permission,
    require_source_permission,
)
from app.source_routes import get_session_factory
from margin_db.audit import write_audit_event
from margin_db.entity_catalog import ENTITY_RESOLUTION_TASK_TYPE
from margin_db.entity_models import Entity, EntityMention
from margin_db.governance import GovernanceError, list_review_tasks, review_task_to_dict, update_review_task
from margin_db.governance_extraction_review import (
    GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE,
    GOVERNANCE_RECORD_REVIEW_TASK_TYPE,
)
from margin_db.governance_models import GovernanceRecord, GovernanceRecordIdeaBlock
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRelation
from margin_db.relationship_models import EntityRelationship
from margin_db.review_bundles import ReviewBundleError, build_review_bundle, decide_review_bundle
from margin_db.review_models import ReviewTask, ReviewTaskComment
from margin_db.review_transitions import ReviewTransitionError, apply_semantic_review_action
from margin_db.semantic_resolution import SEMANTIC_RESOLUTION_TASK_TYPE
from margin_db.source_models import (
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourceElementOutcome,
    SourcePage,
    UnmappedExtraction,
)
from margin_db.user_models import User

router = APIRouter()


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    assigned_to: str | None = None
    resolution: str | None = None
    comment: str | None = None


class ReviewBundleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    resolution: str = Field(min_length=1)
    entity_resolution_targets: dict[str, str] = Field(default_factory=dict)


class SemanticReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "confirm_equivalent", "approve_distinct", "mark_refinement", "mark_contradiction", "mark_supersession"
    ]
    note: str = Field(min_length=1)
    expected_updated_at: datetime | None = None
    survivor_id: str | None = None
    source_idea_block_id: str | None = None
    target_idea_block_id: str | None = None


def _actor(request: Request) -> str:
    principal_id = getattr(request.state, "principal_id", None)
    return principal_id if isinstance(principal_id, str) and principal_id else "system"


def _resolve_assignee(session: Session, assignee: str) -> str:
    """Return the user a review task is assigned to.

    A review task names a user, so an assignment has to name one that exists.
    A reader may name that user by identifier or by the address the directory
    knows them by, and an assignment to nobody is refused rather than stored.
    """

    candidate = assignee.strip()
    if not candidate:
        raise HTTPException(status_code=422, detail="assignee is required")
    if session.get(User, candidate) is not None:
        return candidate
    by_email = session.scalar(select(User.id).where(User.email == candidate))
    if by_email:
        return str(by_email)
    raise HTTPException(status_code=422, detail=f"unknown assignee: {candidate}")


def _require_review_task_scope(session: Session, task: ReviewTask, request: Request) -> None:
    if task.task_type == SEMANTIC_RESOLUTION_TASK_TYPE:
        metadata_ids = (task.metadata_ or {}).get("idea_block_ids")
        if task.target_type == "idea_block":
            idea_block_ids = [task.target_id]
        elif task.target_type == "idea_block_pair" and isinstance(metadata_ids, list) and len(metadata_ids) == 2:
            idea_block_ids = [str(item) for item in metadata_ids]
        else:
            raise HTTPException(status_code=409, detail="semantic review task has no valid idea-block scope")
        if (
            not idea_block_ids
            or any(not item for item in idea_block_ids)
            or (task.target_type == "idea_block_pair" and len(set(idea_block_ids)) != 2)
        ):
            raise HTTPException(status_code=409, detail="semantic review task has no valid idea-block scope")
        blocks = session.scalars(select(IdeaBlock).where(IdeaBlock.id.in_(idea_block_ids))).all()
        if len(blocks) != len(set(idea_block_ids)):
            raise HTTPException(status_code=409, detail="semantic review task references a missing idea block")
        domain_ids = {block.domain_id for block in blocks}
    elif task.task_type == ENTITY_RESOLUTION_TASK_TYPE:
        if task.target_type == "entity":
            require_entity_permission(session, task.target_id, permissions=request.state.permissions, action="review")
            return
        elif task.target_type == "entity_mention":
            mention = session.get(EntityMention, task.target_id)
            if mention is None:
                raise HTTPException(status_code=409, detail="entity resolution task references a missing mention")
            domain_ids = {mention.domain_id}
        elif task.target_type == "idea_block":
            block = session.get(IdeaBlock, task.target_id)
            if block is None:
                raise HTTPException(status_code=409, detail="entity resolution task references a missing idea block")
            domain_ids = {block.domain_id}
        else:
            raise HTTPException(status_code=409, detail="entity resolution task has no valid scope")
    elif task.target_type in {"source_document", "source_page", "source_element", "extraction_candidate"}:
        require_source_permission(
            session, _source_document_id_for_task(session, task), permissions=request.state.permissions, action="review"
        )
        return
    elif task.target_type == "source_element_outcome":
        outcome = session.get(SourceElementOutcome, task.target_id)
        if outcome is None:
            raise HTTPException(status_code=409, detail="source element outcome task references a missing outcome")
        require_source_permission(
            session,
            _source_document_id_for_element(session, outcome.source_element_id),
            permissions=request.state.permissions,
            action="review",
        )
        return
    elif task.target_type == "unmapped_extraction":
        unmapped = session.get(UnmappedExtraction, task.target_id)
        if unmapped is None:
            raise HTTPException(status_code=409, detail="unmapped extraction task references a missing record")
        require_source_permission(
            session, unmapped.source_document_id, permissions=request.state.permissions, action="review"
        )
        return
    elif task.target_type == "entity_relationship":
        relationship = session.get(EntityRelationship, task.target_id)
        if relationship is None:
            raise HTTPException(status_code=409, detail="relationship task references a missing relationship")
        for entity_id in {relationship.source_entity_id, relationship.target_entity_id}:
            require_entity_permission(session, entity_id, permissions=request.state.permissions, action="review")
        return
    elif task.target_type == "idea_block_relation":
        relation = session.get(IdeaBlockRelation, task.target_id)
        if relation is None:
            raise HTTPException(status_code=409, detail="contradiction task references a missing IdeaBlock relation")
        if task.task_type != "governance_contradiction_review" or relation.relation_type != "contradicts":
            raise HTTPException(status_code=409, detail="review task has an invalid contradiction scope")
        if relation.status != "verified":
            raise HTTPException(
                status_code=409, detail="contradiction task is stale because the relation is not verified"
            )
        relation_block_ids = {relation.source_idea_block_id, relation.target_idea_block_id}
        blocks = session.scalars(select(IdeaBlock).where(IdeaBlock.id.in_(relation_block_ids))).all()
        if len(blocks) != len(relation_block_ids):
            raise HTTPException(status_code=409, detail="contradiction task references a missing IdeaBlock")
        if any(
            block.current_revision is None or block.current_revision.publication_status != "verified"
            for block in blocks
        ):
            raise HTTPException(status_code=409, detail="contradiction task has an unverified IdeaBlock endpoint")
        domain_ids = {block.domain_id for block in blocks}
        for domain_id in domain_ids:
            require_permission(
                permissions=request.state.permissions,
                domain_id=domain_id,
                category=None,
                sensitivity=None,
                action="review",
            )
        records = session.scalars(
            select(GovernanceRecord)
            .join(GovernanceRecordIdeaBlock, GovernanceRecordIdeaBlock.governance_record_id == GovernanceRecord.id)
            .where(GovernanceRecordIdeaBlock.idea_block_id.in_(relation_block_ids))
            .distinct()
        ).all()
        scoped_record_ids = _task_governance_record_ids(task)
        if scoped_record_ids is not None:
            if not scoped_record_ids or not scoped_record_ids.issubset({record.id for record in records}):
                raise HTTPException(status_code=409, detail="contradiction task has an invalid governance record scope")
            records = [record for record in records if record.id in scoped_record_ids]
        if not records:
            raise HTTPException(status_code=409, detail="contradiction task has no governance record scope")
        for record in records:
            require_governance_permission(
                permissions=request.state.permissions,
                domain_id=record.domain_id,
                record_type=record.record_type,
                operation="approve",
            )
        return
    elif task.target_type == "architecture_claim":
        source_document_ids = _source_document_ids_from_task_evidence(session, task)
        if not source_document_ids:
            raise HTTPException(status_code=409, detail="review task has no source scope")
        for source_document_id in source_document_ids:
            require_source_permission(
                session, source_document_id, permissions=request.state.permissions, action="review"
            )
        return
    elif task.target_type == "governance_record":
        record = session.get(GovernanceRecord, task.target_id)
        if record is None:
            raise HTTPException(status_code=409, detail="governance review task references a missing record")
        require_governance_permission(
            permissions=request.state.permissions,
            domain_id=record.domain_id,
            record_type=record.record_type,
            operation="approve",
        )
        return
    elif task.target_type == GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE:
        metadata = task.metadata_ or {}
        record_type = _governance_candidate_record_type(metadata)
        domain_id = metadata.get("domain_id")
        if not isinstance(domain_id, str) or not domain_id.strip():
            raise HTTPException(status_code=409, detail="governance review task has no governance scope")
        require_governance_permission(
            permissions=request.state.permissions, domain_id=domain_id, record_type=record_type, operation="approve"
        )
        candidate_source_document_id = metadata.get("source_document_id")
        if not isinstance(candidate_source_document_id, str) or not candidate_source_document_id.strip():
            raise HTTPException(status_code=409, detail="governance review task has no source scope")
        require_source_permission(
            session, candidate_source_document_id, permissions=request.state.permissions, action="review"
        )
        return
    elif task.target_type == "idea_block":
        block = session.get(IdeaBlock, task.target_id)
        if block is None:
            raise HTTPException(status_code=409, detail="review task references a missing idea block")
        domain_ids = {block.domain_id}
    else:
        raise HTTPException(status_code=409, detail="review task has no valid governance scope")
    for domain_id in domain_ids:
        require_permission(
            permissions=request.state.permissions, domain_id=domain_id, category=None, sensitivity=None, action="review"
        )


def _source_document_id_for_task(session: Session, task: ReviewTask) -> str:
    if task.target_type == "source_document":
        source_document_id = task.target_id
    elif task.target_type == "source_page":
        source_document_id = session.scalar(
            select(SourceDocumentVersion.source_document_id)
            .join(SourcePage, SourcePage.source_version_id == SourceDocumentVersion.id)
            .where(SourcePage.id == task.target_id)
        )
    else:
        source_document_id = _source_document_id_for_element(session, task.target_id)
    if not source_document_id:
        raise HTTPException(status_code=409, detail="review task references a missing source scope")
    return str(source_document_id)


def _source_document_id_for_element(session: Session, source_element_id: str) -> str:
    source_document_id = session.scalar(
        select(SourceDocumentVersion.source_document_id)
        .join(SourceElement, SourceElement.source_version_id == SourceDocumentVersion.id)
        .where(SourceElement.id == source_element_id)
    )
    if not source_document_id:
        raise HTTPException(status_code=409, detail="review task references a missing source element")
    return str(source_document_id)


def _source_document_ids_from_task_evidence(session: Session, task: ReviewTask) -> set[str]:
    source_element_ids = {
        str(item["source_element_id"])
        for item in task.evidence or []
        if isinstance(item, dict) and item.get("source_element_id")
    }
    if not source_element_ids:
        return set()
    return set(
        session.scalars(
            select(SourceDocumentVersion.source_document_id)
            .join(SourceElement, SourceElement.source_version_id == SourceDocumentVersion.id)
            .where(SourceElement.id.in_(source_element_ids))
        ).all()
    )


def _has_review_task_scope(session: Session, task: ReviewTask, request: Request) -> bool:
    try:
        _require_review_task_scope(session, task, request)
    except HTTPException:
        return False
    return True


def _governance_candidate_record_type(metadata: dict[str, object]) -> str:
    candidate = metadata.get("governance_candidate")
    record_type = candidate.get("record_type") if isinstance(candidate, dict) else None
    if not isinstance(record_type, str) or not record_type.strip():
        raise HTTPException(status_code=409, detail="governance review task has no valid record type")
    return record_type


def _task_governance_record_ids(task: ReviewTask) -> frozenset[str] | None:
    values = (task.metadata_ or {}).get("governance_record_ids")
    if not isinstance(values, list):
        return None
    return frozenset(str(value) for value in values if isinstance(value, str) and value.strip())


def _review_task_for_scope(session: Session, task_id: uuid.UUID) -> ReviewTask | None:
    """Load task metadata without hydrating evidence before authorization."""

    return session.scalar(select(ReviewTask).options(defer(ReviewTask.evidence)).where(ReviewTask.id == task_id))


def _authorized_contradiction_source_element_ids(
    session: Session, task: ReviewTask, request: Request
) -> frozenset[str] | None:
    if task.target_type != "idea_block_relation":
        return None
    relation = session.get(IdeaBlockRelation, task.target_id)
    if relation is None:
        return frozenset()
    source_element_ids = session.scalars(
        select(ClaimEvidence.source_element_id).where(
            ClaimEvidence.idea_block_id.in_((relation.source_idea_block_id, relation.target_idea_block_id)),
            ClaimEvidence.source_element_id.is_not(None),
        )
    ).all()
    return permitted_source_element_ids(
        session, source_element_ids, permissions=request.state.permissions, action="read"
    )


def _authorized_contradiction_governance_record_ids(task: ReviewTask) -> frozenset[str] | None:
    if task.target_type != "idea_block_relation":
        return None
    return _task_governance_record_ids(task)


def _task_idea_block_ids(session: Session, task: ReviewTask) -> set[str]:
    if task.target_type == "idea_block":
        return {task.target_id}
    if task.target_type == "idea_block_relation":
        relation = session.get(IdeaBlockRelation, task.target_id)
        if relation is not None:
            return {relation.source_idea_block_id, relation.target_idea_block_id}
        return set()
    metadata_ids = (task.metadata_ or {}).get("idea_block_ids")
    if isinstance(metadata_ids, list):
        return {str(item) for item in metadata_ids if str(item).strip()}
    if task.target_type == "idea_block_pair":
        blocks = list(session.scalars(select(IdeaBlock.id)).all())
        for left_id in blocks:
            for right_id in blocks:
                if left_id != right_id and f"{left_id}:{right_id}" == task.target_id:
                    return {left_id, right_id}
    return set()


def _task_domain_id(
    session: Session,
    task: ReviewTask,
    *,
    entities: dict[str, Entity],
    idea_blocks: dict[str, IdeaBlock],
    source_domains: dict[tuple[str, str], str],
) -> str | None:
    entity = entities.get(task.target_id) if task.target_type == "entity" else None
    if entity is not None:
        return entity.domain_id
    if task.target_type == "entity_mention":
        mention = session.get(EntityMention, task.target_id)
        return mention.domain_id if mention is not None else None
    source_domain = source_domains.get((task.target_type, task.target_id))
    if source_domain is not None:
        return source_domain
    if task.target_type == "governance_record":
        metadata_domain = (task.metadata_ or {}).get("domain_id")
        if isinstance(metadata_domain, str) and metadata_domain:
            return metadata_domain
        record = session.get(GovernanceRecord, task.target_id)
        return record.domain_id if record is not None else None
    if task.target_type == GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE:
        metadata_domain = (task.metadata_ or {}).get("domain_id")
        return metadata_domain if isinstance(metadata_domain, str) and metadata_domain else None
    domains = {idea_blocks[item].domain_id for item in _task_idea_block_ids(session, task) if item in idea_blocks}
    return next(iter(domains)) if len(domains) == 1 else None


class ReviewTaskRaise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: str = Field(min_length=1, max_length=60)
    target_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)
    task_type: str = Field(default="manual_review", max_length=80)
    recommendation: str | None = Field(default=None, max_length=2_000)
    domain_id: str | None = Field(default=None, max_length=200)


@router.post("/review/tasks", status_code=201)
def raise_review_task(body: ReviewTaskRaise, request: Request) -> dict[str, object]:
    """Raise a review task so a human resolves something an agent found.

    Raising a task is a governed act. It requires review permission, it is
    written to the audit trail, and it never changes the record it points at.
    """

    require_any_domain_permission(
        permissions=request.state.permissions, action="review", detail="missing review access to raise a review task"
    )
    with get_session_factory(request)() as session:
        task = ReviewTask(
            task_type=body.task_type,
            target_type=body.target_type,
            target_id=body.target_id,
            reason=body.reason,
            recommendation=body.recommendation,
            metadata_={"domain_id": body.domain_id} if body.domain_id else {},
        )
        session.add(task)
        session.flush()
        write_audit_event(
            session,
            actor=getattr(request.state, "principal_id", "anonymous"),
            action="review_task_raised",
            target_type=body.target_type,
            target_id=body.target_id,
            after={"task_id": str(task.id), "task_type": body.task_type},
        )
        session.commit()
        return {"task": review_task_to_dict(session, task)}


@router.get("/review/tasks")
def review_tasks(
    request: Request,
    status: str | None = None,
    assigned_to: str | None = None,
    task_type: str | None = None,
    domain_id: str | None = None,
    priority: str | None = None,
) -> dict[str, object]:
    session_factory = get_session_factory(request)
    require_any_domain_permission(
        permissions=request.state.permissions, action="review", detail="missing review access for review tasks"
    )
    if domain_id:
        require_permission(
            permissions=request.state.permissions, domain_id=domain_id, category=None, sensitivity=None, action="review"
        )
    if assigned_to == "me":
        # A reader asking for their own work names themselves in the only way
        # they can, and the queue resolves that to the one it knows.
        assigned_to = getattr(request.state, "principal_id", None) or ""
    with session_factory() as session:
        tasks = list_review_tasks(
            session,
            status=status,
            assigned_to=assigned_to,
            task_type=task_type,
            domain_id=domain_id,
            priority=priority,
            defer_evidence=True,
            excluded_target_types=(GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE, "governance_record"),
        )
        governance_record_types = permitted_governance_record_types(request.state.permissions, "approve")
        governance_domains = permitted_governance_domains(request.state.permissions, "approve")
        if governance_record_types and governance_domains != frozenset():
            governance_statement = (
                select(ReviewTask)
                .join(GovernanceRecord, GovernanceRecord.id == ReviewTask.target_id)
                .where(
                    ReviewTask.target_type == "governance_record",
                    GovernanceRecord.record_type.in_(governance_record_types),
                )
            )
            if governance_domains is not None:
                governance_statement = governance_statement.where(GovernanceRecord.domain_id.in_(governance_domains))
            if domain_id:
                governance_statement = governance_statement.where(GovernanceRecord.domain_id == domain_id)
            if status:
                governance_statement = governance_statement.where(ReviewTask.status == status)
            if assigned_to:
                governance_statement = governance_statement.where(ReviewTask.assigned_to == assigned_to)
            if task_type:
                governance_statement = governance_statement.where(ReviewTask.task_type == task_type)
            if priority:
                governance_statement = governance_statement.where(ReviewTask.priority == priority)
            tasks.extend(session.scalars(governance_statement.order_by(ReviewTask.created_at, ReviewTask.id)).all())
        if governance_record_types and governance_domains != frozenset():
            candidate_statement = select(ReviewTask).where(
                ReviewTask.target_type == GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE,
                ReviewTask.metadata_["governance_candidate"]["record_type"].as_string().in_(governance_record_types),
            )
            if governance_domains is not None:
                candidate_statement = candidate_statement.where(
                    ReviewTask.metadata_["domain_id"].as_string().in_(governance_domains)
                )
            if domain_id:
                candidate_statement = candidate_statement.where(
                    ReviewTask.metadata_["domain_id"].as_string() == domain_id
                )
            if status:
                candidate_statement = candidate_statement.where(ReviewTask.status == status)
            if assigned_to:
                candidate_statement = candidate_statement.where(ReviewTask.assigned_to == assigned_to)
            if task_type:
                candidate_statement = candidate_statement.where(ReviewTask.task_type == task_type)
            if priority:
                candidate_statement = candidate_statement.where(ReviewTask.priority == priority)
            candidate_scope_tasks = session.scalars(
                candidate_statement.options(defer(ReviewTask.evidence)).order_by(ReviewTask.created_at, ReviewTask.id)
            ).all()
            authorized_candidate_ids = [
                task.id for task in candidate_scope_tasks if _has_review_task_scope(session, task, request)
            ]
            if authorized_candidate_ids:
                tasks.extend(
                    session.scalars(select(ReviewTask).where(ReviewTask.id.in_(authorized_candidate_ids))).all()
                )
        authorized_tasks = [task for task in tasks if _has_review_task_scope(session, task, request)]
        entities = {
            entity.id: entity
            for entity in session.scalars(
                select(Entity).where(Entity.id.in_([task.target_id for task in tasks if task.target_type == "entity"]))
            )
        }
        idea_block_ids = {idea_block_id for task in tasks for idea_block_id in _task_idea_block_ids(session, task)}
        idea_blocks = {
            block.id: block
            for block in session.scalars(select(IdeaBlock).where(IdeaBlock.id.in_(idea_block_ids))).all()
        }
        source_domains: dict[tuple[str, str], str] = {}
        source_document_ids = {task.target_id for task in tasks if task.target_type == "source_document"}
        if source_document_ids:
            source_domains.update(
                {
                    ("source_document", document.id): document.domain_id
                    for document in session.scalars(
                        select(SourceDocument).where(SourceDocument.id.in_(source_document_ids))
                    ).all()
                }
            )
        source_page_ids = {task.target_id for task in tasks if task.target_type == "source_page"}
        if source_page_ids:
            source_domains.update(
                {
                    ("source_page", target_id): source_domain
                    for target_id, source_domain in session.execute(
                        select(SourcePage.id, SourceDocument.domain_id)
                        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourcePage.source_version_id)
                        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
                        .where(SourcePage.id.in_(source_page_ids))
                    ).all()
                }
            )
        source_element_ids = {task.target_id for task in tasks if task.target_type == "source_element"}
        source_element_ids.update(
            str(item["source_element_id"])
            for task in tasks
            for item in (task.evidence or [])
            if isinstance(item, dict) and item.get("source_element_id")
        )
        if source_element_ids:
            source_domains.update(
                {
                    ("source_element", target_id): source_domain
                    for target_id, source_domain in session.execute(
                        select(SourceElement.id, SourceDocument.domain_id)
                        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
                        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
                        .where(SourceElement.id.in_(source_element_ids))
                    ).all()
                }
            )
        source_outcome_ids = {task.target_id for task in tasks if task.target_type == "source_element_outcome"}
        if source_outcome_ids:
            source_domains.update(
                {
                    ("source_element_outcome", outcome_id): source_domain
                    for outcome_id, source_domain in session.execute(
                        select(SourceElementOutcome.id, SourceDocument.domain_id)
                        .join(SourceElement, SourceElement.id == SourceElementOutcome.source_element_id)
                        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
                        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
                        .where(SourceElementOutcome.id.in_(source_outcome_ids))
                    ).all()
                }
            )
        unmapped_ids = {task.target_id for task in tasks if task.target_type == "unmapped_extraction"}
        if unmapped_ids:
            source_domains.update(
                {
                    ("unmapped_extraction", unmapped.id): source_domain
                    for unmapped, source_domain in session.execute(
                        select(UnmappedExtraction, SourceDocument.domain_id)
                        .join(SourceDocument, SourceDocument.id == UnmappedExtraction.source_document_id)
                        .where(UnmappedExtraction.id.in_(unmapped_ids))
                    ).all()
                }
            )
        for task in tasks:
            if task.target_type not in {"architecture_claim", GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE}:
                continue
            if task.target_type == "architecture_claim":
                domains = {
                    source_domains[("source_element", source_element_id)]
                    for source_element_id in {
                        str(item["source_element_id"])
                        for item in task.evidence or []
                        if isinstance(item, dict) and item.get("source_element_id")
                    }
                    if ("source_element", source_element_id) in source_domains
                }
            else:
                domains = {
                    source_domains[("source_element", source_element_id)]
                    for source_element_id in {
                        str(item["source_element_id"])
                        for item in task.evidence or []
                        if isinstance(item, dict) and item.get("source_element_id")
                    }
                    if ("source_element", source_element_id) in source_domains
                }
            if len(domains) == 1:
                source_domains[(task.target_type, task.target_id)] = next(iter(domains))
        scoped_tasks = [
            task
            for task in authorized_tasks
            if (
                task_domain := _task_domain_id(
                    session, task, entities=entities, idea_blocks=idea_blocks, source_domains=source_domains
                )
            )
            and request.state.permissions.allows_domain(task_domain, "review")
        ]
        return {
            "tasks": [
                {
                    **review_task_to_dict(task),
                    "domain_id": _task_domain_id(
                        session, task, entities=entities, idea_blocks=idea_blocks, source_domains=source_domains
                    ),
                }
                for task in scoped_tasks
            ]
        }


@router.get("/review/tasks/{task_id}")
def review_task_detail(task_id: uuid.UUID, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    require_any_domain_permission(
        permissions=request.state.permissions, action="review", detail="missing review access for review tasks"
    )
    with session_factory() as session:
        task = _review_task_for_scope(session, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="review task not found")
        _require_review_task_scope(session, task, request)
        comments = session.scalars(
            select(ReviewTaskComment)
            .where(ReviewTaskComment.review_task_id == task_id)
            .order_by(ReviewTaskComment.created_at)
        ).all()
        return {
            "task": {
                **review_task_to_dict(task),
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                "comments": [
                    {
                        "id": str(comment.id),
                        "author_id": comment.author_id,
                        "comment": comment.comment,
                        "created_at": comment.created_at.isoformat(),
                    }
                    for comment in comments
                ],
            },
            "bundle": build_review_bundle(
                session,
                task_id,
                allowed_source_element_ids=_authorized_contradiction_source_element_ids(session, task, request),
                allowed_governance_record_ids=_authorized_contradiction_governance_record_ids(task),
            ),
        }


@router.get("/review/tasks/{task_id}/bundle")
def review_task_bundle(task_id: uuid.UUID, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    require_any_domain_permission(
        permissions=request.state.permissions, action="review", detail="missing review access for review bundle"
    )
    try:
        with session_factory() as session:
            task = _review_task_for_scope(session, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="review task not found")
            _require_review_task_scope(session, task, request)
            return {
                "bundle": build_review_bundle(
                    session,
                    task_id,
                    allowed_source_element_ids=_authorized_contradiction_source_element_ids(session, task, request),
                    allowed_governance_record_ids=_authorized_contradiction_governance_record_ids(task),
                )
            }
    except ReviewBundleError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/review/tasks/{task_id}/decision")
def review_task_bundle_decision(task_id: uuid.UUID, body: ReviewBundleDecision, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    require_any_domain_permission(
        permissions=request.state.permissions, action="review", detail="missing review access for review bundle"
    )
    try:
        with session_factory() as session:
            task = _review_task_for_scope(session, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="review task not found")
            _require_review_task_scope(session, task, request)
            task = decide_review_bundle(
                session,
                task_id=task_id,
                decision=body.decision,
                actor_id=getattr(request.state, "principal_id", None),
                resolution=body.resolution,
                entity_resolution_targets=body.entity_resolution_targets,
            )
            write_audit_event(
                session,
                actor=_actor(request),
                action="governance.review_bundle.decided",
                target_type="review_task",
                target_id=str(task_id),
                before={"status": "open_or_assigned"},
                after={"status": task.status, "resolution": task.resolution},
            )
            session.commit()
            return {"task": review_task_to_dict(task)}
    except ReviewBundleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/review/tasks/{task_id}/action")
def semantic_review_action(task_id: uuid.UUID, body: SemanticReviewAction, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    require_any_domain_permission(
        permissions=request.state.permissions,
        action="review",
        detail="missing review access for semantic review action",
    )
    try:
        with session_factory() as session:
            task = session.get(ReviewTask, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="review task not found")
            _require_review_task_scope(session, task, request)
            result = apply_semantic_review_action(
                session,
                task_id=task_id,
                action=body.action,
                actor=_actor(request),
                note=body.note,
                expected_updated_at=body.expected_updated_at,
                survivor_id=body.survivor_id,
                source_idea_block_id=body.source_idea_block_id,
                target_idea_block_id=body.target_idea_block_id,
            )
            session.commit()
            return {
                "task": review_task_to_dict(result.task),
                "action": result.action,
                "survivor_id": result.survivor_id,
                "relation_id": result.relation_id,
                "updated_at": result.task.updated_at.isoformat() if result.task.updated_at else None,
            }
    except ReviewTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/review/tasks/{task_id}")
def update_review_task_route(task_id: uuid.UUID, body: ReviewUpdate, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    require_any_domain_permission(
        permissions=request.state.permissions, action="review", detail="missing review access for review tasks"
    )
    try:
        with session_factory() as session:
            before = _review_task_for_scope(session, task_id)
            if before is None:
                raise HTTPException(status_code=404, detail="review task not found")
            _require_review_task_scope(session, before, request)
            if before.task_type == GOVERNANCE_RECORD_REVIEW_TASK_TYPE and body.status in {
                "approved",
                "rejected",
                "closed",
            }:
                raise HTTPException(
                    status_code=409,
                    detail="governance record review tasks require an authorized governance record operation",
                )
            if (
                before.task_type == "governance_contradiction_review"
                and before.target_type == "idea_block_relation"
                and body.status in {"approved", "rejected", "closed"}
            ):
                raise HTTPException(
                    status_code=409, detail="contradiction review tasks require the relation-scoped decision operation"
                )
            before_state = {
                "status": before.status if before else None,
                "assigned_to": before.assigned_to if before else None,
                "resolution": before.resolution if before else None,
            }
            assigned_to = body.assigned_to
            if assigned_to is not None:
                assigned_to = _resolve_assignee(session, assigned_to)
            task = update_review_task(
                session,
                task_id,
                status=body.status,
                assigned_to=assigned_to,
                resolution=body.resolution,
                comment=body.comment,
                actor=getattr(request.state, "principal_id", None),
            )
            write_audit_event(
                session,
                actor=_actor(request),
                action="governance.review_task.updated",
                target_type="review_task",
                target_id=str(task_id),
                before=before_state,
                after={"status": task.status, "assigned_to": task.assigned_to, "resolution": task.resolution},
            )
            session.commit()
            return {"task": review_task_to_dict(task)}
    except GovernanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
