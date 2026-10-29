"""Bounded, authorization-scoped queries for explicit relationships."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord, GovernanceRecordEntity, GovernanceRecordRevision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRelation, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_schema.ontology import (
    DEFAULT_TRAVERSAL_RELATIONSHIP_TYPES,
    ENTITY_RELATIONSHIP_TYPES,
    RELATIONSHIP_REGISTRY,
    normalize_relationship_type,
    validate_relationship_endpoints,
)

MAX_TRAVERSAL_DEPTH = 4
MAX_RESULTS_PER_DEPTH = 25
MAX_TRAVERSAL_RESULTS = 100
TRAVERSAL_STATEMENT_TIMEOUT_MS = 1_000

_GOVERNANCE_LIFECYCLE_STATUSES = (
    "proposed",
    "accepted",
    "superseded",
    "deprecated",
    "rejected",
    "identified",
    "mitigating",
    "closed",
    "approved",
    "expired",
    "revoked",
    "planned",
    "in_progress",
    "resolved",
    "open",
    "blocked",
    "completed",
    "cancelled",
)


class RelationshipQueryError(ValueError):
    """Raised when a relationship query does not have a safe bounded scope."""


class RelationshipDatabaseConfigurationError(RuntimeError):
    """Raised when a recursive relationship query is not backed by PostgreSQL."""


@dataclass(frozen=True, slots=True)
class EntitySummary:
    id: str
    label: str
    ontology_type: str
    domain_id: str | None
    lifecycle_status: str


@dataclass(frozen=True, slots=True)
class RelationshipProvenanceSummary:
    id: str
    idea_block_id: str
    source_element_id: str
    source_quote: str | None
    polarity: str
    status: str
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True, slots=True)
class RelationshipEdge:
    relationship_id: str
    source: EntitySummary
    target: EntitySummary
    relation_type: str
    confidence: str
    status: str
    valid_from: date | None
    valid_to: date | None
    direction: str
    hop: int
    provenance: tuple[RelationshipProvenanceSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class TraversalResult:
    edges: tuple[RelationshipEdge, ...]
    capped: bool


@dataclass(frozen=True, slots=True)
class ImpactRecord:
    entity: EntitySummary
    hop: int
    relation_type: str | None
    path: tuple[RelationshipEdge, ...]


@dataclass(frozen=True, slots=True)
class IdeaBlockSummary:
    id: str
    statement: str
    type: str
    status: str
    domain_id: str


@dataclass(frozen=True, slots=True)
class IdeaBlockRelationship:
    relationship_id: str
    source: IdeaBlockSummary
    target: IdeaBlockSummary
    relation_type: str
    direction: str
    status: str
    confidence: float
    valid_from: date | None
    valid_to: date | None
    decision_provenance: dict


@dataclass(frozen=True, slots=True)
class IdeaBlockRelationshipResult:
    relations: tuple[IdeaBlockRelationship, ...]
    capped: bool


@dataclass(frozen=True, slots=True)
class GovernedEntityLink:
    governance_record_id: str
    business_key: str
    title: str
    record_type: str
    lifecycle_status: str
    domain_id: str
    role: str


@dataclass(frozen=True, slots=True)
class GovernedEntityLinksResult:
    links: tuple[GovernedEntityLink, ...]
    capped: bool


def direct_neighbors(
    session: Session,
    *,
    entity_id: str,
    allowed_entity_ids: frozenset[str],
    relation_types: Iterable[str] | None = None,
    statuses: Iterable[str] = ("verified",),
    as_of: date | None = None,
    limit: int = MAX_TRAVERSAL_RESULTS,
) -> TraversalResult:
    """Fetch one-hop readable relationships through the endpoint indexes."""

    _require_visible_seed(entity_id, allowed_entity_ids)
    bounded_limit = _bounded_limit(limit, maximum=MAX_TRAVERSAL_RESULTS, name="limit")
    filters = _relationship_filters(relation_types=relation_types, statuses=statuses, as_of=as_of)
    endpoint_filter = or_(
        and_(
            EntityRelationship.source_entity_id == entity_id,
            EntityRelationship.target_entity_id.in_(allowed_entity_ids),
        ),
        and_(
            EntityRelationship.target_entity_id == entity_id,
            EntityRelationship.source_entity_id.in_(allowed_entity_ids),
        ),
    )
    rows = session.scalars(
        select(EntityRelationship)
        .where(endpoint_filter, EntityRelationship.source_entity_id.in_(allowed_entity_ids))
        .where(*filters)
        .order_by(EntityRelationship.created_at.desc(), EntityRelationship.id)
        .limit(bounded_limit + 1)
    ).all()
    capped = len(rows) > bounded_limit
    return TraversalResult(
        edges=relationship_edges(session, rows[:bounded_limit], seed_entity_id=entity_id), capped=capped
    )


def bounded_path(
    session: Session,
    *,
    source_entity_id: str,
    target_entity_id: str,
    allowed_entity_ids: frozenset[str],
    max_depth: int,
    relation_types: Iterable[str] | None = None,
    statuses: Iterable[str] = ("verified",),
    as_of: date | None = None,
    results_per_depth: int = MAX_RESULTS_PER_DEPTH,
    total_limit: int = MAX_TRAVERSAL_RESULTS,
) -> TraversalResult:
    """Return one shortest permitted relationship path using a bounded CTE."""

    _require_visible_seed(source_entity_id, allowed_entity_ids)
    _require_visible_seed(target_entity_id, allowed_entity_ids)
    if source_entity_id == target_entity_id:
        return TraversalResult(edges=(), capped=False)
    traversed = _bounded_traversal(
        session,
        entity_id=source_entity_id,
        allowed_entity_ids=allowed_entity_ids,
        max_depth=max_depth,
        direction="both",
        relation_types=relation_types,
        statuses=statuses,
        as_of=as_of,
        results_per_depth=results_per_depth,
        total_limit=total_limit,
    )
    path = _shortest_path(
        source_entity_id=source_entity_id, target_entity_id=target_entity_id, edges=traversed.edges, max_depth=max_depth
    )
    return TraversalResult(edges=path, capped=traversed.capped)


def bounded_impact(
    session: Session,
    *,
    entity_id: str,
    allowed_entity_ids: frozenset[str],
    max_depth: int,
    direction: str = "both",
    relation_types: Iterable[str] | None = None,
    statuses: Iterable[str] = ("verified",),
    as_of: date | None = None,
    results_per_depth: int = MAX_RESULTS_PER_DEPTH,
    total_limit: int = MAX_TRAVERSAL_RESULTS,
) -> tuple[tuple[ImpactRecord, ...], bool]:
    """Return readable entities reached through a bounded dependency traversal."""

    traversed = _bounded_traversal(
        session,
        entity_id=entity_id,
        allowed_entity_ids=allowed_entity_ids,
        max_depth=max_depth,
        direction=direction,
        relation_types=relation_types,
        statuses=statuses,
        as_of=as_of,
        results_per_depth=results_per_depth,
        total_limit=total_limit,
    )
    paths = _shortest_paths(source_entity_id=entity_id, edges=traversed.edges, max_depth=max_depth)
    return (
        tuple(
            ImpactRecord(
                entity=edge_entity, hop=len(path), relation_type=path[-1].relation_type if path else None, path=path
            )
            for entity_id_value, path in sorted(paths.items(), key=lambda item: (len(item[1]), item[0]))
            if entity_id_value != entity_id
            for edge_entity in (_summary_for_path(entity_id_value, path),)
            if edge_entity is not None
        ),
        traversed.capped,
    )


def with_authorized_provenance(
    session: Session,
    *,
    edges: Iterable[RelationshipEdge],
    allowed_idea_block_ids: frozenset[str],
    allowed_source_element_ids: frozenset[str],
    as_of: date | None = None,
) -> tuple[RelationshipEdge, ...]:
    """Attach only verified provenance anchored to current active evidence."""

    edge_values = tuple(edges)
    relationship_ids = tuple(edge.relationship_id for edge in edge_values)
    if not relationship_ids or not allowed_idea_block_ids or not allowed_source_element_ids:
        # A relationship without a readable, exact evidence chain is not an
        # asserted edge for the explorer.  Keep it quarantined for review
        # rather than exposing an unsubstantiated edge with an empty payload.
        return ()
    provenance_relationship_ids, provenance_sources = relationship_provenance_scope(session, relationship_ids)
    relationship_rows = session.scalars(
        select(EntityRelationship).where(EntityRelationship.id.in_(relationship_ids))
    ).all()
    valid_provenance_edge_ids = {
        row.id
        for row in relationship_rows
        if not (isinstance(row.metadata_, dict) and row.metadata_.get("derived") is True)
    }
    valid_provenance_edge_ids.update(provenance_sources)
    provenance_by_relationship: dict[str, list[RelationshipProvenanceSummary]] = {}
    source_entity = aliased(Entity)
    target_entity = aliased(Entity)
    category_entity = aliased(Entity)
    query = (
        select(RelationshipProvenance)
        .join(EntityRelationship, EntityRelationship.id == RelationshipProvenance.relationship_id)
        .join(source_entity, source_entity.id == EntityRelationship.source_entity_id)
        .join(target_entity, target_entity.id == EntityRelationship.target_entity_id)
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
                ClaimEvidence.idea_block_id == IdeaBlock.id,
                ClaimEvidence.idea_block_revision_id == IdeaBlockRevision.id,
                ClaimEvidence.source_element_id == RelationshipProvenance.source_element_id,
                ClaimEvidence.source_quote == RelationshipProvenance.source_quote,
                ClaimEvidence.polarity == RelationshipProvenance.polarity,
                ClaimEvidence.status == "active",
                ClaimEvidence.governance_record_revision_id.is_(None),
            ),
        )
        .where(RelationshipProvenance.relationship_id.in_(provenance_relationship_ids))
        .where(RelationshipProvenance.idea_block_id.in_(allowed_idea_block_ids))
        .where(RelationshipProvenance.source_element_id.in_(allowed_source_element_ids))
        .where(RelationshipProvenance.status == "verified")
        .where(EntityRelationship.status.in_(("verified", "candidate")))
        .where(source_entity.lifecycle_status == "active", target_entity.lifecycle_status == "active")
        .where(source_entity.domain_id == target_entity.domain_id)
        .where(source_entity.domain_id == IdeaBlock.domain_id, target_entity.domain_id == IdeaBlock.domain_id)
        .where(SourceDocumentVersion.status == "active")
        .where(category_entity.lifecycle_status == "active")
        .where(IdeaBlock.merged_into_id.is_(None), IdeaBlock.tombstoned_at.is_(None), IdeaBlock.archived_at.is_(None))
        .where(IdeaBlockRevision.idea_block_id == IdeaBlock.id)
        .where(IdeaBlockRevision.publication_status == "verified")
        .where(SourceDocument.domain_id == IdeaBlock.domain_id)
        .order_by(RelationshipProvenance.created_at, RelationshipProvenance.id)
    )
    if as_of is not None:
        query = query.where(
            or_(RelationshipProvenance.valid_from.is_(None), RelationshipProvenance.valid_from <= as_of),
            or_(RelationshipProvenance.valid_to.is_(None), RelationshipProvenance.valid_to >= as_of),
            or_(ClaimEvidence.valid_from.is_(None), ClaimEvidence.valid_from <= as_of),
            or_(ClaimEvidence.valid_to.is_(None), ClaimEvidence.valid_to >= as_of),
        )
    rows = session.scalars(query).all()
    for row in rows:
        provenance_by_relationship.setdefault(row.relationship_id, []).append(
            RelationshipProvenanceSummary(
                id=row.id,
                idea_block_id=row.idea_block_id,
                source_element_id=row.source_element_id,
                source_quote=row.source_quote,
                polarity=row.polarity,
                status=row.status,
                valid_from=row.valid_from,
                valid_to=row.valid_to,
            )
        )
    enriched: list[RelationshipEdge] = []
    for edge in edge_values:
        if edge.relationship_id not in valid_provenance_edge_ids:
            continue
        provenance = tuple(
            provenance_by_relationship.get(provenance_sources.get(edge.relationship_id, edge.relationship_id), ())
        )
        if provenance:
            enriched.append(replace(edge, provenance=provenance))
    return tuple(enriched)


def relationship_provenance_scope(
    session: Session, relationship_ids: Iterable[str]
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Expand derived inverse edges to their validated originals for provenance lookup."""

    direct_ids = tuple(dict.fromkeys(relationship_ids))
    if not direct_ids:
        return (), {}
    relationship_rows = session.scalars(select(EntityRelationship).where(EntityRelationship.id.in_(direct_ids))).all()
    relationships_by_id = {row.id: row for row in relationship_rows}
    inverse_ids: set[str] = set()
    for row in relationship_rows:
        metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
        inverse_id = metadata.get("inverse_of") if metadata.get("derived") is True else None
        if isinstance(inverse_id, str):
            inverse_ids.add(inverse_id)
    missing_inverse_ids = inverse_ids.difference(relationships_by_id)
    if missing_inverse_ids:
        relationships_by_id.update(
            {
                row.id: row
                for row in session.scalars(
                    select(EntityRelationship).where(EntityRelationship.id.in_(missing_inverse_ids))
                ).all()
            }
        )
    provenance_sources: dict[str, str] = {}
    scoped_ids = list(direct_ids)
    for row in relationship_rows:
        metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
        original_id = metadata.get("inverse_of") if metadata.get("derived") is True else None
        original = relationships_by_id.get(original_id) if isinstance(original_id, str) else None
        if original is None or not _is_valid_inverse(original, row):
            continue
        provenance_sources[row.id] = original.id
        if original.id not in scoped_ids:
            scoped_ids.append(original.id)
    return tuple(scoped_ids), provenance_sources


