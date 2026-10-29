"""The agent's tool surface.

Tools are read-only by default. A tool that can change something says so, and
it only runs when the run was explicitly granted the right to write. Every
invocation is recorded, so a run's trail shows what it read and what it tried
to change.

The agent reads the same governed API the rest of the product uses. It holds no
privilege that the person who started the run does not hold.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_agent.runtime import record_event
from margin_auth.permissions import PermissionSnapshot
from margin_db.agent_models import EVENT_KIND_TOOL_CALL, EVENT_KIND_TOOL_RESULT, AgentRun


class ToolNotAvailable(RuntimeError):
    """Raised when a tool is asked for that the run may not use."""


@dataclass(frozen=True, slots=True)
class ToolContext:
    """What a tool is given when it runs."""

    session: Session
    run: AgentRun
    permissions: PermissionSnapshot
    arguments: dict[str, Any]

    def argument(self, key: str, default: str = "") -> str:
        value = self.arguments.get(key)
        return value if isinstance(value, str) else default


@dataclass(frozen=True, slots=True)
class AgentTool:
    """One capability the agent may use."""

    name: str
    description: str
    handler: Callable[[ToolContext], str]
    requires_write: bool = False


def _search(context: ToolContext) -> str:
    from margin_retrieval.interfaces import RetrievalScope
    from margin_retrieval.service import RetrievalService

    domain_id = context.run.domain_id
    scope = RetrievalScope(domain_ids=(domain_id,) if domain_id else ())
    service = RetrievalService()
    result = service.search(
        context.session, query=context.argument("query"), scope=scope, permissions=context.permissions, top_k=8
    )
    return "\n".join(f"- {item.text}" for item in result.results) or "no readable records matched"


def _entity(context: ToolContext) -> str:
    from margin_db.entity_models import Entity

    entity = context.session.get(Entity, context.argument("entity_id"))
    if entity is None:
        return f"no entity with id {context.argument('entity_id')}"
    return (
        f"{entity.preferred_label} ({entity.ontology_type}) in {entity.domain_id or 'no domain'}. "
        f"{entity.definition or 'No definition recorded.'}"
    )


def _neighbors(context: ToolContext) -> str:
    from margin_db.relationship_models import EntityRelationship, RelationshipProvenance

    entity_id = context.argument("entity_id")
    rows = context.session.execute(
        select(EntityRelationship)
        .where((EntityRelationship.source_entity_id == entity_id) | (EntityRelationship.target_entity_id == entity_id))
        .order_by(EntityRelationship.id)
    ).all()
    if not rows:
        return f"{entity_id} has no readable relationships"
    lines = []
    for relationship in rows:
        quote = context.session.scalar(
            select(RelationshipProvenance.source_quote).where(RelationshipProvenance.relationship_id == relationship.id)
        )
        lines.append(
            f"{relationship.source_entity_id} -{relationship.relation_type}-> "
            f"{relationship.target_entity_id}" + (f" : {quote}" if quote else "")
        )
    return "\n".join(lines)


def _evidence(context: ToolContext) -> str:
    from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision

    idea_block_id = context.argument("idea_block_id")
    block = context.session.get(IdeaBlock, idea_block_id)
    if block is None:
        return f"no claim with id {idea_block_id}"
    revision = context.session.get(IdeaBlockRevision, block.current_revision_id) if block.current_revision_id else None
    statement = revision.statement if revision is not None else "(no current revision)"
    quotes = [
        evidence.source_quote
        for evidence in context.session.scalars(
            select(ClaimEvidence).where(ClaimEvidence.idea_block_id == idea_block_id)
        ).all()
        if evidence.source_quote
    ]
    joined = "\n".join(f"  - {quote}" for quote in quotes[:5])
    return f"{statement}\n{joined}" if joined else statement


def _narrative(context: ToolContext) -> str:
    from margin_db.charter import active_charter
    from margin_db.narrative import compose_domain_narrative
    from margin_schema.ontology import GOVERNANCE_RECORD_TYPES

    domain_id = context.argument("domain_id")
    permitted_governance = frozenset(
        record_type
        for record_type in GOVERNANCE_RECORD_TYPES
        if context.permissions.allows_category(f"governance:{record_type}", "read")
    )
    composed = compose_domain_narrative(
        context.session,
        domain_id,
        readable_entity_ids=frozenset(),
        readable_idea_block_ids=frozenset(),
        readable_source_element_ids=frozenset(),
        permitted_governance_record_types=permitted_governance,
    )
    charter = active_charter(context.session, domain_id)
    markdown = str(composed["markdown"])
    if charter is not None and charter.purpose:
        return f"{markdown}\n\n*Charter purpose:* {charter.purpose}"
    return markdown


TOOLS: tuple[AgentTool, ...] = (
    AgentTool(name="knowledge_search", description="Search the readable knowledge base.", handler=_search),
    AgentTool(name="knowledge_entity", description="Read one canonical entity.", handler=_entity),
    AgentTool(
        name="knowledge_neighbors", description="Read the verified relationships around one entity.", handler=_neighbors
    ),
    AgentTool(name="knowledge_evidence", description="Read the evidence behind one claim.", handler=_evidence),
    AgentTool(name="domain_narrative", description="Read the narrative view of a domain.", handler=_narrative),
)

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
WRITE_TOOL_NAMES = tuple(tool.name for tool in TOOLS if tool.requires_write)


def invoke_tool(context: ToolContext, name: str) -> str:
    """Run one tool on behalf of a run and record the call."""

    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ToolNotAvailable(f"no such tool: {name}")
    if tool.requires_write and not context.run.write_granted:
        raise ToolNotAvailable(f"the tool '{name}' may change things and this run may not")
    record_event(context.session, context.run, EVENT_KIND_TOOL_CALL, {"tool": name})
    try:
        text = tool.handler(context)
    except Exception as error:
        record_event(context.session, context.run, EVENT_KIND_TOOL_RESULT, {"tool": name, "error": str(error)[:500]})
        raise
    record_event(context.session, context.run, EVENT_KIND_TOOL_RESULT, {"tool": name, "characters": len(text)})
    return text
