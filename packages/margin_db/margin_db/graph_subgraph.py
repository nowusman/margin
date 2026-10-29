"""Assembly of authorized knowledge subgraphs from canonical records.

This module is the single path by which the graph surface obtains records. A
caller resolves the principal's readable entities first, and every node and
edge returned here is drawn from that set. A relationship is only reported
when its evidence chain is itself readable, so an edge in the graph is always
a claim the reader can check.

Nothing here is persisted. A subgraph is assembled for one request and
discarded.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from margin_db.entity_models import Entity
from margin_db.knowledge_models import IdeaBlock
from margin_db.relationship_models import EntityRelationship
from margin_db.relationship_queries import relationship_edges, with_authorized_provenance
from margin_db.source_models import SourceDocumentVersion, SourceElement
from margin_graph.types import GraphEdge, GraphNode
from margin_schema.ontology import (
    DEFAULT_TRAVERSAL_RELATIONSHIP_TYPES,
    ENTITY_RELATIONSHIP_TYPES,
    normalize_relationship_type,
)

MAX_SUBGRAPH_DEPTH = 3
MAX_SUBGRAPH_NODES = 400
MAX_SUBGRAPH_EDGES = 1_200


class SubgraphScopeError(ValueError):
    """Raised when a requested subgraph falls outside the authorized scope."""


@dataclass(frozen=True, slots=True)
class AuthorizedSubgraph:
    """A readable set of entities and the verified relationships between them."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    capped: bool
    depth_reached: int

    @property
    def node_ids(self) -> frozenset[str]:
        return frozenset(node.id for node in self.nodes)