def related_idea_blocks(
    session: Session,
    *,
    idea_block_id: str,
    allowed_idea_block_ids: frozenset[str],
    statuses: Iterable[str] = ("verified",),
    as_of: date | None = None,
    limit: int = MAX_TRAVERSAL_RESULTS,
) -> IdeaBlockRelationshipResult:
    """Return direct, explicit IdeaBlock relations without treating them as entity edges."""

    _require_visible_seed(idea_block_id, allowed_idea_block_ids)
    bounded_limit = _bounded_limit(limit, maximum=MAX_TRAVERSAL_RESULTS, name="limit")
    status_values = _nonempty_values(statuses, "statuses")
    filters = [IdeaBlockRelation.status.in_(status_values)]
    if as_of is not None:
        filters.extend(
            (
                or_(IdeaBlockRelation.valid_from.is_(None), IdeaBlockRelation.valid_from <= as_of),
                or_(IdeaBlockRelation.valid_to.is_(None), IdeaBlockRelation.valid_to >= as_of),
            )
        )
    rows = session.scalars(
        select(IdeaBlockRelation)
        .where(
            or_(
                IdeaBlockRelation.source_idea_block_id == idea_block_id,
                IdeaBlockRelation.target_idea_block_id == idea_block_id,
            ),
            IdeaBlockRelation.source_idea_block_id.in_(allowed_idea_block_ids),
            IdeaBlockRelation.target_idea_block_id.in_(allowed_idea_block_ids),
            _verified_idea_block_endpoint(IdeaBlockRelation.source_idea_block_id),
            _verified_idea_block_endpoint(IdeaBlockRelation.target_idea_block_id),
            IdeaBlockRelation.decision_provenance != {},
            *filters,
        )
        .order_by(IdeaBlockRelation.created_at.desc(), IdeaBlockRelation.id)
        .limit(bounded_limit + 1)
    ).all()
    capped = len(rows) > bounded_limit
    rows = rows[:bounded_limit]
    summaries = _idea_block_summaries(
        session, {item_id for row in rows for item_id in (row.source_idea_block_id, row.target_idea_block_id)}
    )
    return IdeaBlockRelationshipResult(
        relations=tuple(
            IdeaBlockRelationship(
                relationship_id=row.id,
                source=summaries[row.source_idea_block_id],
                target=summaries[row.target_idea_block_id],
                relation_type=row.relation_type,
                direction=row.direction,
                status=row.status,
                confidence=float(row.confidence),
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                decision_provenance=row.decision_provenance or {},
            )
            for row in rows
        ),
        capped=capped,
    )


