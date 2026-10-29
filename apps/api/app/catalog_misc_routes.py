from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.catalog_helpers import (
    can_read_entity as _can_read_entity,
    can_read_idea_block as _can_read_idea_block,
    idea_block_dict as _idea_block_dict,
    require_any_read as _require_any_read,
)
from app.source_routes import get_session_factory
from margin_auth.permissions import WILDCARD_SCOPE
from margin_db.entity_models import Entity
from margin_db.knowledge_models import IdeaBlock
from margin_db.relationship_models import EntityRelationship
from margin_db.source_models import SourceDocument
from margin_db.user_models import Domain

router = APIRouter(prefix="/api", tags=["catalog"])


class DomainPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    owner: str | None = None
    steward: str | None = None
    description: str | None = None


def _require_domain_catalog_write(request: Request) -> None:
    permissions = getattr(request.state, "permissions", None)
    if permissions is None:
        return
    if not permissions.allows_domain(WILDCARD_SCOPE, "write"):
        raise HTTPException(status_code=403, detail="missing write access for domain catalog")


@router.get("/domains")
def list_domains(
    request: Request, permission: str = Query(default="read", pattern="^(read|write|review)$")
) -> dict[str, object]:
    session_factory = get_session_factory(request)
    permissions = getattr(request.state, "permissions", None)
    with session_factory() as session:
        rows = session.execute(select(Domain).order_by(Domain.name)).scalars()
        return {
            "domains": [
                {
                    "id": row.id,
                    "name": row.name,
                    "owner": row.owner,
                    "steward": row.steward,
                    "status": row.status,
                    "description": row.description,
                }
                for row in rows
                if permissions is None or permissions.allows_domain(row.id, permission)
            ]
        }


@router.post("/domains", status_code=201)
def upsert_domain(body: DomainPayload, request: Request) -> dict[str, object]:
    _require_domain_catalog_write(request)
    domain_id = body.id.strip()
    domain_name = body.name.strip()
    if not domain_id or not domain_name:
        raise HTTPException(status_code=422, detail="domain id and name are required")
    session_factory = get_session_factory(request)
    with session_factory() as session:
        existing_name = session.execute(select(Domain).where(Domain.name == domain_name)).scalar_one_or_none()
        if existing_name is not None and existing_name.id != domain_id:
            raise HTTPException(status_code=409, detail="domain name already exists")
        domain = session.get(Domain, domain_id)
        if domain is None:
            domain = Domain(id=domain_id, name=domain_name)
            session.add(domain)
        domain.name = domain_name
        domain.owner = body.owner
        domain.steward = body.steward
        domain.description = body.description
        session.commit()
        return {
            "domain": {
                "id": domain.id,
                "name": domain.name,
                "owner": domain.owner,
                "steward": domain.steward,
                "status": domain.status,
                "description": domain.description,
            }
        }


@router.delete("/domains/{domain_id}")
def delete_domain(domain_id: str, request: Request) -> dict[str, object]:
    _require_domain_catalog_write(request)
    session_factory = get_session_factory(request)
    with session_factory() as session:
        domain = session.get(Domain, domain_id)
        if domain is None:
            raise HTTPException(status_code=404, detail="domain not found")
        source_count = int(
            session.scalar(
                select(func.count()).select_from(SourceDocument).where(SourceDocument.domain_id == domain_id)
            )
            or 0
        )
        if source_count:
            raise HTTPException(status_code=409, detail="domain is still referenced by source documents")
        session.delete(domain)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=409, detail="domain is still referenced by access or governance records"
            ) from exc
        return {"status": "deleted", "domain_id": domain_id}


C4_HIERARCHY_RELATIONSHIPS = ("contains", "part_of")


@router.get("/c4/tree")
def c4_tree(request: Request) -> dict[str, object]:
    _require_any_read(request)
    session_factory = get_session_factory(request)
    with session_factory() as session:
        rows = session.execute(
            select(Entity)
            .where(Entity.lifecycle_status == "active")
            .order_by(Entity.ontology_type, Entity.preferred_label)
        ).scalars()
        readable = [row for row in rows if _can_read_entity(session, request, row)]
        readable_ids = {row.id for row in readable}
        nodes = [
            {
                "id": row.id,
                "level": row.ontology_type,
                "name": row.preferred_label,
                "kind": row.ontology_type,
                "domain_id": row.domain_id,
                "category": row.ontology_type,
                "status": row.lifecycle_status,
            }
            for row in readable
        ]
        edges = [
            {
                "id": relationship.id,
                "source_id": relationship.source_entity_id,
                "target_id": relationship.target_entity_id,
                "relation_type": relationship.relation_type,
                "status": relationship.status,
            }
            for relationship in session.scalars(
                select(EntityRelationship)
                .where(EntityRelationship.relation_type.in_(C4_HIERARCHY_RELATIONSHIPS))
                .where(EntityRelationship.source_entity_id.in_(readable_ids))
                .where(EntityRelationship.target_entity_id.in_(readable_ids))
                .order_by(EntityRelationship.id)
            ).all()
            if readable_ids
        ]
        return {"nodes": nodes, "edges": edges}


@router.get("/c4/overlays")
def c4_overlays(request: Request) -> dict[str, object]:
    _require_any_read(request)
    session_factory = get_session_factory(request)
    with session_factory() as session:
        rows = session.execute(
            select(Entity)
            .where(Entity.lifecycle_status == "active")
            .order_by(Entity.ontology_type, Entity.preferred_label)
        ).scalars()
        levels: dict[str, dict[str, int]] = {}
        for row in rows:
            if not _can_read_entity(session, request, row):
                continue
            domain_key = row.domain_id or "unassigned"
            counts = levels.setdefault(domain_key, {})
            counts[row.ontology_type] = counts.get(row.ontology_type, 0) + 1
        overlays = [
            {
                "domain_id": domain_id,
                "levels": [{"level": level, "count": count} for level, count in sorted(counts.items())],
                "record_count": sum(counts.values()),
            }
            for domain_id, counts in sorted(levels.items())
        ]
    return {"overlays": overlays}


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        block = session.get(IdeaBlock, evidence_id)
        if block is None:
            raise HTTPException(status_code=404, detail="IdeaBlock not found")
        if not _can_read_idea_block(session, request, block):
            raise HTTPException(status_code=403, detail="missing read access for IdeaBlock")
        return {"idea_block": _idea_block_dict(session, request, block)}
