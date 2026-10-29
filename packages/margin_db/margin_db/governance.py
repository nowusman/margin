"""Governance consistency checks and review-task utilities."""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from margin_db.entity_models import Entity, EntityMention, EntityResolutionDecision
from margin_db.governance_models import (
    GovernanceRecord,
    GovernanceRecordEntity,
    GovernanceRecordIdeaBlock,
    GovernanceRecordRelation,
    GovernanceRecordRevision,
)
from margin_db.governance_types import BACKLOG_ORIGIN_STATUSES, GovernanceError
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRelation, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship
from margin_db.review_models import ReviewTask, ReviewTaskComment
from margin_db.source_models import SourceDocumentVersion, SourceElement
from margin_schema.findings import HygieneFinding
from margin_schema.ontology import validate_governance_relationship_endpoints

_OPEN_REVIEW_STATES = ("open", "assigned", "needs_evidence")
_REVIEW_STATES = frozenset((*_OPEN_REVIEW_STATES, "approved", "rejected", "closed"))


def run_hygiene_lint(session: Session) -> list[HygieneFinding]:
    """Report catalog conditions that require a human review task."""

    findings: list[HygieneFinding] = []
    for entity in session.scalars(select(Entity).order_by(Entity.id)).all():
        if entity.definition and not entity.definition_idea_block_id:
            findings.append(
                HygieneFinding(
                    rule_id="entity_definition_missing_evidence_link",
                    severity="error",
                    target_type="entity",
                    target_id=entity.id,
                    message="Canonical Entity definition is missing its definition IdeaBlock link.",
                    recommendation="Link the definition to an evidence-backed ontology IdeaBlock or remove it.",
                )
            )
    decision_rows = session.execute(
        select(EntityMention, EntityResolutionDecision)
        .join(EntityResolutionDecision, EntityResolutionDecision.mention_id == EntityMention.id)
        .where(EntityResolutionDecision.is_current.is_(True))
    ).all()
    for mention, decision in decision_rows:
        if decision.status != "review_required":
            continue
        task = session.scalar(
            select(ReviewTask.id).where(
                ReviewTask.task_type == "entity_resolution_review",
                ReviewTask.target_type == "entity_mention",
                ReviewTask.target_id == mention.id,
                ReviewTask.status.in_(_OPEN_REVIEW_STATES),
            )
        )
        if task is None:
            findings.append(
                HygieneFinding(
                    rule_id="unresolved_entity_mention_without_task",
                    severity="error",
                    target_type="entity_mention",
                    target_id=mention.id,
                    message="An unresolved EntityMention has no open resolution task.",
                    evidence=(
                        {
                            "source_element_id": mention.source_element_id,
                            "idea_block_revision_id": mention.idea_block_revision_id,
                        },
                    ),
                    recommendation="Create one entity-resolution review task for this mention.",
                )
            )
    findings.extend(governance_consistency_findings(session))
    return sorted(findings, key=lambda item: (item.rule_id, item.target_type, item.target_id))