def governed_entity_links(
    session: Session,
    *,
    entity_id: str,
    allowed_entity_ids: frozenset[str],
    allowed_governance_record_ids: frozenset[str],
    limit: int = MAX_TRAVERSAL_RESULTS,
) -> GovernedEntityLinksResult:
    """Return governance-to-Entity links under both entity and record authorization."""

    _require_visible_seed(entity_id, allowed_entity_ids)
    if not allowed_governance_record_ids:
        return GovernedEntityLinksResult(links=(), capped=False)
    bounded_limit = _bounded_limit(limit, maximum=MAX_TRAVERSAL_RESULTS, name="limit")
    rows = session.execute(
        select(GovernanceRecordEntity, GovernanceRecord)
        .join(GovernanceRecord, GovernanceRecord.id == GovernanceRecordEntity.governance_record_id)
        .join(
            GovernanceRecordRevision,
            and_(
                GovernanceRecordRevision.id == GovernanceRecord.current_revision_id,
                GovernanceRecordRevision.governance_record_id == GovernanceRecord.id,
            ),
        )
        .join(Entity, Entity.id == GovernanceRecordEntity.entity_id)
        .where(GovernanceRecordEntity.entity_id == entity_id)
        .where(GovernanceRecord.id.in_(allowed_governance_record_ids))
        .where(Entity.lifecycle_status == "active")
        .where(or_(Entity.domain_id.is_(None), Entity.domain_id == GovernanceRecord.domain_id))
        .where(GovernanceRecord.lifecycle_status.in_(_GOVERNANCE_LIFECYCLE_STATUSES))
        .order_by(GovernanceRecord.updated_at.desc(), GovernanceRecord.id)
        .limit(bounded_limit + 1)
    ).all()
    capped = len(rows) > bounded_limit
    return GovernedEntityLinksResult(
        links=tuple(
            GovernedEntityLink(
                governance_record_id=record.id,
                business_key=record.business_key,
                title=record.title,
                record_type=record.record_type,
                lifecycle_status=record.lifecycle_status,
                domain_id=record.domain_id,
                role=link.role,
            )
            for link, record in rows[:bounded_limit]
        ),
        capped=capped,
    )