def authorized_subgraph(
    session: Session,
    *,
    allowed_entity_ids: frozenset[str],
    allowed_idea_block_ids: frozenset[str],
    allowed_source_element_ids: frozenset[str],
    root_entity_ids: Iterable[str] | None = None,
    relation_types: Iterable[str] | None = None,
    statuses: Iterable[str] = ("verified",),
    depth: int = 1,
    as_of: date | None = None,
    max_nodes: int = MAX_SUBGRAPH_NODES,
    max_edges: int = MAX_SUBGRAPH_EDGES,
) -> AuthorizedSubgraph:
    """Assemble a readable subgraph.

    With ``root_entity_ids`` the subgraph is the bounded neighbourhood of those
    entities, expanded to ``depth`` hops. Without them the subgraph covers the
    whole authorized scope, which is what a domain-wide map needs, including
    entities that have no relationships at all.
    """

    scope_ids = frozenset(allowed_entity_ids)
    if not scope_ids:
        return AuthorizedSubgraph(nodes=(), edges=(), capped=False, depth_reached=0)

    status_values = tuple(dict.fromkeys(status for status in statuses if status)) or ("verified",)
    relation_values = _relation_values(relation_types)
    roots = tuple(root_entity_ids or ())

    if not roots:
        relationship_rows = _relationships_in_scope(
            session,
            scope_ids=scope_ids,
            relation_types=relation_values,
            statuses=status_values,
            as_of=as_of,
            limit=max_edges + 1,
        )
        capped = len(relationship_rows) > max_edges
        depth_reached = 0
        relationship_rows = relationship_rows[:max_edges]
    else:
        seeds: set[str] = set()
        for root_entity_id in roots:
            if root_entity_id not in scope_ids:
                raise SubgraphScopeError("requested subgraph root is outside the authorized scope")
            seeds.add(root_entity_id)
        relationship_rows, capped, depth_reached = _expand(
            session,
            scope_ids=scope_ids,
            seeds=seeds,
            relation_types=relation_values,
            statuses=status_values,
            as_of=as_of,
            depth=depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    graph_edges = _graph_edges(
        session,
        relationship_rows=relationship_rows,
        allowed_idea_block_ids=allowed_idea_block_ids,
        allowed_source_element_ids=allowed_source_element_ids,
        as_of=as_of,
    )
    # The records an edge names are part of the graph, so they are returned
    # with it. A graph whose edges point at records it does not contain is not
    # a graph the reader can follow. The node budget is spent on those records
    # first; a relationship that does not fit is left out rather than shown
    # pointing at something the reader cannot see.
    included_ids: set[str] = set(roots)
    kept_edges: list[GraphEdge] = []
    for edge in graph_edges:
        endpoints = {edge.source_id, edge.target_id}
        if len(included_ids | endpoints) > max_nodes:
            capped = True
            break
        included_ids |= endpoints
        kept_edges.append(edge)
    edges = tuple(kept_edges)
    nodes = _graph_nodes(session, included_ids)
    if not roots:
        # A whole-domain map also shows the records that have no relationships
        # at all, because a stranded record is exactly what a reader cannot
        # find on their own.
        remaining = max_nodes - len(nodes)
        if remaining > 0:
            spare_nodes = _graph_nodes(session, set(scope_ids - included_ids))
            if len(spare_nodes) > remaining:
                capped = True
                spare_nodes = spare_nodes[:remaining]
            nodes = tuple(sorted((*nodes, *spare_nodes), key=lambda node: node.id))
    return AuthorizedSubgraph(nodes=nodes, edges=edges, capped=capped, depth_reached=depth_reached)


def full_scope_subgraph(
    session: Session,
    *,
    statuses: Iterable[str] = ("verified",),
    relation_types: Iterable[str] | None = None,
    max_nodes: int = MAX_SUBGRAPH_NODES,
    max_edges: int = MAX_SUBGRAPH_EDGES,
) -> AuthorizedSubgraph:
    """Assemble the whole corpus graph for a system-level scan.

    A scheduled governance scan has no requesting principal, so the scope is
    every active record. That is a deliberate widening, and it is why the
    findings it produces are review work for a human rather than a statement
    made to a reader.

    The scan also keeps the records its relationships point at, including the
    ones that are no longer active. A reference to a retired record is exactly
    what the scan exists to find, and it cannot be found if the retired record
    is left out of the scope.
    """

    entity_ids = frozenset(
        entity_id
        for (entity_id,) in session.execute(select(Entity.id).where(Entity.lifecycle_status == "active")).all()
    )
    referenced_entity_ids = frozenset(
        entity_id
        for (entity_id,) in session.execute(
            select(EntityRelationship.source_entity_id).union_all(select(EntityRelationship.target_entity_id))
        ).all()
    )
    idea_block_ids = frozenset(
        idea_block_id
        for (idea_block_id,) in session.execute(select(IdeaBlock.id).where(IdeaBlock.archived_at.is_(None))).all()
    )
    source_element_ids = frozenset(
        source_element_id
        for (source_element_id,) in session.execute(
            select(SourceElement.id)
            .join(SourceDocumentVersion, SourceElement.source_version_id == SourceDocumentVersion.id)
            .where(SourceDocumentVersion.status == "active")
        ).all()
    )
    return authorized_subgraph(
        session,
        allowed_entity_ids=entity_ids | referenced_entity_ids,
        allowed_idea_block_ids=idea_block_ids,
        allowed_source_element_ids=source_element_ids,
        root_entity_ids=None,
        relation_types=relation_types,
        statuses=statuses,
        depth=1,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )


def _expand(
    session: Session,
    *,
    scope_ids: frozenset[str],
    seeds: set[str],
    relation_types: tuple[str, ...] | None,
    statuses: tuple[str, ...],
    as_of: date | None,
    depth: int,
    max_nodes: int,
    max_edges: int,
) -> tuple[list[EntityRelationship], bool, int]:
    """Grow the neighbourhood one hop at a time, staying inside the scope."""

    selected_ids = set(seeds)
    frontier = set(seeds)
    collected: dict[str, EntityRelationship] = {}
    capped = False
    depth_reached = 0

    for hop in range(1, max(1, depth) + 1):
        if not frontier:
            break
        rows = _relationships_in_scope(
            session,
            scope_ids=scope_ids,
            relation_types=relation_types,
            statuses=statuses,
            as_of=as_of,
            limit=max_edges + 1,
            frontier=frontier,
        )
        if len(rows) > max_edges:
            capped = True
            rows = rows[:max_edges]
        for row in rows:
            collected.setdefault(row.id, row)
        discovered = {row.source_entity_id for row in rows} | {row.target_entity_id for row in rows}
        next_frontier = (discovered & scope_ids) - selected_ids
        if len(selected_ids | next_frontier) > max_nodes:
            capped = True
            next_frontier = set(sorted(next_frontier)[: max(0, max_nodes - len(selected_ids))])
        selected_ids |= next_frontier
        frontier = next_frontier
        depth_reached = hop
        if not frontier:
            break

    ordered = sorted(collected.values(), key=lambda row: row.id)
    if len(ordered) > max_edges:
        capped = True
        ordered = ordered[:max_edges]
    return ordered, capped, depth_reached


def _relationships_in_scope(
    session: Session,
    *,
    scope_ids: frozenset[str],
    relation_types: tuple[str, ...] | None,
    statuses: tuple[str, ...],
    as_of: date | None,
    limit: int,
    frontier: set[str] | None = None,
) -> list[EntityRelationship]:
    """Select readable relationships whose endpoints are both in scope."""

    query = (
        select(EntityRelationship)
        .where(EntityRelationship.source_entity_id.in_(scope_ids))
        .where(EntityRelationship.target_entity_id.in_(scope_ids))
        .where(EntityRelationship.status.in_(statuses))
    )
    if relation_types is not None:
        query = query.where(EntityRelationship.relation_type.in_(relation_types))
    if as_of is not None:
        query = query.where(
            or_(EntityRelationship.valid_from.is_(None), EntityRelationship.valid_from <= as_of),
            or_(EntityRelationship.valid_to.is_(None), EntityRelationship.valid_to >= as_of),
        )
    if frontier:
        query = query.where(
            or_(EntityRelationship.source_entity_id.in_(frontier), EntityRelationship.target_entity_id.in_(frontier))
        )
    return list(session.scalars(query.order_by(EntityRelationship.id).limit(limit)).all())


def _graph_edges(
    session: Session,
    *,
    relationship_rows: Iterable[EntityRelationship],
    allowed_idea_block_ids: frozenset[str],
    allowed_source_element_ids: frozenset[str],
    as_of: date | None,
) -> tuple[GraphEdge, ...]:
    """Turn relationships into graph edges, keeping only evidenced ones."""

    rows = tuple(relationship_rows)
    if not rows:
        return ()
    edges = relationship_edges(session, rows)
    evidenced = with_authorized_provenance(
        session,
        edges=edges,
        allowed_idea_block_ids=allowed_idea_block_ids,
        allowed_source_element_ids=allowed_source_element_ids,
        as_of=as_of,
    )
    return tuple(
        GraphEdge(
            id=edge.relationship_id,
            source_id=edge.source.id,
            target_id=edge.target.id,
            relation_type=edge.relation_type,
            confidence=edge.confidence,
            status=edge.status,
            valid_from=edge.valid_from,
            valid_to=edge.valid_to,
            quotes=tuple(provenance.source_quote for provenance in edge.provenance if provenance.source_quote),
            idea_block_ids=tuple(dict.fromkeys(provenance.idea_block_id for provenance in edge.provenance)),
        )
        for edge in evidenced
    )


def _graph_nodes(session: Session, entity_ids: set[str]) -> tuple[GraphNode, ...]:
    """Build the graph nodes for the supplied entities."""

    if not entity_ids:
        return ()
    rows = session.scalars(select(Entity).where(Entity.id.in_(entity_ids), Entity.lifecycle_status == "active")).all()
    return tuple(
        sorted(
            (
                GraphNode(
                    id=entity.id,
                    label=entity.preferred_label,
                    kind=entity.ontology_type,
                    domain_id=entity.domain_id,
                    definition=entity.definition,
                    ontology_version=entity.ontology_version,
                )
                for entity in rows
            ),
            key=lambda node: node.id,
        )
    )


def _relation_values(relation_types: Iterable[str] | None) -> tuple[str, ...] | None:
    """Normalize and validate the requested relationship types."""

    if relation_types is None:
        return tuple(sorted(DEFAULT_TRAVERSAL_RELATIONSHIP_TYPES))
    normalized: list[str] = []
    for value in relation_types:
        relation_type = normalize_relationship_type(value)
        if relation_type is None or relation_type not in ENTITY_RELATIONSHIP_TYPES:
            raise SubgraphScopeError(f"unknown relationship type: {value}")
        normalized.append(relation_type)
    if not normalized:
        raise SubgraphScopeError("relation_types must contain at least one value")
    return tuple(dict.fromkeys(normalized))