def governance_consistency_findings(
    session: Session,
    *,
    allowed_record_types: frozenset[str] | None = None,
    allowed_domain_ids: frozenset[str] | None = None,
) -> list[HygieneFinding]:
    """Report governance records that cannot provide a valid evidence trace."""

    findings: list[HygieneFinding] = []
    records_query = select(GovernanceRecord)
    if allowed_record_types is not None:
        records_query = records_query.where(GovernanceRecord.record_type.in_(allowed_record_types))
    if allowed_domain_ids is not None:
        records_query = records_query.where(GovernanceRecord.domain_id.in_(allowed_domain_ids))
    records = session.scalars(records_query.order_by(GovernanceRecord.id)).all()
    records_by_id = {record.id: record for record in records}
    active_statuses = {
        "adr": {"accepted"},
        "risk": {"identified", "mitigating", "accepted"},
        "waiver": {"approved"},
        "technical_debt": {"identified", "accepted", "planned", "in_progress"},
        "architecture_backlog": {"open", "planned", "in_progress", "blocked"},
    }
    for record in records:
        revision = session.get(GovernanceRecordRevision, record.current_revision_id)
        if revision is None or revision.governance_record_id != record.id:
            findings.append(
                _governance_finding(
                    "governance_missing_current_revision",
                    record,
                    "Governance record does not point to one of its own revisions.",
                    "Create a complete canonical revision before publishing or reviewing the record.",
                )
            )
        links = session.scalars(
            select(GovernanceRecordIdeaBlock).where(GovernanceRecordIdeaBlock.governance_record_id == record.id)
        ).all()
        block_ids = [link.idea_block_id for link in links]
        for link in links:
            block = session.get(IdeaBlock, link.idea_block_id)
            current_revision = session.get(IdeaBlockRevision, block.current_revision_id) if block is not None else None
            if block is None or current_revision is None or current_revision.idea_block_id != block.id:
                findings.append(
                    _governance_finding(
                        "governance_link_missing_current_idea_block",
                        record,
                        f"Linked Idea Block {link.idea_block_id} is missing a valid current revision.",
                        "Relink a current evidence-backed Idea Block.",
                    )
                )
            elif block.merged_into_id is not None or block.tombstoned_at is not None or block.archived_at is not None:
                findings.append(
                    _governance_finding(
                        "governance_link_inactive_idea_block",
                        record,
                        f"Linked Idea Block {link.idea_block_id} is tombstoned or archived.",
                        "Unlink the inactive Idea Block and attach a current evidence-backed block.",
                    )
                )
            elif current_revision.publication_status != "verified":
                findings.append(
                    _governance_finding(
                        "governance_link_non_current_idea_block",
                        record,
                        f"Linked Idea Block {link.idea_block_id} has no verified current revision.",
                        "Attach a verified current Idea Block revision before relying on the record.",
                    )
                )
        entity_links = session.scalars(
            select(GovernanceRecordEntity).where(GovernanceRecordEntity.governance_record_id == record.id)
        ).all()
        for link in entity_links:
            entity = session.get(Entity, link.entity_id)
            if entity is None:
                findings.append(
                    _governance_finding(
                        "governance_link_missing_entity",
                        record,
                        f"Linked Entity {link.entity_id} no longer exists.",
                        "Unlink the missing Entity and attach a current canonical Entity.",
                    )
                )
            elif entity.lifecycle_status in {"superseded", "retired"}:
                findings.append(
                    _governance_finding(
                        "governance_link_inactive_entity",
                        record,
                        f"Linked Entity {link.entity_id} is {entity.lifecycle_status}.",
                        "Unlink the inactive Entity or replace it with its canonical successor.",
                    )
                )
        if record.lifecycle_status in active_statuses[record.record_type] and not _has_exact_evidence(
            session, record=record, idea_block_ids=block_ids
        ):
            findings.append(
                _governance_finding(
                    "governance_active_record_missing_evidence",
                    record,
                    "Active governance record has no linked exact source evidence.",
                    "Link an atomic Idea Block with supporting or refuting source evidence before "
                    "relying on the record.",
                )
            )
        if record.record_type == "architecture_backlog":
            findings.extend(_backlog_origin_findings(session, record, records_by_id))
    scope_restricted = allowed_record_types is not None or allowed_domain_ids is not None
    for relation in session.scalars(
        select(GovernanceRecordRelation).where(GovernanceRecordRelation.relation_type == "supersedes")
    ).all():
        source = records_by_id.get(relation.source_record_id)
        target = records_by_id.get(relation.target_record_id)
        if scope_restricted and (source is None or target is None):
            continue
        if source is None or target is None or source.record_type != "adr" or target.record_type != "adr":
            findings.append(
                HygieneFinding(
                    rule_id="governance_invalid_supersession",
                    severity="error",
                    target_type="governance_record_relation",
                    target_id=relation.id,
                    message="Supersession must connect two ADR records.",
                    recommendation="Remove the invalid relation and use an explicit ADR supersession operation.",
                )
            )
        elif target.lifecycle_status != "superseded":
            findings.append(
                HygieneFinding(
                    rule_id="governance_supersession_without_terminal_record",
                    severity="error",
                    target_type="governance_record_relation",
                    target_id=relation.id,
                    message="Superseded ADR relation points to a record that is not superseded.",
                    recommendation="Explicitly transition the replaced ADR or remove the relation.",
                )
            )
    return findings