def _bounded_traversal(
    session: Session,
    *,
    entity_id: str,
    allowed_entity_ids: frozenset[str],
    max_depth: int,
    direction: str,
    relation_types: Iterable[str] | None,
    statuses: Iterable[str],
    as_of: date | None,
    results_per_depth: int,
    total_limit: int,
) -> TraversalResult:
    _require_visible_seed(entity_id, allowed_entity_ids)
    if direction not in {"inbound", "outbound", "both"}:
        raise RelationshipQueryError("direction must be inbound, outbound, or both")
    depth = _bounded_limit(max_depth, maximum=MAX_TRAVERSAL_DEPTH, name="max_depth")
    per_depth_limit = _bounded_limit(results_per_depth, maximum=MAX_RESULTS_PER_DEPTH, name="results_per_depth")
    total = _bounded_limit(total_limit, maximum=MAX_TRAVERSAL_RESULTS, name="total_limit")
    _require_postgresql(session)
    relation_values = _relation_values(relation_types)
    status_values = _nonempty_values(statuses, "statuses")
    session.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"), {"timeout": str(TRAVERSAL_STATEMENT_TIMEOUT_MS)}
    )
    rows = (
        session.execute(
            text(
                """
            WITH RECURSIVE walk AS (
                SELECT
                    er.id AS relationship_id,
                    er.source_entity_id,
                    er.target_entity_id,
                    er.relation_type,
                    er.confidence,
                    er.status,
                    er.valid_from,
                    er.valid_to,
                    1 AS hop,
                    CASE WHEN er.source_entity_id = :entity_id THEN 'outbound' ELSE 'inbound' END AS direction,
                    CASE WHEN er.source_entity_id = :entity_id
                        THEN er.target_entity_id ELSE er.source_entity_id END AS node_id,
                    ARRAY[er.id]::text[] AS path_relationship_ids
                FROM entity_relationships er
                JOIN entities source_entity ON source_entity.id = er.source_entity_id
                JOIN entities target_entity ON target_entity.id = er.target_entity_id
                WHERE (
                    (:direction = 'outbound' AND er.source_entity_id = :entity_id)
                    OR (:direction = 'inbound' AND er.target_entity_id = :entity_id)
                    OR (:direction = 'both' AND (er.source_entity_id = :entity_id OR er.target_entity_id = :entity_id))
                )
                AND er.source_entity_id = ANY(CAST(:allowed_entity_ids AS text[]))
                AND er.target_entity_id = ANY(CAST(:allowed_entity_ids AS text[]))
                AND source_entity.lifecycle_status = 'active'
                AND target_entity.lifecycle_status = 'active'
                AND er.status = ANY(CAST(:statuses AS text[]))
                AND (CAST(:relation_types AS text[]) IS NULL
                    OR er.relation_type = ANY(CAST(:relation_types AS text[])))
                AND (CAST(:as_of AS date) IS NULL
                    OR (er.valid_from IS NULL OR er.valid_from <= CAST(:as_of AS date)))
                AND (CAST(:as_of AS date) IS NULL
                    OR (er.valid_to IS NULL OR er.valid_to >= CAST(:as_of AS date)))

                UNION ALL

                SELECT
                    er.id,
                    er.source_entity_id,
                    er.target_entity_id,
                    er.relation_type,
                    er.confidence,
                    er.status,
                    er.valid_from,
                    er.valid_to,
                    walk.hop + 1,
                    CASE WHEN er.source_entity_id = walk.node_id THEN 'outbound' ELSE 'inbound' END,
                    CASE WHEN er.source_entity_id = walk.node_id
                        THEN er.target_entity_id ELSE er.source_entity_id END,
                    walk.path_relationship_ids || er.id
                FROM walk
                JOIN entity_relationships er ON (
                    (:direction = 'outbound' AND er.source_entity_id = walk.node_id)
                    OR (:direction = 'inbound' AND er.target_entity_id = walk.node_id)
                    OR (:direction = 'both' AND (
                        er.source_entity_id = walk.node_id OR er.target_entity_id = walk.node_id
                    ))
                )
                JOIN entities source_entity ON source_entity.id = er.source_entity_id
                JOIN entities target_entity ON target_entity.id = er.target_entity_id
                WHERE walk.hop < :max_depth
                AND NOT (er.id = ANY(walk.path_relationship_ids))
                AND er.source_entity_id = ANY(CAST(:allowed_entity_ids AS text[]))
                AND er.target_entity_id = ANY(CAST(:allowed_entity_ids AS text[]))
                AND source_entity.lifecycle_status = 'active'
                AND target_entity.lifecycle_status = 'active'
                AND er.status = ANY(CAST(:statuses AS text[]))
                AND (CAST(:relation_types AS text[]) IS NULL
                    OR er.relation_type = ANY(CAST(:relation_types AS text[])))
                AND (CAST(:as_of AS date) IS NULL
                    OR (er.valid_from IS NULL OR er.valid_from <= CAST(:as_of AS date)))
                AND (CAST(:as_of AS date) IS NULL
                    OR (er.valid_to IS NULL OR er.valid_to >= CAST(:as_of AS date)))
            ), ranked AS (
                SELECT
                    walk.*,
                    ROW_NUMBER() OVER (PARTITION BY hop ORDER BY relationship_id) AS depth_rank,
                    COUNT(*) OVER (PARTITION BY hop) AS depth_total
                FROM walk
            ), bounded AS (
                SELECT ranked.*, COUNT(*) OVER () AS total_count
                FROM ranked
                WHERE depth_rank <= :results_per_depth
            )
            SELECT relationship_id, source_entity_id, target_entity_id, relation_type, confidence, status,
                valid_from, valid_to, hop, direction, depth_total, total_count
            FROM bounded
            ORDER BY hop, relationship_id
            LIMIT :total_limit
            """
            ),
            {
                "entity_id": entity_id,
                "direction": direction,
                "allowed_entity_ids": list(allowed_entity_ids),
                "statuses": list(status_values),
                "relation_types": list(relation_values) if relation_values is not None else None,
                "as_of": as_of,
                "max_depth": depth,
                "results_per_depth": per_depth_limit,
                "total_limit": total,
            },
        )
        .mappings()
        .all()
    )
    relationship_ids = [str(row["relationship_id"]) for row in rows]
    relationships = {
        relationship.id: relationship
        for relationship in session.scalars(
            select(EntityRelationship).where(EntityRelationship.id.in_(relationship_ids))
        ).all()
    }
    edge_rows = [
        relationships[relationship_id] for relationship_id in relationship_ids if relationship_id in relationships
    ]
    edges_by_id = {
        edge.relationship_id: edge for edge in relationship_edges(session, edge_rows, seed_entity_id=entity_id)
    }
    edges = tuple(
        replace(edges_by_id[str(row["relationship_id"])], hop=int(row["hop"]), direction=str(row["direction"]))
        for row in rows
        if str(row["relationship_id"]) in edges_by_id
    )
    capped = bool(rows) and (
        int(rows[0]["total_count"]) > total or any(int(row["depth_total"]) > per_depth_limit for row in rows)
    )
    return TraversalResult(edges=edges, capped=capped)


