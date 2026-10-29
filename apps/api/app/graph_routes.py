"""HTTP operations for exploring the knowledge graph.

Every response here is computed from the records the caller may read. The
graph is never stored, so a node is present only while the corpus supports it
and an edge is present only when its evidence chain is readable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.authz import permitted_source_element_ids
from app.catalog_helpers import readable_entity_ids, readable_idea_block_ids, require_any_read
from app.source_routes import get_session_factory
from margin_db.entity_models import Entity
from margin_db.graph_models import (
    GRAPH_DECISION_DEFER,
    GRAPH_DECISION_DISMISS,
    GRAPH_DECISION_KINDS,
    GRAPH_TARGET_CONNECTION,
    GRAPH_TARGET_GAP,
    GRAPH_TARGET_TYPES,
    GraphInsightDecision,
)
from margin_db.graph_subgraph import (
    MAX_SUBGRAPH_DEPTH,
    MAX_SUBGRAPH_EDGES,
    MAX_SUBGRAPH_NODES,
    SubgraphScopeError,
    authorized_subgraph,
)
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock
from margin_graph import (
    detect_communities,
    detect_graph_hygiene,
    detect_knowledge_gaps,
    find_surprising_connections,
    research_seed_for_gap,
)
from margin_schema.ids import graph_decision_id
from margin_schema.ontology import ONTOLOGY_VERSION

router = APIRouter(prefix="/api/graph", tags=["graph"])

DEFAULT_INSIGHT_LIMIT = 5
DEFAULT_GAP_LIMIT = 8
DEFAULT_DEFERRAL_DAYS = 7
MAX_DEFERRAL_DAYS = 90


class DecisionRequest(BaseModel):
    target_id: str = Field(min_length=1, max_length=400)
    target_type: str = Field(default=GRAPH_TARGET_CONNECTION, max_length=40)
    decision: str = Field(default=GRAPH_DECISION_DISMISS, max_length=40)
    reason: str | None = Field(default=None, max_length=500)
    defer_days: int = Field(default=DEFAULT_DEFERRAL_DAYS, ge=1, le=MAX_DEFERRAL_DAYS)


def _statuses(include_candidates: bool) -> tuple[str, ...]:
    return ("verified", "candidate") if include_candidates else ("verified",)


def _authorized_entity_ids(session, request: Request) -> frozenset[str]:
    entities = session.scalars(select(Entity).where(Entity.lifecycle_status == "active")).all()
    return frozenset(readable_entity_ids(session, request, {entity.id for entity in entities}))


def _authorized_idea_block_ids(session, request: Request) -> frozenset[str]:
    blocks = session.scalars(select(IdeaBlock)).all()
    return frozenset(readable_idea_block_ids(session, request, blocks))


def _gap_key(gap) -> str:
    """Return a stable identity for a knowledge gap.

    The records a gap covers change as the corpus grows, so they cannot
    identify it: a decision about one gap would silently start applying to
    another. The gap's subject is what identifies it, and it stays short
    enough to be a decision target.
    """

    return f"{gap.gap_type}:{gap.subject}"


def _reader_decisions(session, request: Request, target_type: str) -> dict[str, GraphInsightDecision]:
    """Return the decisions the reader has made about one kind of finding."""

    principal_id = getattr(request.state, "principal_id", "")
    if not principal_id or principal_id == "anonymous":
        return {}
    rows = session.scalars(
        select(GraphInsightDecision).where(
            GraphInsightDecision.principal_id == principal_id, GraphInsightDecision.target_type == target_type
        )
    ).all()
    return {row.target_id: row for row in rows}


def _hidden_targets(decisions: dict[str, GraphInsightDecision]) -> frozenset[str]:
    """Return the targets the reader does not want surfaced right now.

    A dismissal hides a target for good. A deferral hides it only until the
    deferral expires, so a gap that was put aside comes back on its own.
    """

    now = datetime.now(UTC)
    hidden: set[str] = set()
    for target_id, decision in decisions.items():
        if decision.decision == GRAPH_DECISION_DISMISS:
            hidden.add(target_id)
            continue
        if decision.decision != GRAPH_DECISION_DEFER:
            continue
        deferred_until = decision.deferred_until
        if deferred_until is None:
            hidden.add(target_id)
            continue
        if deferred_until.tzinfo is None:
            deferred_until = deferred_until.replace(tzinfo=UTC)
        if deferred_until > now:
            hidden.add(target_id)
    return frozenset(hidden)


def _node_payload(node, community_by_member: dict[str, str], degree_by_node: dict[str, int]) -> dict[str, object]:
    return {
        "id": node.id,
        "label": node.label,
        "kind": node.kind,
        "domain_id": node.domain_id,
        "definition": node.definition,
        "degree": degree_by_node.get(node.id, 0),
        "community_id": community_by_member.get(node.id),
        "ontology_version": node.ontology_version,
    }


def _edge_payload(edge) -> dict[str, object]:
    return {
        "id": edge.id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "relation_type": edge.relation_type,
        "confidence": edge.confidence,
        "status": edge.status,
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "quotes": list(edge.quotes),
        "idea_block_ids": list(edge.idea_block_ids),
    }


def _degree_by_node(edges) -> dict[str, int]:
    degree: dict[str, int] = {}
    for edge in edges:
        degree[edge.source_id] = degree.get(edge.source_id, 0) + 1
        degree[edge.target_id] = degree.get(edge.target_id, 0) + 1
    return degree


@router.get("/subgraph")
def get_subgraph(
    request: Request,
    root_entity_id: Annotated[str | None, Query()] = None,
    depth: Annotated[int, Query(ge=1, le=MAX_SUBGRAPH_DEPTH)] = 1,
    relation_types: Annotated[list[str] | None, Query()] = None,
    include_candidates: Annotated[bool, Query()] = False,
    as_of: date | None = None,
    max_nodes: Annotated[int, Query(ge=1, le=MAX_SUBGRAPH_NODES)] = MAX_SUBGRAPH_NODES,
    max_edges: Annotated[int, Query(ge=1, le=MAX_SUBGRAPH_EDGES)] = MAX_SUBGRAPH_EDGES,
) -> dict[str, object]:
    """Return the readable graph, optionally rooted at one entity."""

    require_any_read(request)
    with get_session_factory(request)() as session:
        allowed_entity_ids = _authorized_entity_ids(session, request)
        allowed_idea_block_ids = _authorized_idea_block_ids(session, request)
        allowed_source_element_ids = permitted_source_element_ids(
            session,
            _readable_source_element_ids(session, allowed_idea_block_ids),
            permissions=request.state.permissions,
            action="read",
        )
        try:
            subgraph = authorized_subgraph(
                session,
                allowed_entity_ids=allowed_entity_ids,
                allowed_idea_block_ids=allowed_idea_block_ids,
                allowed_source_element_ids=allowed_source_element_ids,
                root_entity_ids=(root_entity_id,) if root_entity_id else None,
                relation_types=relation_types,
                statuses=_statuses(include_candidates),
                depth=depth,
                as_of=as_of,
                max_nodes=max_nodes,
                max_edges=max_edges,
            )
        except SubgraphScopeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    communities = detect_communities(subgraph.nodes, subgraph.edges)
    community_by_member = {
        member_id: community.community_id for community in communities for member_id in community.member_ids
    }
    degree_by_node = _degree_by_node(subgraph.edges)
    return {
        "nodes": [_node_payload(node, community_by_member, degree_by_node) for node in subgraph.nodes],
        "edges": [_edge_payload(edge) for edge in subgraph.edges],
        "communities": [community.as_dict() for community in communities],
        "capped": subgraph.capped,
        "depth_reached": subgraph.depth_reached,
        "ontology_version": ONTOLOGY_VERSION,
        "scope": {"entity_count": len(allowed_entity_ids), "root_entity_id": root_entity_id, "depth": depth},
    }


@router.get("/communities")
def get_communities(
    request: Request,
    root_entity_id: Annotated[str | None, Query()] = None,
    depth: Annotated[int, Query(ge=1, le=MAX_SUBGRAPH_DEPTH)] = 1,
    relation_types: Annotated[list[str] | None, Query()] = None,
    include_candidates: Annotated[bool, Query()] = False,
    as_of: date | None = None,
) -> dict[str, object]:
    """Return the communities the readable graph resolves into."""

    require_any_read(request)
    with get_session_factory(request)() as session:
        subgraph = _assemble_subgraph(
            session,
            request,
            root_entity_id=root_entity_id,
            depth=depth,
            relation_types=relation_types,
            include_candidates=include_candidates,
            as_of=as_of,
        )
    communities = detect_communities(subgraph.nodes, subgraph.edges)
    return {
        "communities": [community.as_dict() for community in communities],
        "node_count": len(subgraph.nodes),
        "edge_count": len(subgraph.edges),
        "capped": subgraph.capped,
        "ontology_version": ONTOLOGY_VERSION,
    }


@router.get("/insights")
def get_insights(
    request: Request,
    root_entity_id: Annotated[str | None, Query()] = None,
    depth: Annotated[int, Query(ge=1, le=MAX_SUBGRAPH_DEPTH)] = 1,
    relation_types: Annotated[list[str] | None, Query()] = None,
    include_candidates: Annotated[bool, Query()] = False,
    as_of: date | None = None,
    insight_limit: Annotated[int, Query(ge=1, le=50)] = DEFAULT_INSIGHT_LIMIT,
    gap_limit: Annotated[int, Query(ge=1, le=50)] = DEFAULT_GAP_LIMIT,
) -> dict[str, object]:
    """Return surprising connections and knowledge gaps in the readable graph."""

    require_any_read(request)
    with get_session_factory(request)() as session:
        subgraph = _assemble_subgraph(
            session,
            request,
            root_entity_id=root_entity_id,
            depth=depth,
            relation_types=relation_types,
            include_candidates=include_candidates,
            as_of=as_of,
        )
        hidden_connections = _hidden_targets(_reader_decisions(session, request, GRAPH_TARGET_CONNECTION))
        hidden_gaps = _hidden_targets(_reader_decisions(session, request, GRAPH_TARGET_GAP))

    communities = detect_communities(subgraph.nodes, subgraph.edges)
    connections = [
        connection
        for connection in find_surprising_connections(subgraph.nodes, subgraph.edges, communities, limit=insight_limit)
        if connection.edge_id not in hidden_connections
    ]
    gaps = []
    for gap in detect_knowledge_gaps(subgraph.nodes, subgraph.edges, communities, limit=gap_limit):
        gap_key = _gap_key(gap)
        if gap_key in hidden_gaps:
            continue
        term, context = research_seed_for_gap(gap, subgraph.nodes)
        gaps.append(
            {
                **gap.as_dict(),
                "gap_key": gap_key,
                "research_term": term,
                "research_context": context,
                "affected_records": [
                    {"id": node.id, "label": node.label, "kind": node.kind}
                    for node in subgraph.nodes
                    if node.id in gap.node_ids
                ],
            }
        )
    return {
        "surprising_connections": [connection.as_dict() for connection in connections],
        "knowledge_gaps": gaps,
        "hidden_count": len(hidden_connections) + len(hidden_gaps),
        "ontology_version": ONTOLOGY_VERSION,
    }


@router.get("/hygiene")
def get_hygiene(
    request: Request,
    root_entity_id: Annotated[str | None, Query()] = None,
    depth: Annotated[int, Query(ge=1, le=MAX_SUBGRAPH_DEPTH)] = 1,
    relation_types: Annotated[list[str] | None, Query()] = None,
    include_candidates: Annotated[bool, Query()] = False,
    as_of: date | None = None,
) -> dict[str, object]:
    """Report discoverability defects in the readable graph.

    A stranded record and a record that leads nowhere are not wrong, but both
    make the graph harder to navigate. Broken references are reported by the
    system-wide scan, because a reader cannot be told about a reference to a
    record they may not see.
    """

    require_any_read(request)
    with get_session_factory(request)() as session:
        subgraph = _assemble_subgraph(
            session,
            request,
            root_entity_id=root_entity_id,
            depth=depth,
            relation_types=relation_types,
            include_candidates=include_candidates,
            as_of=as_of,
        )
    findings = detect_graph_hygiene(subgraph.nodes, subgraph.edges)
    return {
        "findings": [finding.to_dict() for finding in findings],
        "node_count": len(subgraph.nodes),
        "edge_count": len(subgraph.edges),
    }


@router.post("/insights/decisions")
def record_insight_decision(body: DecisionRequest, request: Request) -> dict[str, object]:
    """Record what the reader wants done about a surfaced connection or gap.

    A dismissal keeps a finding away for good. A deferral keeps it away until
    the deferral expires, at which point the finding returns on its own. Both
    are decisions about the reader's attention, and neither changes the corpus.
    """

    require_any_read(request)
    if body.target_type not in GRAPH_TARGET_TYPES:
        raise HTTPException(status_code=422, detail=f"target_type must be one of {', '.join(GRAPH_TARGET_TYPES)}")
    if body.decision not in GRAPH_DECISION_KINDS:
        raise HTTPException(status_code=422, detail=f"decision must be one of {', '.join(GRAPH_DECISION_KINDS)}")
    principal_id = getattr(request.state, "principal_id", "")
    if not principal_id or principal_id == "anonymous":
        raise HTTPException(status_code=403, detail="a named principal is required to record a decision")
    deferred_until = (
        datetime.now(UTC) + timedelta(days=body.defer_days) if body.decision == GRAPH_DECISION_DEFER else None
    )
    with get_session_factory(request)() as session:
        # The decision is found by what it is about, not by its identifier, so a
        # decision carried over from an earlier release is updated rather than
        # duplicated.
        existing = session.scalar(
            select(GraphInsightDecision).where(
                GraphInsightDecision.principal_id == principal_id,
                GraphInsightDecision.target_type == body.target_type,
                GraphInsightDecision.target_id == body.target_id,
            )
        )
        if existing is None:
            session.add(
                GraphInsightDecision(
                    id=graph_decision_id(
                        {"principal_id": principal_id, "target_type": body.target_type, "target_id": body.target_id}
                    ),
                    principal_id=principal_id,
                    target_type=body.target_type,
                    target_id=body.target_id,
                    decision=body.decision,
                    deferred_until=deferred_until,
                    reason=body.reason,
                )
            )
        else:
            existing.decision = body.decision
            existing.deferred_until = deferred_until
            existing.reason = body.reason
        session.commit()
    return {
        "status": body.decision,
        "target_id": body.target_id,
        "target_type": body.target_type,
        "deferred_until": deferred_until.isoformat() if deferred_until else None,
    }


def _assemble_subgraph(
    session,
    request: Request,
    *,
    root_entity_id: str | None,
    depth: int,
    relation_types: list[str] | None,
    include_candidates: bool,
    as_of: date | None,
):
    allowed_entity_ids = _authorized_entity_ids(session, request)
    allowed_idea_block_ids = _authorized_idea_block_ids(session, request)
    allowed_source_element_ids = permitted_source_element_ids(
        session,
        _readable_source_element_ids(session, allowed_idea_block_ids),
        permissions=request.state.permissions,
        action="read",
    )
    return authorized_subgraph(
        session,
        allowed_entity_ids=allowed_entity_ids,
        allowed_idea_block_ids=allowed_idea_block_ids,
        allowed_source_element_ids=allowed_source_element_ids,
        root_entity_ids=(root_entity_id,) if root_entity_id else None,
        relation_types=relation_types,
        statuses=_statuses(include_candidates),
        depth=depth,
        as_of=as_of,
    )


def _readable_source_element_ids(session, allowed_idea_block_ids: frozenset[str]) -> tuple[str, ...]:
    """Return the source elements backing the readable Idea Blocks."""

    if not allowed_idea_block_ids:
        return ()
    rows = session.execute(
        select(ClaimEvidence.source_element_id)
        .where(ClaimEvidence.idea_block_id.in_(allowed_idea_block_ids), ClaimEvidence.status == "active")
        .distinct()
    ).all()
    return tuple(row[0] for row in rows)
