"""Bounded, authorized post-retrieval traversal over explicit relations."""

from __future__ import annotations

from time import perf_counter

from sqlalchemy import and_, case, exists, false, or_, select
from sqlalchemy.orm import Session, aliased

from margin_db.entity_models import Entity
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRelation, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_retrieval.candidates import FusedCandidate, authorized_current_revisions_for_block_ids
from margin_retrieval.interfaces import ExpansionEnvelope, RetrievalExpansion, RetrievalScope
from margin_retrieval.policy import (
    IDEA_BLOCK_EXPANSION_RELATION_TYPES,
    RETRIEVAL_EXPANSION_DEADLINE_MS,
    RETRIEVAL_EXPANSION_LIMIT,
)
from margin_retrieval.support import exclusive_date_to
from margin_schema.ontology import ENTITY_RELATIONSHIP_TYPES, RELATIONSHIP_REGISTRY


def expand_results(
    session: Session,
    *,
    seed_block_ids: tuple[str, ...],
    scope: RetrievalScope,
    expansion: RetrievalExpansion,
    deadline_ms: float = RETRIEVAL_EXPANSION_DEADLINE_MS,
) -> tuple[tuple[FusedCandidate, ...], ExpansionEnvelope]:
    """Traverse only verified explicit edges after ranked candidates are fixed.

    The deadline bounds how long the traversal may take. It is a parameter so
    that a caller measuring correctness rather than latency can state the
    budget it is willing to wait.
    """

    if expansion.depth == 0:
        return (), ExpansionEnvelope()

    started = perf_counter()
    relation_types = set(expansion.relation_types)
    block_relation_types = tuple(sorted(relation_types & IDEA_BLOCK_EXPANSION_RELATION_TYPES))
    entity_relation_types = tuple(sorted(relation_types & ENTITY_RELATIONSHIP_TYPES))
    visited = set(seed_block_ids)
    frontier = set(seed_block_ids)
    candidates: list[FusedCandidate] = []
    status = "completed"

    for _depth in range(expansion.depth):
        if _deadline_exceeded(started, deadline_ms):
            status = "deadline_exceeded"
            break
        adjacent = _adjacent_blocks(
            session,
            block_ids=tuple(sorted(frontier)),
            block_relation_types=block_relation_types,
            entity_relation_types=entity_relation_types,
            scope=scope,
        )
        if _deadline_exceeded(started, deadline_ms):
            status = "deadline_exceeded"
            break
        adjacent.difference_update(visited)
        if not adjacent:
            break
        readable = authorized_current_revisions_for_block_ids(session, block_ids=adjacent, scope=scope)
        if _deadline_exceeded(started, deadline_ms):
            status = "deadline_exceeded"
            break
        frontier = set(readable)
        visited.update(frontier)
        for block_id in sorted(frontier):
            if len(candidates) == expansion.limit:
                break
            candidates.append(
                FusedCandidate(
                    revision_id=readable[block_id], score=0.0, channel_ranks={"expansion": len(candidates) + 1}
                )
            )
        if len(candidates) == expansion.limit:
            break

    return (
        tuple(candidates),
        ExpansionEnvelope(status=status, relation_types=tuple(sorted(relation_types)), depth=expansion.depth),
    )


def _adjacent_blocks(
    session: Session,
    *,
    block_ids: tuple[str, ...],
    block_relation_types: tuple[str, ...],
    entity_relation_types: tuple[str, ...],
    scope: RetrievalScope,
) -> set[str]:
    values: set[str] = set()
    if block_relation_types:
        query = (
            select(IdeaBlockRelation.source_idea_block_id, IdeaBlockRelation.target_idea_block_id)
            .where(IdeaBlockRelation.status == "verified")
            .where(IdeaBlockRelation.relation_type.in_(block_relation_types))
            .where(
                or_(
                    IdeaBlockRelation.source_idea_block_id.in_(block_ids),
                    IdeaBlockRelation.target_idea_block_id.in_(block_ids),
                )
            )
            .where(_verified_idea_block_endpoint(IdeaBlockRelation.source_idea_block_id))
            .where(_verified_idea_block_endpoint(IdeaBlockRelation.target_idea_block_id))
            .where(IdeaBlockRelation.decision_provenance != {})
        )
        if scope.date_from:
            query = query.where(
                or_(IdeaBlockRelation.valid_to.is_(None), IdeaBlockRelation.valid_to >= scope.date_from)
            )
        if scope.date_to:
            query = query.where(
                or_(
                    IdeaBlockRelation.valid_from.is_(None),
                    IdeaBlockRelation.valid_from < exclusive_date_to(scope.date_to),
                )
            )
        rows = session.execute(
            query.order_by(IdeaBlockRelation.created_at.asc(), IdeaBlockRelation.id.asc()).limit(
                RETRIEVAL_EXPANSION_LIMIT
            )
        ).all()
        for source_id, target_id in rows:
            values.update((source_id, target_id))
    if entity_relation_types:
        values.update(_entity_adjacent_blocks(session, block_ids, entity_relation_types, scope))
    return values