def relationship_edges(
    session: Session, relationships: Iterable[EntityRelationship], *, seed_entity_id: str | None = None
) -> tuple[RelationshipEdge, ...]:
    """Build readable relationship edges for the supplied relationships.

    A seed makes the edge direction relative to that entity. Without a seed the
    edge is undirected, which is the correct reading for a whole-subgraph view
    where no single entity is the origin of the traversal.
    """

    relationship_rows = tuple(relationships)
    entity_ids = {
        entity_id
        for relationship in relationship_rows
        for entity_id in (relationship.source_entity_id, relationship.target_entity_id)
    }
    summaries = entity_summaries(session, entity_ids)
    return tuple(
        RelationshipEdge(
            relationship_id=relationship.id,
            source=summaries[relationship.source_entity_id],
            target=summaries[relationship.target_entity_id],
            relation_type=relationship.relation_type,
            confidence=relationship.confidence,
            status=relationship.status,
            valid_from=relationship.valid_from,
            valid_to=relationship.valid_to,
            direction=_edge_direction(relationship, seed_entity_id),
            hop=1,
        )
        for relationship in relationship_rows
        if relationship.source_entity_id in summaries
        and relationship.target_entity_id in summaries
        and _valid_entity_relationship(relationship, summaries)
    )


def _edge_direction(relationship: EntityRelationship, seed_entity_id: str | None) -> str:
    if seed_entity_id is None:
        return "undirected"
    return "outbound" if relationship.source_entity_id == seed_entity_id else "inbound"


