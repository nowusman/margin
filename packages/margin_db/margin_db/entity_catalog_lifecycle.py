"""Atomic lifecycle operations for canonical Entity catalog records."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from margin_db.entity_models import Entity, EntityAlias, EntityMention, EntityResolutionDecision
from margin_db.governance_models import GovernanceRecordEntity
from margin_db.knowledge_models import IdeaBlockEntity
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.review_models import ReviewTask
from margin_schema.ids import relationship_id
from margin_schema.ontology import canonical_relationship_endpoints, validate_relationship_endpoints


def merge_entities(session: Session, *, source: Entity, survivor: Entity, actor_id: str | None, reason: str) -> None:
    """Merge duplicate entities and every direct Entity link in one transaction."""

    from margin_db.entity_catalog import _record_action, _touch_catalog_version, schedule_affected_reresolution

    _validate_merge(source, survivor)
    _assert_governance_mergeable(session, source_id=source.id, survivor_id=survivor.id)
    moved_aliases = _move_aliases(session, source=source, survivor=survivor)
    moved_decisions = _move_resolution_rows(
        session, source_id=source.id, survivor=survivor, actor_id=actor_id, reason=reason
    )
    _move_idea_block_links(session, source_id=source.id, survivor_id=survivor.id)
    collapsed_relationships = _move_relationships(session, source_id=source.id, survivor_id=survivor.id)
    _move_governance_links(session, source_id=source.id, survivor_id=survivor.id)
    _move_review_links(session, source_id=source.id, survivor_id=survivor.id)
    source.lifecycle_status = "superseded"
    source.superseded_by_entity_id = survivor.id
    source.retired_at = datetime.now(UTC)
    _touch_catalog_version(source)
    _touch_catalog_version(survivor)
    _record_action(
        session,
        "merge",
        source.id,
        survivor.id,
        actor_id,
        reason,
        {
            "moved_aliases": moved_aliases,
            "moved_resolution_decision_count": moved_decisions,
            "collapsed_relationship_ids": list(collapsed_relationships),
        },
    )
    session.flush()
    schedule_affected_reresolution(
        session,
        domain_id=survivor.domain_id,
        entity_ids=(source.id, survivor.id),
        normalized_aliases=tuple(dict.fromkeys((source.normalized_label, survivor.normalized_label, *moved_aliases))),
    )


def supersede_entity(session: Session, *, source: Entity, successor: Entity, actor_id: str | None, reason: str) -> None:
    """Mark an Entity superseded and retain the explicit ontology relation."""

    from margin_db.entity_catalog import (
        _record_action,
        _touch_catalog_version,
        _upsert_catalog_relationship,
        schedule_affected_reresolution,
    )

    _validate_supersession(source, successor)
    source.lifecycle_status = "superseded"
    source.superseded_by_entity_id = successor.id
    source.retired_at = datetime.now(UTC)
    _upsert_catalog_relationship(session, source=source, target=successor, relation_type="supersedes_entity")
    _touch_catalog_version(source)
    _touch_catalog_version(successor)
    _record_action(session, "supersede", source.id, successor.id, actor_id, reason)
    session.flush()
    schedule_affected_reresolution(
        session,
        domain_id=successor.domain_id,
        entity_ids=(source.id, successor.id),
        normalized_aliases=(source.normalized_label, successor.normalized_label),
    )


def retire_entity(session: Session, *, entity: Entity, actor_id: str | None, reason: str) -> None:
    """Retire an Entity and its aliases without deleting its evidence history."""

    from margin_db.entity_catalog import _record_action, _touch_catalog_version, schedule_affected_reresolution

    if entity.lifecycle_status == "retired":
        return
    if entity.lifecycle_status not in {"draft", "active"}:
        raise ValueError(f"cannot retire an Entity in {entity.lifecycle_status} lifecycle state")
    entity.lifecycle_status = "retired"
    entity.retired_at = datetime.now(UTC)
    for alias in session.scalars(select(EntityAlias).where(EntityAlias.entity_id == entity.id)).all():
        alias.lifecycle_status = "retired"
    _touch_catalog_version(entity)
    _record_action(session, "retire", entity.id, None, actor_id, reason)
    session.flush()
    schedule_affected_reresolution(
        session, domain_id=entity.domain_id, entity_ids=(entity.id,), normalized_aliases=(entity.normalized_label,)
    )


def _validate_merge(source: Entity, survivor: Entity) -> None:
    from margin_db.entity_catalog import EntityMergeConflict

    if source.id == survivor.id:
        raise EntityMergeConflict(("entity_id",))
    fields: list[str] = []
    if source.external_id and survivor.external_id and source.external_id != survivor.external_id:
        fields.append("external_id")
    if source.ontology_type != survivor.ontology_type:
        fields.append("ontology_type")
    if source.domain_id != survivor.domain_id:
        fields.append("domain_id")
    if source.lifecycle_status != "active":
        fields.append("source_lifecycle_status")
    if survivor.lifecycle_status != "active":
        fields.append("survivor_lifecycle_status")
    if fields:
        raise EntityMergeConflict(fields)


def _validate_supersession(source: Entity, successor: Entity) -> None:
    from margin_db.entity_catalog import EntityMergeConflict

    if source.id == successor.id:
        raise EntityMergeConflict(("entity_id",))
    fields: list[str] = []
    if source.ontology_type != successor.ontology_type:
        fields.append("ontology_type")
    if source.domain_id != successor.domain_id:
        fields.append("domain_id")
    if source.lifecycle_status != "active":
        fields.append("source_lifecycle_status")
    if successor.lifecycle_status != "active":
        fields.append("successor_lifecycle_status")
    if fields:
        raise EntityMergeConflict(fields)


def _assert_governance_mergeable(session: Session, *, source_id: str, survivor_id: str) -> None:
    from margin_db.entity_catalog import EntityMergeConflict

    source_links = session.scalars(
        select(GovernanceRecordEntity).where(GovernanceRecordEntity.entity_id == source_id)
    ).all()
    survivor_links = {
        (link.governance_record_id, link.role)
        for link in session.scalars(
            select(GovernanceRecordEntity).where(GovernanceRecordEntity.entity_id == survivor_id)
        ).all()
    }
    blockers = [
        f"{link.governance_record_id}:{link.role}"
        for link in source_links
        if (link.governance_record_id, link.role) in survivor_links
    ]
    if blockers:
        raise EntityMergeConflict(tuple(f"governance_record:{value}" for value in blockers))


def _move_aliases(session: Session, *, source: Entity, survivor: Entity) -> tuple[str, ...]:
    moved: list[str] = []
    aliases = list(session.scalars(select(EntityAlias).where(EntityAlias.entity_id == source.id)).all())
    for alias in aliases:
        duplicate = session.scalar(
            select(EntityAlias).where(
                EntityAlias.entity_id == survivor.id,
                EntityAlias.normalized_alias == alias.normalized_alias,
                EntityAlias.locale == alias.locale,
            )
        )
        if duplicate is None:
            alias.entity_id = survivor.id
            alias.ontology_type = survivor.ontology_type
            alias.domain_id = survivor.domain_id
            moved.append(alias.normalized_alias)
        else:
            session.delete(alias)
    return tuple(moved)


def _move_resolution_rows(
    session: Session, *, source_id: str, survivor: Entity, actor_id: str | None, reason: str
) -> int:
    from margin_db.entity_catalog import _record_resolution_decision

    moved = 0
    current_decisions = list(
        session.scalars(
            select(EntityResolutionDecision).where(
                EntityResolutionDecision.entity_id == source_id, EntityResolutionDecision.is_current.is_(True)
            )
        ).all()
    )
    for decision in current_decisions:
        mention = session.get(EntityMention, decision.mention_id)
        if mention is None:
            continue
        mention.candidate_entity_ids = list(
            dict.fromkeys(
                survivor.id if value == source_id else value for value in (mention.candidate_entity_ids or [])
            )
        )
        _record_resolution_decision(
            session,
            mention=mention,
            entity=survivor,
            outcome="resolved",
            method="entity_merge",
            confidence=decision.confidence,
            candidate_ids=mention.candidate_entity_ids,
            rationale=f"{reason} (merged canonical Entity {source_id} into {survivor.id})",
            actor_id=actor_id,
        )
        moved += 1
    for mention in session.scalars(select(EntityMention)).all():
        if source_id in (mention.candidate_entity_ids or []):
            mention.candidate_entity_ids = list(
                dict.fromkeys(survivor.id if value == source_id else value for value in mention.candidate_entity_ids)
            )
    return moved


def _move_idea_block_links(session: Session, *, source_id: str, survivor_id: str) -> None:
    links = list(session.scalars(select(IdeaBlockEntity).where(IdeaBlockEntity.entity_id == source_id)).all())
    for link in links:
        duplicate = session.get(
            IdeaBlockEntity, {"idea_block_id": link.idea_block_id, "entity_id": survivor_id, "role": link.role}
        )
        if duplicate is None:
            link.entity_id = survivor_id
        else:
            session.delete(link)


def _move_relationships(session: Session, *, source_id: str, survivor_id: str) -> tuple[str, ...]:
    collapsed: list[str] = []
    relationships = list(
        session.scalars(
            select(EntityRelationship).where(
                or_(EntityRelationship.source_entity_id == source_id, EntityRelationship.target_entity_id == source_id)
            )
        ).all()
    )
    for relationship in relationships:
        source_endpoint = survivor_id if relationship.source_entity_id == source_id else relationship.source_entity_id
        target_endpoint = survivor_id if relationship.target_entity_id == source_id else relationship.target_entity_id
        source_endpoint, target_endpoint = canonical_relationship_endpoints(
            relationship.relation_type, source_endpoint, target_endpoint
        )
        if source_endpoint == target_endpoint:
            _close_collapsed_relationship_reviews(session, relationship.id)
            session.execute(
                delete(RelationshipProvenance).where(RelationshipProvenance.relationship_id == relationship.id)
            )
            session.delete(relationship)
            collapsed.append(relationship.id)
            continue
        source_entity = session.get(Entity, source_endpoint)
        target_entity = session.get(Entity, target_endpoint)
        if source_entity is None or target_entity is None:
            raise ValueError("entity merge found an orphan relationship endpoint")
        validate_relationship_endpoints(
            relationship.relation_type, source_entity.ontology_type, target_entity.ontology_type
        )
        duplicate = session.scalar(
            select(EntityRelationship).where(
                EntityRelationship.source_entity_id == source_endpoint,
                EntityRelationship.target_entity_id == target_endpoint,
                EntityRelationship.relation_type == relationship.relation_type,
                EntityRelationship.id != relationship.id,
            )
        )
        if duplicate is None:
            duplicate = EntityRelationship(
                id=relationship_id(
                    {
                        "source_entity_id": source_endpoint,
                        "target_entity_id": target_endpoint,
                        "relation_type": relationship.relation_type,
                    }
                ),
                source_entity_id=source_endpoint,
                target_entity_id=target_endpoint,
                relation_type=relationship.relation_type,
                ontology_version=relationship.ontology_version,
                confidence=relationship.confidence,
                status=relationship.status,
                valid_from=relationship.valid_from,
                valid_to=relationship.valid_to,
                metadata_={**(relationship.metadata_ or {}), "merged_from_relationship_id": relationship.id},
            )
            session.add(duplicate)
            session.flush()
        session.execute(
            update(RelationshipProvenance)
            .where(RelationshipProvenance.relationship_id == relationship.id)
            .values(relationship_id=duplicate.id)
        )
        session.execute(
            update(ReviewTask)
            .where(ReviewTask.target_type == "entity_relationship", ReviewTask.target_id == relationship.id)
            .values(target_id=duplicate.id)
        )
        session.delete(relationship)
    return tuple(collapsed)


def _close_collapsed_relationship_reviews(session: Session, relationship_id_value: str) -> None:
    session.execute(
        update(ReviewTask)
        .where(ReviewTask.target_type == "entity_relationship", ReviewTask.target_id == relationship_id_value)
        .values(
            status="closed", resolution="relationship collapsed by canonical Entity merge", closed_at=datetime.now(UTC)
        )
    )


def _move_governance_links(session: Session, *, source_id: str, survivor_id: str) -> None:
    links = list(
        session.scalars(select(GovernanceRecordEntity).where(GovernanceRecordEntity.entity_id == source_id)).all()
    )
    for link in links:
        duplicate = session.get(
            GovernanceRecordEntity,
            {"governance_record_id": link.governance_record_id, "entity_id": survivor_id, "role": link.role},
        )
        if duplicate is None:
            link.entity_id = survivor_id
        else:
            session.delete(link)


def _move_review_links(session: Session, *, source_id: str, survivor_id: str) -> None:
    session.execute(
        update(ReviewTask)
        .where(ReviewTask.target_type == "entity", ReviewTask.target_id == source_id)
        .values(target_id=survivor_id)
    )