def _has_exact_evidence(session: Session, *, record: GovernanceRecord, idea_block_ids: list[str]) -> bool:
    direct_evidence = session.scalar(
        select(ClaimEvidence.id)
        .join(GovernanceRecordRevision, GovernanceRecordRevision.id == ClaimEvidence.governance_record_revision_id)
        .where(
            GovernanceRecordRevision.governance_record_id == record.id,
            GovernanceRecordRevision.id == record.current_revision_id,
            ClaimEvidence.status == "active",
        )
        .limit(1)
    )
    if direct_evidence is not None:
        return True
    if not idea_block_ids:
        return False
    return (
        session.scalar(
            select(ClaimEvidence.id)
            .join(IdeaBlockRevision, IdeaBlockRevision.id == ClaimEvidence.idea_block_revision_id)
            .join(IdeaBlock, IdeaBlock.id == IdeaBlockRevision.idea_block_id)
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
            .where(
                ClaimEvidence.idea_block_id.in_(idea_block_ids),
                ClaimEvidence.source_element_id.is_not(None),
                ClaimEvidence.status == "active",
                SourceDocumentVersion.status == "active",
                IdeaBlock.current_revision_id == IdeaBlockRevision.id,
                IdeaBlockRevision.idea_block_id == IdeaBlock.id,
                IdeaBlockRevision.publication_status == "verified",
            )
            .limit(1)
        )
        is not None
    )


def _backlog_origin_findings(
    session: Session, record: GovernanceRecord, records_by_id: dict[str, GovernanceRecord]
) -> list[HygieneFinding]:
    attributes = record.attributes or {}
    origin_id = attributes.get("originating_governance_record_id")
    manual_origin = attributes.get("manual_origin")
    if isinstance(origin_id, str) and origin_id.strip():
        origin = records_by_id.get(origin_id) or session.get(GovernanceRecord, origin_id)
        if origin is not None and origin.id == record.id:
            message = "Architecture Backlog cannot originate from itself."
        elif origin is not None and origin.domain_id == record.domain_id:
            eligible_states = BACKLOG_ORIGIN_STATUSES.get(origin.record_type, frozenset())
            if origin.lifecycle_status not in eligible_states:
                message = "Architecture Backlog origin is not in an eligible interim lifecycle state."
            else:
                relation = session.scalar(
                    select(GovernanceRecordRelation).where(
                        GovernanceRecordRelation.source_record_id == record.id,
                        GovernanceRecordRelation.target_record_id == origin.id,
                        GovernanceRecordRelation.relation_type == "corrects",
                        GovernanceRecordRelation.status == "verified",
                    )
                )
                if relation is None:
                    message = "Architecture Backlog origin is missing its verified correction relation."
                else:
                    try:
                        validate_governance_relationship_endpoints(
                            relation.relation_type, record.record_type, origin.record_type
                        )
                    except ValueError as exc:
                        message = str(exc)
                    else:
                        provenance = relation.provenance
                        reason = provenance.get("reason") if isinstance(provenance, dict) else None
                        if not isinstance(reason, str) or not reason.strip():
                            message = "Architecture Backlog correction relation lacks meaningful provenance."
                        else:
                            return []
        else:
            message = "Architecture Backlog origin does not identify a record in the same domain."
    elif isinstance(manual_origin, str) and manual_origin.strip():
        return []
    else:
        message = "Architecture Backlog has neither an originating governance record nor a documented manual origin."
    return [
        _governance_finding(
            "governance_backlog_missing_valid_origin",
            record,
            message,
            "Link the item to its interim ADR, waiver, accepted risk, or technical debt, or document a manual origin.",
        )
    ]


def _governance_finding(rule_id: str, record: GovernanceRecord, message: str, recommendation: str) -> HygieneFinding:
    return HygieneFinding(
        rule_id=rule_id,
        severity="error",
        target_type="governance_record",
        target_id=record.id,
        message=message,
        recommendation=recommendation,
    )