def _valid_entity_relationship(relationship: EntityRelationship, summaries: dict[str, EntitySummary]) -> bool:
    source = summaries.get(relationship.source_entity_id)
    target = summaries.get(relationship.target_entity_id)
    if source is None or target is None:
        return False
    try:
        validate_relationship_endpoints(relationship.relation_type, source.ontology_type, target.ontology_type)
    except ValueError:
        return False
    return True


def entity_summaries(session: Session, entity_ids: set[str]) -> dict[str, EntitySummary]:
    if not entity_ids:
        return {}
    return {
        entity.id: EntitySummary(
            id=entity.id,
            label=entity.preferred_label,
            ontology_type=entity.ontology_type,
            domain_id=entity.domain_id,
            lifecycle_status=entity.lifecycle_status,
        )
        for entity in session.scalars(
            select(Entity).where(Entity.id.in_(entity_ids), Entity.lifecycle_status == "active")
        ).all()
    }


def _idea_block_summaries(session: Session, idea_block_ids: set[str]) -> dict[str, IdeaBlockSummary]:
    if not idea_block_ids:
        return {}
    rows = session.execute(
        select(IdeaBlock, IdeaBlockRevision)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(IdeaBlock.id.in_(idea_block_ids), IdeaBlockRevision.publication_status == "verified")
    ).all()
    return {
        block.id: IdeaBlockSummary(
            id=block.id,
            statement=revision.statement,
            type=revision.type,
            status=revision.publication_status,
            domain_id=block.domain_id,
        )
        for block, revision in rows
    }