def _entity_adjacent_blocks(
    session: Session, block_ids: tuple[str, ...], relation_types: tuple[str, ...], scope: RetrievalScope
) -> set[str]:
    source_entities = (
        select(IdeaBlockEntity.entity_id)
        .join(IdeaBlock, IdeaBlock.id == IdeaBlockEntity.idea_block_id)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .join(Entity, Entity.id == IdeaBlockEntity.entity_id)
        .where(
            IdeaBlock.id.in_(block_ids),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
            IdeaBlockRevision.publication_status == "verified",
            Entity.lifecycle_status == "active",
            or_(Entity.domain_id.is_(None), Entity.domain_id == IdeaBlock.domain_id),
        )
    )
    relationship_scope = [
        EntityRelationship.status == "verified",
        EntityRelationship.relation_type.in_(relation_types),
        _active_endpoint_filter(),
        _current_verified_provenance_filter(scope, relation_types),
    ]
    if scope.date_from:
        relationship_scope.append(
            or_(EntityRelationship.valid_to.is_(None), EntityRelationship.valid_to >= scope.date_from)
        )
    if scope.date_to:
        relationship_scope.append(
            or_(
                EntityRelationship.valid_from.is_(None),
                EntityRelationship.valid_from < exclusive_date_to(scope.date_to),
            )
        )
    related_entities = set(
        session.scalars(
            select(
                case(
                    (EntityRelationship.source_entity_id.in_(source_entities), EntityRelationship.target_entity_id),
                    else_=EntityRelationship.source_entity_id,
                )
            )
            .where(
                *relationship_scope,
                or_(
                    EntityRelationship.source_entity_id.in_(source_entities),
                    EntityRelationship.target_entity_id.in_(source_entities),
                ),
            )
            .order_by(EntityRelationship.created_at.asc(), EntityRelationship.id.asc())
            .limit(RETRIEVAL_EXPANSION_LIMIT)
        )
    )
    if not related_entities:
        return set()
    return set(
        session.scalars(
            select(IdeaBlockEntity.idea_block_id)
            .join(IdeaBlock, IdeaBlock.id == IdeaBlockEntity.idea_block_id)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .join(Entity, Entity.id == IdeaBlockEntity.entity_id)
            .where(IdeaBlockEntity.entity_id.in_(related_entities))
            .where(IdeaBlockEntity.idea_block_id.not_in(block_ids))
            .where(
                IdeaBlock.merged_into_id.is_(None),
                IdeaBlock.tombstoned_at.is_(None),
                IdeaBlock.archived_at.is_(None),
                IdeaBlockRevision.publication_status == "verified",
                Entity.lifecycle_status == "active",
                or_(Entity.domain_id.is_(None), Entity.domain_id == IdeaBlock.domain_id),
            )
            .order_by(IdeaBlockEntity.idea_block_id.asc(), IdeaBlockEntity.entity_id.asc())
            .limit(RETRIEVAL_EXPANSION_LIMIT)
        )
    )


def _active_endpoint_filter():
    return and_(
        exists(
            select(Entity.id).where(
                Entity.id == EntityRelationship.source_entity_id, Entity.lifecycle_status == "active"
            )
        ),
        exists(
            select(Entity.id).where(
                Entity.id == EntityRelationship.target_entity_id, Entity.lifecycle_status == "active"
            )
        ),
    )


def _verified_idea_block_endpoint(endpoint_column):
    return (
        select(IdeaBlock.id)
        .join(IdeaBlockRevision, IdeaBlockRevision.id == IdeaBlock.current_revision_id)
        .where(IdeaBlock.id == endpoint_column)
        .where(
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
            IdeaBlockRevision.idea_block_id == IdeaBlock.id,
            IdeaBlockRevision.publication_status == "verified",
        )
        .exists()
    )


