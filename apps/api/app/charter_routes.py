"""Domain charter and briefing operations.

A charter is authored by a domain steward and versioned. A briefing is
composed from the corpus and is never stored. Both are scoped to the domain
they describe, and both require the caller to be able to read that domain.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.authz import permitted_governance_record_types, require_permission
from app.catalog_helpers import (
    readable_entity_ids,
    readable_idea_block_ids,
    readable_source_element_ids,
    require_any_read,
)
from app.source_routes import get_session_factory
from margin_db.charter import active_charter, charter_history, compose_domain_briefing, save_charter
from margin_db.charter_models import CHARTER_STATUS_DRAFT, DomainCharter
from margin_db.entity_models import Entity, EntityMention
from margin_db.knowledge_models import IdeaBlock
from margin_db.known_unknowns import detect_known_unknowns
from margin_db.user_models import Domain

router = APIRouter(prefix="/api/domains", tags=["charter"])


class CharterRequest(BaseModel):
    purpose: str = Field(default="", max_length=4_000)
    scope_inclusions: str = Field(default="", max_length=4_000)
    scope_exclusions: str = Field(default="", max_length=4_000)
    terminology: str = Field(default="", max_length=4_000)
    activate: bool = True


def _charter_payload(charter: DomainCharter | None) -> dict[str, object] | None:
    if charter is None:
        return None
    return {
        "id": charter.id,
        "domain_id": charter.domain_id,
        "version": charter.version,
        "status": charter.status,
        "author_principal_id": charter.author_principal_id,
        "purpose": charter.purpose,
        "scope_inclusions": charter.scope_inclusions,
        "scope_exclusions": charter.scope_exclusions,
        "terminology": charter.terminology,
        "created_at": charter.created_at.isoformat() if charter.created_at else None,
        "activated_at": charter.activated_at.isoformat() if charter.activated_at else None,
        "superseded_at": charter.superseded_at.isoformat() if charter.superseded_at else None,
    }


def _require_domain(request: Request, domain_id: str, action: str) -> None:
    require_permission(
        permissions=request.state.permissions, domain_id=domain_id, category=None, sensitivity=None, action=action
    )


def _may_write(request: Request, domain_id: str) -> bool:
    """Whether the caller may author a charter for this domain."""

    permissions = getattr(request.state, "permissions", None)
    if permissions is None:
        return False
    return permissions.allows_domain(domain_id, "write")


@router.get("/{domain_id}/charter")
def read_charter(domain_id: str, request: Request) -> dict[str, object]:
    """Return the charter in force for a domain and its authored history."""

    require_any_read(request)
    _require_domain(request, domain_id, "read")
    with get_session_factory(request)() as session:
        domain = session.get(Domain, domain_id)
        if domain is None:
            raise HTTPException(status_code=404, detail="domain not found")
        history = charter_history(session, domain_id)
        if not _may_write(request, domain_id):
            history = tuple(charter for charter in history if charter.status != CHARTER_STATUS_DRAFT)
        return {
            "domain_id": domain_id,
            "active": _charter_payload(active_charter(session, domain_id)),
            "history": [_charter_payload(charter) for charter in history],
        }


@router.put("/{domain_id}/charter")
def author_charter(domain_id: str, body: CharterRequest, request: Request) -> dict[str, object]:
    """Author a new charter version for a domain."""

    require_any_read(request)
    _require_domain(request, domain_id, "write")
    principal_id = getattr(request.state, "principal_id", "")
    if not principal_id or principal_id == "anonymous":
        raise HTTPException(status_code=403, detail="a named principal is required to author a charter")
    with get_session_factory(request)() as session:
        domain = session.get(Domain, domain_id)
        if domain is None:
            raise HTTPException(status_code=404, detail="domain not found")
        charter = save_charter(
            session,
            domain_id=domain_id,
            author_principal_id=principal_id,
            purpose=body.purpose,
            scope_inclusions=body.scope_inclusions,
            scope_exclusions=body.scope_exclusions,
            terminology=body.terminology,
            activate=body.activate,
        )
        session.commit()
        return {"charter": _charter_payload(charter)}


@router.get("/{domain_id}/briefing")
def read_briefing(domain_id: str, request: Request) -> dict[str, object]:
    """Return the composed briefing for a domain."""

    require_any_read(request)
    _require_domain(request, domain_id, "read")
    with get_session_factory(request)() as session:
        domain = session.get(Domain, domain_id)
        if domain is None:
            raise HTTPException(status_code=404, detail="domain not found")
        entities = session.scalars(select(Entity).where(Entity.domain_id == domain_id)).all()
        blocks = session.scalars(select(IdeaBlock).where(IdeaBlock.domain_id == domain_id)).all()
        return compose_domain_briefing(
            session,
            domain_id,
            readable_entity_ids=frozenset(readable_entity_ids(session, request, {entity.id for entity in entities})),
            readable_idea_block_ids=frozenset(readable_idea_block_ids(session, request, blocks)),
            permitted_governance_record_types=permitted_governance_record_types(request.state.permissions, "read"),
        )


@router.get("/{domain_id}/known-unknowns")
def list_known_unknowns(domain_id: str, request: Request) -> dict[str, object]:
    """List the names this domain refers to that have no record of their own."""

    require_any_read(request)
    _require_domain(request, domain_id, "read")
    with get_session_factory(request)() as session:
        domain = session.get(Domain, domain_id)
        if domain is None:
            raise HTTPException(status_code=404, detail="domain not found")
        mention_elements = {
            element_id
            for (element_id,) in session.execute(
                select(EntityMention.source_element_id).where(
                    EntityMention.domain_id == domain_id, EntityMention.source_element_id.is_not(None)
                )
            ).all()
        }
        known_unknowns = detect_known_unknowns(
            session,
            domain_id,
            readable_source_element_ids=frozenset(readable_source_element_ids(session, request, mention_elements)),
        )
        return {"domain_id": domain_id, "known_unknowns": [known_unknown.as_dict() for known_unknown in known_unknowns]}