def _shortest_path(
    *, source_entity_id: str, target_entity_id: str, edges: Iterable[RelationshipEdge], max_depth: int
) -> tuple[RelationshipEdge, ...]:
    return _shortest_paths(source_entity_id=source_entity_id, edges=edges, max_depth=max_depth).get(
        target_entity_id, ()
    )


def _shortest_paths(
    *, source_entity_id: str, edges: Iterable[RelationshipEdge], max_depth: int
) -> dict[str, tuple[RelationshipEdge, ...]]:
    adjacency: dict[str, list[RelationshipEdge]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source.id, []).append(edge)
        adjacency.setdefault(edge.target.id, []).append(edge)
    paths: dict[str, tuple[RelationshipEdge, ...]] = {source_entity_id: ()}
    frontier: deque[str] = deque((source_entity_id,))
    while frontier:
        current = frontier.popleft()
        current_path = paths[current]
        if len(current_path) >= max_depth:
            continue
        for edge in adjacency.get(current, []):
            next_entity_id = edge.target.id if edge.source.id == current else edge.source.id
            if next_entity_id in paths:
                continue
            paths[next_entity_id] = (*current_path, edge)
            frontier.append(next_entity_id)
    return paths


def _summary_for_path(entity_id: str, path: tuple[RelationshipEdge, ...]) -> EntitySummary | None:
    if not path:
        return None
    last_edge = path[-1]
    return last_edge.source if last_edge.source.id == entity_id else last_edge.target


