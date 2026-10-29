"""HTTP operations for explicit, evidence-backed relationships."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.authz import (
    permitted_governance_domains,
    permitted_governance_record_types,
    permitted_source_element_ids,
    require_entity_permission,
)
from app.catalog_helpers import can_read_idea_block, readable_entity_ids, readable_idea_block_ids, require_idea_block
from app.source_routes import get_session_factory
from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord
from margin_db.knowledge_models import IdeaBlock
from margin_db.relationship_models import RelationshipProvenance
from margin_db.relationship_queries import (
    MAX_RESULTS_PER_DEPTH,
    MAX_TRAVERSAL_DEPTH,
    MAX_TRAVERSAL_RESULTS,
    IdeaBlockRelationship,
    ImpactRecord,
    RelationshipDatabaseConfigurationError,
    RelationshipEdge,
    RelationshipQueryError,
    bounded_impact,
    bounded_path,
    direct_neighbors,
    governed_entity_links,
    related_idea_blocks,
    relationship_provenance_scope,
    with_authorized_provenance,
)

router = APIRouter(prefix="/api/relationships", tags=["relationships"])


class PathRequest(BaseModel):
    source_entity_id: str
    target_entity_id: str
    max_depth: int = Field(default=3, ge=1, le=MAX_TRAVERSAL_DEPTH)
    relation_types: list[str] | None = None
    include_candidates: bool = False
    as_of: date | None = None
    results_per_depth: int = Field(default=MAX_RESULTS_PER_DEPTH, ge=1, le=MAX_RESULTS_PER_DEPTH)
    total_limit: int = Field(default=MAX_TRAVERSAL_RESULTS, ge=1, le=MAX_TRAVERSAL_RESULTS)


def _statuses(include_candidates: bool) -> tuple[str, ...]:
    return ("verified", "candidate") if include_candidates else ("verified",)


def _authorized_entity_ids(session: Session, request: Request) -> frozenset[str]:
    entities = session.scalars(select(Entity).where(Entity.lifecycle_status == "active")).all()
    return frozenset(readable_entity_ids(session, request, {entity.id for entity in entities}))


def _authorized_idea_block_ids(session: Session, request: Request) -> frozenset[str]:
    blocks = session.scalars(select(IdeaBlock)).all()
    return frozenset(readable_idea_block_ids(session, request, blocks))


def _attach_authorized_provenance(
    session: Session, request: Request, edges: Iterable[RelationshipEdge], *, as_of: date | None = None
) -> tuple[RelationshipEdge, ...]:
    edge_values = tuple(edges)
    relationship_ids = {edge.relationship_id for edge in edge_values}
    if not relationship_ids:
        return edge_values
    provenance_relationship_ids, _ = relationship_provenance_scope(session, relationship_ids)
    candidates = session.scalars(
        select(RelationshipProvenance)
        .where(RelationshipProvenance.relationship_id.in_(provenance_relationship_ids))
        .where(RelationshipProvenance.status == "verified")
    ).all()
    block_ids = {candidate.idea_block_id for candidate in candidates}
    blocks = session.scalars(select(IdeaBlock).where(IdeaBlock.id.in_(block_ids))).all() if block_ids else []
    readable_blocks = frozenset(readable_idea_block_ids(session, request, blocks))
    readable_elements = permitted_source_element_ids(
        session,
        (candidate.source_element_id for candidate in candidates),
        permissions=request.state.permissions,
        action="read",
    )
    return with_authorized_provenance(
        session,
        edges=edge_values,
        allowed_idea_block_ids=readable_blocks,
        allowed_source_element_ids=readable_elements,
        as_of=as_of,
    )


def _authorized_governance_record_ids(session: Session, request: Request) -> frozenset[str]:
    record_types = permitted_governance_record_types(request.state.permissions, "read")
    domains = permitted_governance_domains(request.state.permissions, "read")
    if not record_types or domains == frozenset():
        return frozenset()
    query = select(GovernanceRecord.id).where(GovernanceRecord.record_type.in_(record_types))
    if domains is not None:
        query = query.where(GovernanceRecord.domain_id.in_(domains))
    return frozenset(session.scalars(query).all())


def _edge_payload(edge: RelationshipEdge) -> dict[str, object]:
    return {
        "relationship_id": edge.relationship_id,
        "source": asdict(edge.source),
        "target": asdict(edge.target),
        "relation_type": edge.relation_type,
        "confidence": edge.confidence,
        "status": edge.status,
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "direction": edge.direction,
        "hop": edge.hop,
        "provenance": [
            {
                "id": provenance.id,
                "idea_block_id": provenance.idea_block_id,
                "source_element_id": provenance.source_element_id,
                "source_quote": provenance.source_quote,
                "polarity": provenance.polarity,
                "status": provenance.status,
                "valid_from": provenance.valid_from.isoformat() if provenance.valid_from else None,
                "valid_to": provenance.valid_to.isoformat() if provenance.valid_to else None,
            }
            for provenance in edge.provenance
        ],
    }


def _raise_query_error(exc: Exception) -> None:
    if isinstance(exc, RelationshipQueryError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, RelationshipDatabaseConfigurationError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, DBAPIError) and "statement timeout" in str(exc).lower():
        raise HTTPException(status_code=504, detail="relationship query exceeded its deadline") from exc
    raise exc


@router.get("/neighbors/{entity_id}")
def get_neighbors(
    entity_id: str,
    request: Request,
    relation_types: Annotated[list[str] | None, Query()] = None,
    include_candidates: Annotated[bool, Query()] = False,
    as_of: date | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_TRAVERSAL_RESULTS)] = MAX_TRAVERSAL_RESULTS,
) -> dict[str, object]:
    with get_session_factory(request)() as session:
        require_entity_permission(session, entity_id, permissions=request.state.permissions, action="read")
        try:
            result = direct_neighbors(
                session,
                entity_id=entity_id,
                allowed_entity_ids=_authorized_entity_ids(session, request),
                relation_types=relation_types,
                statuses=_statuses(include_candidates),
                as_of=as_of,
                limit=limit,
            )
            edges = _attach_authorized_provenance(session, request, result.edges, as_of=as_of)
        except (RelationshipQueryError, RelationshipDatabaseConfigurationError, DBAPIError) as exc:
            _raise_query_error(exc)
    return {"entity_id": entity_id, "edges": [_edge_payload(edge) for edge in edges], "capped": result.capped}


@router.post("/path")
def get_path(body: PathRequest, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        require_entity_permission(session, body.source_entity_id, permissions=request.state.permissions, action="read")
        require_entity_permission(session, body.target_entity_id, permissions=request.state.permissions, action="read")
        try:
            result = bounded_path(
                session,
                source_entity_id=body.source_entity_id,
                target_entity_id=body.target_entity_id,
                allowed_entity_ids=_authorized_entity_ids(session, request),
                max_depth=body.max_depth,
                relation_types=body.relation_types,
                statuses=_statuses(body.include_candidates),
                as_of=body.as_of,
                results_per_depth=body.results_per_depth,
                total_limit=body.total_limit,
            )
            edges = _attach_authorized_provenance(session, request, result.edges, as_of=body.as_of)
            if len(edges) != len(result.edges):
                # Never return a partial path: an omitted edge means the
                # complete relationship chain is not readable/asserted.
                edges = ()
        except (RelationshipQueryError, RelationshipDatabaseConfigurationError, DBAPIError) as exc:
            _raise_query_error(exc)
    return {
        "source_entity_id": body.source_entity_id,
        "target_entity_id": body.target_entity_id,
        "path": [_edge_payload(edge) for edge in edges],
        "found": bool(edges),
        "capped": result.capped,
    }


@router.get("/impact/{entity_id}")
def get_impact(
    entity_id: str,
    request: Request,
    max_depth: Annotated[int, Query(ge=1, le=MAX_TRAVERSAL_DEPTH)] = 2,
    direction: Annotated[str, Query(pattern="^(inbound|outbound|both)$")] = "both",
    relation_types: Annotated[list[str] | None, Query()] = None,
    include_candidates: Annotated[bool, Query()] = False,
    as_of: date | None = None,
    results_per_depth: Annotated[int, Query(ge=1, le=MAX_RESULTS_PER_DEPTH)] = MAX_RESULTS_PER_DEPTH,
    total_limit: Annotated[int, Query(ge=1, le=MAX_TRAVERSAL_RESULTS)] = MAX_TRAVERSAL_RESULTS,
) -> dict[str, object]:
    with get_session_factory(request)() as session:
        require_entity_permission(session, entity_id, permissions=request.state.permissions, action="read")
        try:
            impacts, capped = bounded_impact(
                session,
                entity_id=entity_id,
                allowed_entity_ids=_authorized_entity_ids(session, request),
                max_depth=max_depth,
                direction=direction,
                relation_types=relation_types,
                statuses=_statuses(include_candidates),
                as_of=as_of,
                results_per_depth=results_per_depth,
                total_limit=total_limit,
            )
            all_edges = _attach_authorized_provenance(
                session, request, (edge for impact in impacts for edge in impact.path), as_of=as_of
            )
        except (RelationshipQueryError, RelationshipDatabaseConfigurationError, DBAPIError) as exc:
            _raise_query_error(exc)
    edges_by_id = {edge.relationship_id: edge for edge in all_edges}
    visible_impacts = tuple(
        impact for impact in impacts if all(edge.relationship_id in edges_by_id for edge in impact.path)
    )
    return {
        "entity_id": entity_id,
        "impacts": [_impact_payload(impact, edges_by_id) for impact in visible_impacts],
        "capped": capped,
    }


@router.get("/governed-entity/{entity_id}")
def get_governed_entity(
    entity_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_TRAVERSAL_RESULTS)] = MAX_TRAVERSAL_RESULTS,
) -> dict[str, object]:
    with get_session_factory(request)() as session:
        require_entity_permission(session, entity_id, permissions=request.state.permissions, action="read")
        try:
            result = governed_entity_links(
                session,
                entity_id=entity_id,
                allowed_entity_ids=_authorized_entity_ids(session, request),
                allowed_governance_record_ids=_authorized_governance_record_ids(session, request),
                limit=limit,
            )
        except (RelationshipQueryError, DBAPIError) as exc:
            _raise_query_error(exc)
    return {
        "entity_id": entity_id,
        "governance_records": [asdict(link) for link in result.links],
        "capped": result.capped,
    }


@router.get("/related-idea-block/{idea_block_id}")
def get_related_idea_blocks(
    idea_block_id: str,
    request: Request,
    include_candidates: Annotated[bool, Query()] = False,
    as_of: date | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_TRAVERSAL_RESULTS)] = MAX_TRAVERSAL_RESULTS,
) -> dict[str, object]:
    with get_session_factory(request)() as session:
        block = require_idea_block(session, idea_block_id)
        if not can_read_idea_block(session, request, block):
            raise HTTPException(status_code=403, detail="missing read access for IdeaBlock")
        try:
            result = related_idea_blocks(
                session,
                idea_block_id=idea_block_id,
                allowed_idea_block_ids=_authorized_idea_block_ids(session, request),
                statuses=_statuses(include_candidates),
                as_of=as_of,
                limit=limit,
            )
        except RelationshipQueryError as exc:
            _raise_query_error(exc)
    return {
        "idea_block_id": idea_block_id,
        "relations": [_idea_block_relationship_payload(relation) for relation in result.relations],
        "capped": result.capped,
    }


def _impact_payload(impact: ImpactRecord, edges_by_id: dict[str, RelationshipEdge]) -> dict[str, object]:
    return {
        "entity": asdict(impact.entity),
        "hop": impact.hop,
        "relation_type": impact.relation_type,
        "path": [_edge_payload(edges_by_id[edge.relationship_id]) for edge in impact.path],
    }


def _idea_block_relationship_payload(relation: IdeaBlockRelationship) -> dict[str, object]:
    return {
        "relationship_id": relation.relationship_id,
        "source": asdict(relation.source),
        "target": asdict(relation.target),
        "relation_type": relation.relation_type,
        "direction": relation.direction,
        "status": relation.status,
        "confidence": relation.confidence,
        "valid_from": relation.valid_from.isoformat() if relation.valid_from else None,
        "valid_to": relation.valid_to.isoformat() if relation.valid_to else None,
        "decision_provenance": relation.decision_provenance,
    }
