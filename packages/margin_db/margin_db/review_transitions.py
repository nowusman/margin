"""Separate review transition functions for IdeaBlocks, relationships, and entities.

Relationship review:
- IdeaBlock, relationship, and entity review are routed through separate
  transition functions with separate allowed-state maps and audit events.
- A transition only mutates the targeted object and its own provenance
  rows. It never mutates a different object.
- Approving an IdeaBlock does not automatically verify inferred
  relationships.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.audit import write_audit_event
from margin_db.idea_block_resolution import (
    create_idea_block_revision,
    current_idea_block_revision,
    upsert_idea_block_relation,
)
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.relationship_models import RelationshipProvenance
from margin_db.review_models import ReviewTask, ReviewTaskComment
from margin_db.semantic_resolution import SEMANTIC_RESOLUTION_TASK_TYPE, compare_protected_fields


class ReviewTransitionError(ValueError):
    """Raised when a review transition cannot be applied."""


@dataclass(frozen=True)
class ReviewAuditEvent:
    """A small audit event for a review transition.

    Captured separately from the DB transaction so a transition is
    inspectable from logs and tests without re-querying state.
    """

    target_type: str
    target_id: str
    actor: str
    from_status: str | None
    to_status: str
    note: str
    recorded_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_type": self.target_type,
            "target_id": self.target_id,
            "actor": self.actor,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "note": self.note,
            "recorded_at": self.recorded_at.isoformat(),
        }


_IDEA_BLOCK_ALLOWED: dict[str, set[str]] = {
    "candidate": {"verified", "rejected", "superseded"},
    "verified": {"rejected", "superseded"},
    "rejected": {"candidate", "superseded"},
    "superseded": {"candidate"},
}


def transition_idea_block_status(
    session: Session, *, idea_block_id: str, target_status: str, actor: str = "system", note: str = ""
) -> tuple[IdeaBlock, ReviewAuditEvent]:
    """Apply a review transition to an IdeaBlock without touching anything else.

    The transition validates against the IdeaBlock allowed-state map, sets
    the new status, records an audit event, and closes any open review
    tasks targeting the IdeaBlock. It never mutates relationships or
    entities.
    """
    block = session.get(IdeaBlock, idea_block_id)
    if block is None:
        raise ReviewTransitionError(f"idea block not found: {idea_block_id}")
    current_revision = current_idea_block_revision(session, block)
    allowed = _IDEA_BLOCK_ALLOWED.get(current_revision.publication_status, set())
    if target_status not in allowed:
        raise ReviewTransitionError(
            f"idea block {idea_block_id} cannot transition from "
            f"{current_revision.publication_status} to {target_status}"
        )
    from_status = current_revision.publication_status
    create_idea_block_revision(
        session,
        block,
        statement=current_revision.statement,
        normalized_statement=current_revision.normalized_statement,
        type=current_revision.type,
        confidence=current_revision.confidence,
        polarity=current_revision.polarity,
        qualifiers=current_revision.qualifiers,
        valid_from=current_revision.valid_from,
        valid_to=current_revision.valid_to,
        publication_status=target_status,
        actor=actor,
        process="review_transition",
        change_reason=note or f"review transitioned {from_status} -> {target_status}",
        metadata=current_revision.metadata_,
        ontology_version=current_revision.ontology_version,
    )
    event = ReviewAuditEvent(
        target_type="idea_block",
        target_id=idea_block_id,
        actor=actor,
        from_status=from_status,
        to_status=target_status,
        note=note or f"IdeaBlock review transitioned {from_status} -> {target_status}",
        recorded_at=datetime.now(UTC),
    )
    write_audit_event(
        session,
        actor=actor,
        action="idea_block_review_transition",
        target_type="idea_block",
        target_id=idea_block_id,
        before={"status": from_status},
        after={"status": target_status, "note": event.note},
    )
    _close_open_review_tasks(session, target_type="idea_block", target_id=idea_block_id)
    session.flush()
    return block, event


SEMANTIC_REVIEW_ACTIONS = frozenset(
    {"confirm_equivalent", "approve_distinct", "mark_refinement", "mark_contradiction", "mark_supersession"}
)

_SEMANTIC_RELATIONS = {
    "mark_refinement": "refines",
    "mark_contradiction": "contradicts",
    "mark_supersession": "supersedes",
}


@dataclass(frozen=True)
class SemanticReviewActionResult:
    task: ReviewTask
    action: str
    survivor_id: str | None = None
    relation_id: str | None = None


def apply_semantic_review_action(
    session: Session,
    *,
    task_id: Any,
    action: str,
    actor: str = "system",
    note: str,
    expected_updated_at: datetime | None = None,
    survivor_id: str | None = None,
    source_idea_block_id: str | None = None,
    target_idea_block_id: str | None = None,
) -> SemanticReviewActionResult:
    """Apply one authorized semantic decision as one atomic review transition."""
    if action not in SEMANTIC_REVIEW_ACTIONS:
        raise ReviewTransitionError(f"unsupported semantic review action: {action}")
    if not note.strip():
        raise ReviewTransitionError("semantic review actions require a resolution note")
    task = session.scalar(select(ReviewTask).where(ReviewTask.id == task_id).with_for_update())
    if task is None:
        raise ReviewTransitionError(f"review task not found: {task_id}")
    if task.task_type != SEMANTIC_RESOLUTION_TASK_TYPE or task.target_type != "idea_block_pair":
        raise ReviewTransitionError("review task is not a semantic resolution task")
    if task.status not in {"open", "assigned"}:
        raise ReviewTransitionError(f"review task is stale or already resolved: {task.status}")
    if expected_updated_at is not None and not _same_timestamp(task.updated_at, expected_updated_at):
        raise ReviewTransitionError("review task is stale; reload the review bundle before deciding")
    pair = _semantic_pair_ids(session, task)
    if pair is None:
        raise ReviewTransitionError("semantic review task does not identify exactly two IdeaBlocks")
    block_rows = session.execute(
        select(IdeaBlock, IdeaBlockRevision)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(IdeaBlock.id.in_(pair))
        .with_for_update()
    ).all()
    blocks = {block.id: block for block, _revision in block_rows}
    revisions = {block.id: revision for block, revision in block_rows}
    if len(blocks) != 2:
        raise ReviewTransitionError("semantic review task references a missing IdeaBlock")
    if len({block.domain_id for block in blocks.values()}) != 1:
        raise ReviewTransitionError("semantic review task crosses semantic governance scopes")
    if any(block.merged_into_id or block.archived_at for block in blocks.values()):
        raise ReviewTransitionError("semantic review task is stale because an IdeaBlock was already archived")
    if any(revision.publication_status not in {"candidate", "verified"} for revision in revisions.values()):
        raise ReviewTransitionError("semantic review task is stale because an IdeaBlock revision is no longer current")
    _validate_semantic_revision_snapshot(task, pair, blocks, revisions)
    before = {"status": task.status, "idea_block_ids": list(pair), "action": action}
    relation_id: str | None = None
    selected_survivor: str | None = None
    if action == "confirm_equivalent":
        selected_survivor = survivor_id
        if selected_survivor not in pair:
            raise ReviewTransitionError("confirm_equivalent requires one of the two IdeaBlocks as survivor")
        relation_id = _confirm_equivalence(
            session,
            survivor_id=selected_survivor,
            duplicate_id=pair[1] if pair[0] == selected_survivor else pair[0],
            task=task,
            actor=actor,
            note=note,
        )
    elif action == "approve_distinct":
        if source_idea_block_id is not None or target_idea_block_id is not None:
            raise ReviewTransitionError("approve_distinct does not accept relation endpoints")
    else:
        source, target = _relation_endpoints(
            pair,
            source_idea_block_id=source_idea_block_id,
            target_idea_block_id=target_idea_block_id,
            symmetric=action == "mark_contradiction",
        )
        relation = upsert_idea_block_relation(
            session,
            source_idea_block_id=source,
            target_idea_block_id=target,
            relation_type=_SEMANTIC_RELATIONS[action],
            status="verified",
            confidence=Decimal("1"),
            decision_provenance={"action": action, "actor": actor, "review_task_id": str(task.id), "note": note},
        )
        relation_id = relation.id

    now = datetime.now(UTC)
    task.status = "approved"
    task.resolution = note.strip()
    task.closed_at = now
    task.updated_at = now
    task.metadata_ = {
        **dict(task.metadata_ or {}),
        "decision_provenance": {"action": action, "actor": actor, "review_task_id": str(task.id)},
    }
    session.add(
        ReviewTaskComment(review_task_id=task.id, author_id=actor if actor != "system" else None, comment=note.strip())
    )
    _close_open_semantic_pair_tasks(session, pair, exclude_task_id=task.id)
    write_audit_event(
        session,
        actor=actor,
        action=f"semantic_review.{action}",
        target_type="review_task",
        target_id=str(task.id),
        before=before,
        after={
            "status": task.status,
            "survivor_id": selected_survivor,
            "relation_id": relation_id,
            "resolution": task.resolution,
        },
    )
    session.flush()
    return SemanticReviewActionResult(task=task, action=action, survivor_id=selected_survivor, relation_id=relation_id)


def _semantic_pair_ids(session: Session, task: ReviewTask) -> tuple[str, str] | None:
    if task.target_type != "idea_block_pair":
        return None
    metadata_ids = (task.metadata_ or {}).get("idea_block_ids")
    if (
        isinstance(metadata_ids, list)
        and len(metadata_ids) == 2
        and all(isinstance(item, str) for item in metadata_ids)
    ):
        return str(metadata_ids[0]), str(metadata_ids[1])
    blocks = list(session.scalars(select(IdeaBlock)).all())
    for left in blocks:
        for right in blocks:
            if left.id != right.id and f"{left.id}:{right.id}" == task.target_id:
                return left.id, right.id
    return None


def _same_timestamp(left: datetime | None, right: datetime) -> bool:
    if left is None:
        return False
    left_value = left if left.tzinfo is not None else left.replace(tzinfo=UTC)
    right_value = right if right.tzinfo is not None else right.replace(tzinfo=UTC)
    return left_value == right_value


def _validate_semantic_revision_snapshot(
    task: ReviewTask, pair: tuple[str, str], blocks: dict[str, IdeaBlock], revisions: dict[str, IdeaBlockRevision]
) -> None:
    metadata = task.metadata_ or {}
    expected = metadata.get("idea_block_revision_ids")
    if isinstance(expected, dict):
        expected_ids = {str(block_id): str(revision_id) for block_id, revision_id in expected.items()}
        current_ids = {block_id: revisions[block_id].id for block_id in pair}
        if expected_ids != current_ids:
            raise ReviewTransitionError("review task is stale because an IdeaBlock revision changed")
        return
    if task.created_at is not None and any(
        block.updated_at is not None and block.updated_at > task.created_at for block in blocks.values()
    ):
        raise ReviewTransitionError("review task is stale because an IdeaBlock changed")


def _relation_endpoints(
    pair: tuple[str, str], *, source_idea_block_id: str | None, target_idea_block_id: str | None, symmetric: bool
) -> tuple[str, str]:
    if symmetric:
        ordered = sorted(pair)
        return ordered[0], ordered[1]
    if source_idea_block_id not in pair or target_idea_block_id not in pair:
        raise ReviewTransitionError("this semantic action requires both IdeaBlock relation endpoints")
    if source_idea_block_id == target_idea_block_id:
        raise ReviewTransitionError("IdeaBlock relation endpoints must be distinct")
    return source_idea_block_id, target_idea_block_id


def _idea_block_entity_ids(session: Session, idea_block_id: str) -> tuple[str, ...]:
    return tuple(
        session.scalars(
            select(IdeaBlockEntity.entity_id)
            .where(IdeaBlockEntity.idea_block_id == idea_block_id)
            .order_by(IdeaBlockEntity.entity_id)
        ).all()
    )


def _confirm_equivalence(
    session: Session, *, survivor_id: str, duplicate_id: str, task: ReviewTask, actor: str, note: str
) -> str:
    survivor = session.get(IdeaBlock, survivor_id)
    duplicate = session.get(IdeaBlock, duplicate_id)
    if survivor is None or duplicate is None:
        raise ReviewTransitionError("equivalence review references a missing IdeaBlock")
    if duplicate.merged_into_id and duplicate.merged_into_id != survivor_id:
        raise ReviewTransitionError("duplicate IdeaBlock is already merged into another survivor")
    survivor_revision = current_idea_block_revision(session, survivor)
    duplicate_revision = current_idea_block_revision(session, duplicate)
    comparison = compare_protected_fields(
        survivor_revision,
        duplicate_revision,
        left_domain_id=survivor.domain_id,
        right_domain_id=duplicate.domain_id,
        left_entity_ids=_idea_block_entity_ids(session, survivor_id),
        right_entity_ids=_idea_block_entity_ids(session, duplicate_id),
    )
    if comparison.guard_conflicts:
        raise ReviewTransitionError(
            "protected semantic differences block equivalence; choose a distinct semantic review action"
        )
    evidence_keys = {
        (item.source_element_id, item.quote_char_start, item.quote_char_end, item.polarity, item.extraction_run_id)
        for item in session.scalars(select(ClaimEvidence).where(ClaimEvidence.idea_block_id == survivor_id)).all()
    }
    duplicate_evidence = list(
        session.scalars(select(ClaimEvidence).where(ClaimEvidence.idea_block_id == duplicate_id)).all()
    )
    for item in duplicate_evidence:
        key = (
            item.source_element_id,
            item.quote_char_start,
            item.quote_char_end,
            item.polarity,
            item.extraction_run_id,
        )
        if key in evidence_keys:
            continue
        item.idea_block_id = survivor_id
        item.idea_block_revision_id = survivor_revision.id
        evidence_keys.add(key)
    for provenance in session.scalars(
        select(RelationshipProvenance).where(RelationshipProvenance.idea_block_id == duplicate_id)
    ):
        provenance.idea_block_id = survivor_id
    survivor_entities = {
        (item.entity_id, item.role)
        for item in session.scalars(select(IdeaBlockEntity).where(IdeaBlockEntity.idea_block_id == survivor_id)).all()
    }
    duplicate_entities = list(
        session.scalars(select(IdeaBlockEntity).where(IdeaBlockEntity.idea_block_id == duplicate_id)).all()
    )
    for item in duplicate_entities:
        if (item.entity_id, item.role) not in survivor_entities:
            session.add(IdeaBlockEntity(idea_block_id=survivor_id, entity_id=item.entity_id, role=item.role))
    if duplicate_revision.publication_status != "duplicate":
        create_idea_block_revision(
            session,
            duplicate,
            statement=duplicate_revision.statement,
            normalized_statement=duplicate_revision.normalized_statement,
            type=duplicate_revision.type,
            confidence=duplicate_revision.confidence,
            polarity=duplicate_revision.polarity,
            qualifiers=duplicate_revision.qualifiers or {},
            valid_from=duplicate_revision.valid_from,
            valid_to=duplicate_revision.valid_to,
            publication_status="duplicate",
            actor=actor,
            process="semantic_review",
            change_reason=note.strip(),
            metadata={
                **dict(duplicate_revision.metadata_ or {}),
                "merged_into_id": survivor_id,
                "review_task_id": str(task.id),
            },
            ontology_version=duplicate_revision.ontology_version,
            semantic_key=duplicate_revision.semantic_key,
            semantic_key_version=duplicate_revision.semantic_key_version,
        )
    now = datetime.now(UTC)
    duplicate.merged_into_id = survivor_id
    duplicate.archived_at = now
    duplicate.archive_reason = note.strip()
    duplicate.tombstoned_at = now
    duplicate.tombstone_reason = f"confirmed equivalent to {survivor_id}"
    relation = upsert_idea_block_relation(
        session,
        source_idea_block_id=survivor_id,
        target_idea_block_id=duplicate_id,
        relation_type="equivalent",
        status="verified",
        confidence=Decimal("1"),
        decision_provenance={
            "action": "confirm_equivalent",
            "actor": actor,
            "review_task_id": str(task.id),
            "note": note.strip(),
        },
    )
    return relation.id


def _close_open_review_tasks(session: Session, *, target_type: str, target_id: str) -> None:
    for task in session.scalars(
        select(ReviewTask).where(
            ReviewTask.target_type == target_type,
            ReviewTask.target_id == target_id,
            ReviewTask.status.in_(("open", "assigned", "needs_evidence")),
        )
    ):
        task.status = "closed"
        task.resolution = f"Closed because {target_type} {target_id} changed."

    if target_type != "idea_block":
        return
    _close_open_semantic_pair_tasks(session, (target_id,), exclude_task_id=None)


def _close_open_semantic_pair_tasks(
    session: Session, idea_block_ids: tuple[str, ...], *, exclude_task_id: Any | None
) -> None:
    target_ids = set(idea_block_ids)
    for task in session.scalars(
        select(ReviewTask).where(
            ReviewTask.task_type == SEMANTIC_RESOLUTION_TASK_TYPE,
            ReviewTask.target_type == "idea_block_pair",
            ReviewTask.status.in_(("open", "assigned", "needs_evidence")),
        )
    ):
        if exclude_task_id is not None and task.id == exclude_task_id:
            continue
        task_ids = set((task.metadata_ or {}).get("idea_block_ids", ()))
        if not task_ids:
            pair = _semantic_pair_ids(session, task)
            task_ids = set(pair) if pair is not None else set()
        if target_ids.intersection(task_ids):
            task.status = "closed"
            task.resolution = "Closed because a reviewed IdeaBlock changed."