def create_review_tasks_for_findings(session: Session, findings: list[HygieneFinding]) -> list[ReviewTask]:
    """Create at most one open task per deterministic hygiene finding."""

    created: list[ReviewTask] = []
    for finding in findings:
        task_type = f"hygiene:{finding.rule_id}"
        task = session.scalar(
            select(ReviewTask).where(
                ReviewTask.task_type == task_type,
                ReviewTask.target_type == finding.target_type,
                ReviewTask.target_id == finding.target_id,
                ReviewTask.status.in_(_OPEN_REVIEW_STATES),
            )
        )
        if task is None:
            task = ReviewTask(
                task_type=task_type,
                target_type=finding.target_type,
                target_id=finding.target_id,
                priority="high" if finding.severity == "error" else "normal",
                reason=finding.message,
                recommendation=finding.recommendation,
                evidence=[dict(item) for item in finding.evidence],
                metadata_={"rule_id": finding.rule_id},
            )
            session.add(task)
            created.append(task)
        else:
            task.reason = finding.message
            task.recommendation = finding.recommendation
            task.evidence = [dict(item) for item in finding.evidence]
    return created


def detect_contradiction_candidates(
    session: Session,
    *,
    allowed_record_types: frozenset[str] | None = None,
    allowed_domain_ids: frozenset[str] | None = None,
) -> list[ReviewTask]:
    """Open review work; contradiction detection never changes a record itself."""

    active_statuses = {
        "adr": {"accepted"},
        "risk": {"identified", "mitigating", "accepted"},
        "waiver": {"approved"},
        "technical_debt": {"identified", "accepted", "planned", "in_progress"},
        "architecture_backlog": {"open", "planned", "in_progress", "blocked"},
    }
    relations = session.scalars(
        select(IdeaBlockRelation).where(
            IdeaBlockRelation.relation_type == "contradicts", IdeaBlockRelation.status == "verified"
        )
    ).all()
    created: list[ReviewTask] = []
    for relation in relations:
        record_ids = session.scalars(
            select(GovernanceRecordIdeaBlock.governance_record_id)
            .where(
                GovernanceRecordIdeaBlock.idea_block_id.in_(
                    (relation.source_idea_block_id, relation.target_idea_block_id)
                )
            )
            .distinct()
        ).all()
        active_record_ids = [
            record.id
            for record in session.scalars(select(GovernanceRecord).where(GovernanceRecord.id.in_(record_ids))).all()
            if record.lifecycle_status in active_statuses.get(record.record_type, set())
            and (allowed_record_types is None or record.record_type in allowed_record_types)
            and (allowed_domain_ids is None or record.domain_id in allowed_domain_ids)
        ]
        if not active_record_ids:
            continue
        provenance = relation.decision_provenance if isinstance(relation.decision_provenance, dict) else {}
        if provenance.get("decision") == "approved":
            continue
        task = session.scalar(
            select(ReviewTask).where(
                ReviewTask.task_type == "governance_contradiction_review",
                ReviewTask.target_type == "idea_block_relation",
                ReviewTask.target_id == relation.id,
                ReviewTask.status.in_(_OPEN_REVIEW_STATES),
            )
        )
        if task is None:
            task = ReviewTask(
                task_type="governance_contradiction_review",
                target_type="idea_block_relation",
                target_id=relation.id,
                priority="high",
                reason="A verified contradiction affects an active governance record.",
                recommendation=(
                    "Review the evidence and explicitly decide whether either linked record needs a "
                    "lifecycle change."
                ),
                evidence=[
                    {
                        "source_idea_block_id": relation.source_idea_block_id,
                        "target_idea_block_id": relation.target_idea_block_id,
                        "governance_record_ids": active_record_ids,
                    }
                ],
                metadata_={
                    "idea_block_ids": [relation.source_idea_block_id, relation.target_idea_block_id],
                    "governance_record_ids": active_record_ids,
                    "relation_type": relation.relation_type,
                },
            )
            session.add(task)
            created.append(task)
    return created


def detect_blind_spots(
    session: Session,
    *,
    allowed_record_types: frozenset[str] | None = None,
    allowed_domain_ids: frozenset[str] | None = None,
) -> list[ReviewTask]:
    """Open review work for governance records that lack a valid evidence trace."""

    findings = [
        finding
        for finding in governance_consistency_findings(
            session, allowed_record_types=allowed_record_types, allowed_domain_ids=allowed_domain_ids
        )
        if finding.rule_id
        in {
            "governance_active_record_missing_evidence",
            "governance_backlog_missing_valid_origin",
            "governance_link_missing_current_idea_block",
            "governance_missing_current_revision",
            "governance_link_inactive_idea_block",
            "governance_link_non_current_idea_block",
            "governance_link_missing_entity",
            "governance_link_inactive_entity",
        }
    ]
    return create_review_tasks_for_findings(session, findings)