def _verified_idea_block_endpoint(endpoint_column: ColumnElement[str]) -> ColumnElement[bool]:
    """Keep relation results on current, verified IdeaBlock revisions only."""

    return (
        select(IdeaBlock.id)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(IdeaBlock.id == endpoint_column)
        .where(IdeaBlock.merged_into_id.is_(None), IdeaBlock.tombstoned_at.is_(None), IdeaBlock.archived_at.is_(None))
        .where(IdeaBlockRevision.publication_status == "verified")
        .exists()
    )


def _is_valid_inverse(original: EntityRelationship, inverse: EntityRelationship) -> bool:
    definition = RELATIONSHIP_REGISTRY.get(original.relation_type)
    return bool(
        definition is not None
        and definition.inverse == inverse.relation_type
        and original.status in {"verified", "candidate"}
        and inverse.status in {"verified", "candidate"}
        and original.source_entity_id == inverse.target_entity_id
        and original.target_entity_id == inverse.source_entity_id
    )


def _relationship_filters(
    *, relation_types: Iterable[str] | None, statuses: Iterable[str], as_of: date | None
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [EntityRelationship.status.in_(_nonempty_values(statuses, "statuses"))]
    relation_values = _relation_values(relation_types)
    if relation_values is not None:
        filters.append(EntityRelationship.relation_type.in_(relation_values))
    if as_of is not None:
        filters.extend(
            (
                or_(EntityRelationship.valid_from.is_(None), EntityRelationship.valid_from <= as_of),
                or_(EntityRelationship.valid_to.is_(None), EntityRelationship.valid_to >= as_of),
            )
        )
    return filters


def _relation_values(relation_types: Iterable[str] | None) -> tuple[str, ...] | None:
    if relation_types is None:
        return tuple(sorted(DEFAULT_TRAVERSAL_RELATIONSHIP_TYPES))
    values = _nonempty_values(relation_types, "relation_types")
    normalized: list[str] = []
    for value in values:
        relation_type = normalize_relationship_type(value)
        if relation_type is None or relation_type not in ENTITY_RELATIONSHIP_TYPES:
            raise RelationshipQueryError(f"unknown EntityRelationship type: {value}")
        normalized.append(relation_type)
    return tuple(dict.fromkeys(normalized))


def _nonempty_values(values: Iterable[str], name: str) -> tuple[str, ...]:
    cleaned_values: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise RelationshipQueryError(f"{name} must contain only text values")
        normalized = value.strip()
        if normalized:
            cleaned_values.append(normalized)
    cleaned = tuple(dict.fromkeys(cleaned_values))
    if not cleaned:
        raise RelationshipQueryError(f"{name} must contain at least one value")
    return cleaned


def _require_visible_seed(identifier: str, allowed_ids: frozenset[str]) -> None:
    if identifier not in allowed_ids:
        raise RelationshipQueryError("requested relationship seed is outside the authorized scope")


def _bounded_limit(value: int, *, maximum: int, name: str) -> int:
    if value < 1 or value > maximum:
        raise RelationshipQueryError(f"{name} must be between 1 and {maximum}")
    return value


def _require_postgresql(session: Session) -> None:
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect != "postgresql":
        raise RelationshipDatabaseConfigurationError("bounded relationship traversal requires PostgreSQL")
