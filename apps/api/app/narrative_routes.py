"""Narrative views over the canonical record layer.

A narrative is composed on request for one domain and returned as Markdown.
It is never stored, so it cannot drift from the records it describes, and it
carries the references it was built from so that every statement can be
checked.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.authz import permitted_governance_record_types, require_permission
from app.catalog_helpers import (
    readable_entity_ids,
    readable_idea_block_ids,
    readable_source_element_ids,
    require_any_read,
)
from app.source_routes import get_session_factory
from margin_db.entity_models import Entity
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock
from margin_db.narrative import NARRATIVE_MAX_CHARS, compose_domain_narrative
from margin_db.user_models import Domain

router = APIRouter(prefix="/api/domains", tags=["narrative"])


@router.get("/{domain_id}/narrative")
def read_narrative(domain_id: str, request: Request) -> dict[str, object]:
    """Return the narrative view of one domain."""

    require_any_read(request)
    require_permission(
        permissions=request.state.permissions, domain_id=domain_id, category=None, sensitivity=None, action="read"
    )
    with get_session_factory(request)() as session:
        domain = session.get(Domain, domain_id)
        if domain is None:
            raise HTTPException(status_code=404, detail="domain not found")
        entities = session.scalars(select(Entity).where(Entity.domain_id == domain_id)).all()
        blocks = session.scalars(select(IdeaBlock).where(IdeaBlock.domain_id == domain_id)).all()
        readable_entities = frozenset(readable_entity_ids(session, request, {entity.id for entity in entities}))
        readable_blocks = frozenset(readable_idea_block_ids(session, request, blocks))
        evidence_elements = {
            element_id
            for (element_id,) in session.execute(
                select(ClaimEvidence.source_element_id).where(
                    ClaimEvidence.idea_block_id.in_(readable_blocks), ClaimEvidence.source_element_id.is_not(None)
                )
            ).all()
        }
        return compose_domain_narrative(
            session,
            domain_id,
            readable_entity_ids=readable_entities,
            readable_idea_block_ids=readable_blocks,
            readable_source_element_ids=frozenset(readable_source_element_ids(session, request, evidence_elements)),
            permitted_governance_record_types=permitted_governance_record_types(request.state.permissions, "read"),
            max_chars=NARRATIVE_MAX_CHARS,
        )