def list_review_tasks(
    session: Session,
    *,
    status: str | None = None,
    assigned_to: str | None = None,
    task_type: str | None = None,
    target_type: str | None = None,
    excluded_target_types: Collection[str] | None = None,
    domain_id: str | None = None,
    priority: str | None = None,
    defer_evidence: bool = False,
) -> list[ReviewTask]:
    statement = select(ReviewTask)
    if defer_evidence:
        statement = statement.options(defer(ReviewTask.evidence))
    if status:
        statement = statement.where(ReviewTask.status == status)
    if assigned_to:
        statement = statement.where(ReviewTask.assigned_to == assigned_to)
    if task_type:
        statement = statement.where(ReviewTask.task_type == task_type)
    if target_type:
        statement = statement.where(ReviewTask.target_type == target_type)
    if excluded_target_types:
        statement = statement.where(ReviewTask.target_type.not_in(tuple(excluded_target_types)))
    if priority:
        statement = statement.where(ReviewTask.priority == priority)
    tasks = list(session.scalars(statement.order_by(ReviewTask.created_at, ReviewTask.id)).all())
    if domain_id is None:
        return tasks
    return [task for task in tasks if review_task_domain_id(session, task) == domain_id]


def review_task_domain_id(session: Session, task: ReviewTask) -> str | None:
    if task.target_type == "governance_record":
        record = session.get(GovernanceRecord, task.target_id)
        return record.domain_id if record is not None else None
    if task.target_type == "entity":
        entity = session.get(Entity, task.target_id)
        return entity.domain_id if entity is not None else None
    if task.target_type == "entity_mention":
        mention = session.get(EntityMention, task.target_id)
        return mention.domain_id if mention is not None else None
    if task.target_type == "idea_block":
        block = session.get(IdeaBlock, task.target_id)
        return block.domain_id if block is not None else None
    if task.target_type == "entity_relationship":
        relationship = session.get(EntityRelationship, task.target_id)
        if relationship is None:
            return None
        entity = session.get(Entity, relationship.source_entity_id)
        return entity.domain_id if entity is not None else None
    if task.target_type == "idea_block_relation":
        relation = session.get(IdeaBlockRelation, task.target_id)
        if relation is None:
            return None
        blocks = session.scalars(
            select(IdeaBlock).where(IdeaBlock.id.in_((relation.source_idea_block_id, relation.target_idea_block_id)))
        ).all()
        domains = {block.domain_id for block in blocks}
        return next(iter(domains)) if len(domains) == 1 else None
    return None


def update_review_task(
    session: Session,
    task_id: object,
    *,
    status: str | None = None,
    assigned_to: str | None = None,
    resolution: str | None = None,
    comment: str | None = None,
    actor: str | None = None,
) -> ReviewTask:
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise GovernanceError("review task not found")
    if (
        task.task_type == "governance_contradiction_review"
        and task.target_type == "idea_block_relation"
        and status in {"approved", "rejected", "closed"}
    ):
        raise GovernanceError("contradiction review tasks require the relation-scoped decision operation")
    if status is not None:
        if status not in _REVIEW_STATES:
            raise GovernanceError(f"unknown review status: {status}")
        task.status = status
        if status in {"approved", "rejected", "closed"}:
            task.closed_at = datetime.now(UTC)
    if assigned_to is not None:
        task.assigned_to = assigned_to
    if resolution is not None:
        task.resolution = resolution
    if comment is not None:
        clean_comment = comment.strip()
        if not clean_comment:
            raise GovernanceError("review comment cannot be blank")
        session.add(ReviewTaskComment(review_task_id=task.id, author_id=actor, comment=clean_comment))
    return task


def review_task_to_dict(task: ReviewTask) -> dict[str, object]:
    return {
        "id": str(task.id),
        "task_type": task.task_type,
        "status": task.status,
        "priority": task.priority,
        "target_type": task.target_type,
        "target_id": task.target_id,
        "reason": task.reason,
        "assigned_to": task.assigned_to,
        "recommendation": task.recommendation,
        "evidence": task.evidence or [],
        "resolution": task.resolution,
        "metadata": task.metadata_ or {},
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "closed_at": task.closed_at.isoformat() if task.closed_at else None,
    }