def _current_verified_provenance_filter(scope: RetrievalScope, relation_types: tuple[str, ...]):
    direct = exists(_provenance_query(scope, EntityRelationship.id)).correlate(EntityRelationship)
    inverse_types = tuple(
        (relation_type, RELATIONSHIP_REGISTRY[relation_type].inverse)
        for relation_type in relation_types
        if relation_type in RELATIONSHIP_REGISTRY and RELATIONSHIP_REGISTRY[relation_type].inverse
    )
    if not inverse_types:
        return direct

    original = aliased(EntityRelationship)
    inverse_query = _provenance_query(scope, original.id).join(
        original, original.id == EntityRelationship.metadata_["inverse_of"].as_string()
    )
    inverse_query = inverse_query.where(
        EntityRelationship.metadata_["derived"].as_boolean().is_(True),
        original.status == "verified",
        original.source_entity_id == EntityRelationship.target_entity_id,
        original.target_entity_id == EntityRelationship.source_entity_id,
        or_(
            *(
                and_(EntityRelationship.relation_type == relation_type, original.relation_type == inverse_type)
                for relation_type, inverse_type in inverse_types
            )
        ),
    )
    if scope.date_from:
        inverse_query = inverse_query.where(or_(original.valid_to.is_(None), original.valid_to >= scope.date_from))
    if scope.date_to:
        inverse_query = inverse_query.where(
            or_(original.valid_from.is_(None), original.valid_from < exclusive_date_to(scope.date_to))
        )
    return or_(direct, exists(inverse_query).correlate(EntityRelationship))


def _provenance_query(scope: RetrievalScope, relationship_id):
    if not scope.domain_ids and not scope.all_domain_ids_authorized:
        return select(RelationshipProvenance.id).where(false())
    if not scope.sensitivities:
        return select(RelationshipProvenance.id).where(false())
    category_entity = aliased(Entity)
    query = (
        select(RelationshipProvenance.id)
        .select_from(RelationshipProvenance)
        .join(SourceElement, SourceElement.id == RelationshipProvenance.source_element_id)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .join(
            category_entity,
            (category_entity.id == SourceDocument.category_id) & (category_entity.ontology_type == "category"),
        )
        .join(IdeaBlock, IdeaBlock.id == RelationshipProvenance.idea_block_id)
        .join(IdeaBlockRevision, IdeaBlockRevision.id == IdeaBlock.current_revision_id)
        .join(
            ClaimEvidence,
            and_(
                ClaimEvidence.idea_block_id == RelationshipProvenance.idea_block_id,
                ClaimEvidence.idea_block_revision_id == IdeaBlockRevision.id,
                ClaimEvidence.source_element_id == RelationshipProvenance.source_element_id,
                ClaimEvidence.source_quote == RelationshipProvenance.source_quote,
                ClaimEvidence.polarity == RelationshipProvenance.polarity,
                ClaimEvidence.governance_record_revision_id.is_(None),
            ),
        )
        .where(RelationshipProvenance.relationship_id == relationship_id)
        .where(RelationshipProvenance.status == "verified")
        .where(SourceDocumentVersion.status == "active")
        .where(category_entity.lifecycle_status == "active")
        .where(IdeaBlockRevision.publication_status == "verified")
        .where(IdeaBlockRevision.idea_block_id == IdeaBlock.id)
        .where(IdeaBlock.merged_into_id.is_(None), IdeaBlock.tombstoned_at.is_(None), IdeaBlock.archived_at.is_(None))
        .where(ClaimEvidence.status == "active")
        .where(SourceDocument.domain_id == IdeaBlock.domain_id)
    )
    if scope.domain_ids:
        query = query.where(SourceDocument.domain_id.in_(scope.domain_ids))
    if scope.all_category_ids_authorized:
        pass
    elif scope.category_ids:
        query = query.where(SourceDocument.category_id.in_(scope.category_ids))
    else:
        return select(RelationshipProvenance.id).where(false())
    if scope.sensitivities:
        query = query.where(SourceDocument.sensitivity.in_(scope.sensitivities))
    if scope.source_document_ids:
        query = query.where(SourceDocument.id.in_(scope.source_document_ids))
    if scope.date_from:
        query = query.where(
            or_(RelationshipProvenance.valid_to.is_(None), RelationshipProvenance.valid_to >= scope.date_from),
            or_(ClaimEvidence.valid_to.is_(None), ClaimEvidence.valid_to >= scope.date_from),
        )
    if scope.date_to:
        query = query.where(
            or_(
                RelationshipProvenance.valid_from.is_(None),
                RelationshipProvenance.valid_from < exclusive_date_to(scope.date_to),
            ),
            or_(ClaimEvidence.valid_from.is_(None), ClaimEvidence.valid_from < exclusive_date_to(scope.date_to)),
        )
    return query


def _deadline_exceeded(started: float, deadline_ms: float) -> bool:
    return (perf_counter() - started) * 1000 > deadline_ms
