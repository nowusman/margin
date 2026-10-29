"""Affected-only Entity catalog re-resolution work."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.audit import write_audit_event
from margin_db.entity_models import (
    RESOLVED_MENTION_LINK_ROLE,
    EntityMention,
    EntityReresolutionJob,
    EntityResolutionDecision,
)
from margin_db.knowledge_models import IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.semantic_resolution import SEMANTIC_KEY_VERSION, canonical_semantic_key
from margin_schema.ontology import ONTOLOGY_VERSION


def reresolve_affected_entities(session: Session, *, job: EntityReresolutionJob) -> dict[str, int]:
    """Recompute only the mentions and current revisions named by ``job``."""

    from margin_db.entity_catalog import resolve_mention

    if job.state == "completed":
        return {"mentions": job.affected_mention_count, "revisions": job.affected_revision_count}
    job.state = "running"
    mentions = _affected_mentions(session, job)
    changed_revisions: set[str] = set()
    decision_changes: list[dict[str, str | None]] = []
    protected_human_decisions = 0
    try:
        for mention in mentions:
            changed_revisions.add(mention.idea_block_revision_id)
            decision = session.scalar(
                select(EntityResolutionDecision).where(
                    EntityResolutionDecision.mention_id == mention.id, EntityResolutionDecision.is_current.is_(True)
                )
            )
            if decision is not None and decision.decided_by is not None:
                protected_human_decisions += 1
                continue
            previous_entity_id = decision.entity_id if decision is not None else None
            result = resolve_mention(
                session,
                text=mention.text,
                proposed_type=mention.proposed_type,
                domain_id=mention.domain_id,
                source_element_id=mention.source_element_id,
                idea_block_revision_id=mention.idea_block_revision_id,
                external_id=mention.external_id,
                context=mention.context,
                supplied_candidate_ids=mention.candidate_entity_ids,
            )
            current_entity_id = result.decision.entity_id if result.decision is not None else None
            if previous_entity_id != current_entity_id:
                if previous_entity_id is not None:
                    _remove_unreferenced_resolution_link(session, mention=mention, entity_id=previous_entity_id)
                decision_changes.append(
                    {"mention_id": mention.id, "old_entity_id": previous_entity_id, "new_entity_id": current_entity_id}
                )
        current_revision_ids = _recompute_current_revisions(session, changed_revisions)
        if current_revision_ids:
            from margin_db.search_materialization import enqueue_search_materialization

            enqueue_search_materialization(session, current_revision_ids, reason="approved_entity_link_changed")
        job.state = "completed"
        job.affected_mention_count = len(mentions)
        job.affected_revision_count = len(current_revision_ids)
        job.failure = None
        job.metadata_ = {
            **(job.metadata_ or {}),
            "decision_changes": decision_changes,
            "protected_human_decision_count": protected_human_decisions,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        write_audit_event(
            session,
            actor="system",
            action="entity_reresolution_completed",
            target_type="entity_reresolution_job",
            target_id=job.id,
            before=None,
            after={
                "affected_mentions": len(mentions),
                "affected_revisions": len(current_revision_ids),
                "decision_changes": decision_changes,
                "protected_human_decision_count": protected_human_decisions,
            },
        )
        return {"mentions": len(mentions), "revisions": len(current_revision_ids)}
    except Exception as exc:
        job.state = "failed"
        job.failure = str(exc)
        job.metadata_ = {**(job.metadata_ or {}), "failed_at": datetime.now(UTC).isoformat()}
        raise


def _affected_mentions(session: Session, job: EntityReresolutionJob) -> list[EntityMention]:
    statement = select(EntityMention)
    if job.domain_id is not None:
        statement = statement.where(EntityMention.domain_id == job.domain_id)
    mentions = list(session.scalars(statement.order_by(EntityMention.id)).all())
    changed_ids = set(job.entity_ids or [])
    aliases = set(job.normalized_aliases or [])
    decisions = {
        item.mention_id: item
        for item in session.scalars(
            select(EntityResolutionDecision)
            .where(EntityResolutionDecision.mention_id.in_([mention.id for mention in mentions]))
            .where(EntityResolutionDecision.is_current.is_(True))
        ).all()
    }
    return [
        mention
        for mention in mentions
        if mention.normalized_text in aliases
        or bool(changed_ids.intersection(mention.candidate_entity_ids or []))
        or (decisions.get(mention.id) is not None and decisions[mention.id].entity_id in changed_ids)
    ]


def _recompute_current_revisions(session: Session, revision_ids: Iterable[str]) -> tuple[str, ...]:
    updated: list[str] = []
    for revision_id in sorted(set(revision_ids)):
        revision = session.get(IdeaBlockRevision, revision_id)
        if revision is None:
            continue
        block = session.get(IdeaBlock, revision.idea_block_id)
        if block is None or block.current_revision_id != revision.id:
            continue
        entity_ids = tuple(
            session.scalars(
                select(IdeaBlockEntity.entity_id)
                .where(IdeaBlockEntity.idea_block_id == block.id)
                .order_by(IdeaBlockEntity.entity_id)
            ).all()
        )
        revision.semantic_key = canonical_semantic_key(
            domain_id=block.domain_id,
            normalized_statement=revision.normalized_statement,
            type=revision.type,
            polarity=revision.polarity,
            qualifiers=revision.qualifiers or {},
            valid_from=revision.valid_from,
            valid_to=revision.valid_to,
            resolved_entity_ids=entity_ids,
        )
        revision.semantic_key_version = SEMANTIC_KEY_VERSION
        revision.ontology_version = ONTOLOGY_VERSION
        metadata = dict(revision.metadata_ or {})
        metadata.pop("search_text", None)
        revision.metadata_ = metadata
        updated.append(revision.id)
    return tuple(updated)


def _remove_unreferenced_resolution_link(session: Session, *, mention: EntityMention, entity_id: str) -> None:
    """Remove an automatic link when no current mention still resolves to it."""

    revision = session.get(IdeaBlockRevision, mention.idea_block_revision_id)
    if revision is None:
        return
    retained = session.scalar(
        select(EntityResolutionDecision.id)
        .join(EntityMention, EntityMention.id == EntityResolutionDecision.mention_id)
        .join(IdeaBlockRevision, IdeaBlockRevision.id == EntityMention.idea_block_revision_id)
        .where(
            EntityResolutionDecision.entity_id == entity_id,
            EntityResolutionDecision.is_current.is_(True),
            IdeaBlockRevision.idea_block_id == revision.idea_block_id,
        )
        .limit(1)
    )
    if retained is not None:
        return
    link = session.get(
        IdeaBlockEntity,
        {"idea_block_id": revision.idea_block_id, "entity_id": entity_id, "role": RESOLVED_MENTION_LINK_ROLE},
    )
    if link is not None:
        session.delete(link)
        if revision.publication_status in {"candidate", "verified"}:
            block = session.get(IdeaBlock, revision.idea_block_id)
            if block is not None and block.current_revision_id == revision.id:
                from margin_db.search_materialization import enqueue_search_materialization

                enqueue_search_materialization(session, (revision.id,), reason="approved_entity_link_changed")
