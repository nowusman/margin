"""Reading and authoring domain charters, and composing domain briefings.

A charter is authored input: a steward states what a domain is for, what
belongs in it, and what does not. A briefing is composed output: a bounded
summary of what the corpus currently says about a domain. They are different
things and are kept apart deliberately, because one is authored and versioned
while the other is derived and disposable.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from margin_db.charter_models import (
    CHARTER_STATUS_ACTIVE,
    CHARTER_STATUS_DRAFT,
    CHARTER_STATUS_SUPERSEDED,
    DomainCharter,
)
from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord
from margin_db.knowledge_models import IdeaBlock
from margin_db.user_models import Domain
from margin_schema.ids import domain_charter_id

BRIEFING_MAX_CHARS = 4_000
BRIEFING_SECTION_GAP = "\n\n"


def active_charter(session: Session, domain_id: str) -> DomainCharter | None:
    """Return the charter currently in force for a domain, if there is one."""

    if not domain_id:
        return None
    return session.scalars(
        select(DomainCharter)
        .where(DomainCharter.domain_id == domain_id, DomainCharter.status == CHARTER_STATUS_ACTIVE)
        .order_by(DomainCharter.version.desc())
    ).first()


def charter_history(session: Session, domain_id: str) -> tuple[DomainCharter, ...]:
    """Return every authored version of a domain charter, newest first."""

    return tuple(
        session.scalars(
            select(DomainCharter).where(DomainCharter.domain_id == domain_id).order_by(DomainCharter.version.desc())
        ).all()
    )


def next_charter_version(session: Session, domain_id: str) -> int:
    """Return the version number a newly authored charter should take."""

    current = session.scalar(select(func.max(DomainCharter.version)).where(DomainCharter.domain_id == domain_id))
    return int(current or 0) + 1


def save_charter(
    session: Session,
    *,
    domain_id: str,
    author_principal_id: str,
    purpose: str,
    scope_inclusions: str,
    scope_exclusions: str,
    terminology: str,
    activate: bool,
) -> DomainCharter:
    """Author a new charter version, optionally putting it into force."""

    version = next_charter_version(session, domain_id)
    charter = DomainCharter(
        id=domain_charter_id({"domain_id": domain_id, "version": version}),
        domain_id=domain_id,
        version=version,
        status=CHARTER_STATUS_ACTIVE if activate else CHARTER_STATUS_DRAFT,
        author_principal_id=author_principal_id,
        purpose=purpose.strip(),
        scope_inclusions=scope_inclusions.strip(),
        scope_exclusions=scope_exclusions.strip(),
        terminology=terminology.strip(),
        activated_at=datetime.now(UTC) if activate else None,
    )
    if activate:
        _supersede_active(session, domain_id=domain_id, keep_version=version)
    session.add(charter)
    session.flush()
    return charter


def _supersede_active(session: Session, *, domain_id: str, keep_version: int) -> None:
    """Retire the charter currently in force so only one is ever active."""

    now = datetime.now(UTC)
    for existing in session.scalars(
        select(DomainCharter).where(
            DomainCharter.domain_id == domain_id,
            DomainCharter.status == CHARTER_STATUS_ACTIVE,
            DomainCharter.version != keep_version,
        )
    ).all():
        existing.status = CHARTER_STATUS_SUPERSEDED
        existing.superseded_at = now


def charter_prompt_section(charter: DomainCharter | None) -> str:
    """Render a charter for injection into an extraction prompt."""

    if charter is None:
        return ""
    parts: list[str] = []
    if charter.purpose:
        parts.append(f"Domain purpose: {charter.purpose}")
    if charter.scope_inclusions:
        parts.append(f"In scope: {charter.scope_inclusions}")
    if charter.scope_exclusions:
        parts.append(f"Out of scope: {charter.scope_exclusions}")
    if charter.terminology:
        parts.append(f"Preferred terminology: {charter.terminology}")
    if not parts:
        return ""
    header = f"This domain has a charter (version {charter.version}). Treat it as scope guidance."
    return header + BRIEFING_SECTION_GAP + "\n".join(parts)


def compose_domain_briefing(
    session: Session,
    domain_id: str,
    *,
    readable_entity_ids: frozenset[str] | None = None,
    readable_idea_block_ids: frozenset[str] | None = None,
    permitted_governance_record_types: frozenset[str] | None = None,
    max_chars: int = BRIEFING_MAX_CHARS,
) -> dict[str, object]:
    """Compose a bounded briefing for a domain from canonical records.

    The briefing describes what the corpus currently holds. It never asserts
    that the corpus is complete or correct, because it cannot know that. It
    describes only what the reader may read, so a briefing cannot report a
    record the reader was not allowed to see.
    """

    domain = session.get(Domain, domain_id) if domain_id else None
    charter = active_charter(session, domain_id)

    entity_query = select(Entity.ontology_type).where(
        Entity.domain_id == domain_id, Entity.lifecycle_status == "active"
    )
    if readable_entity_ids is not None:
        entity_query = entity_query.where(Entity.id.in_(readable_entity_ids))
    entity_counts = Counter(session.execute(entity_query).scalars().all())
    entity_counts.pop(None, None)

    idea_block_query = select(func.count(IdeaBlock.id)).where(IdeaBlock.domain_id == domain_id)
    if readable_idea_block_ids is not None:
        idea_block_query = idea_block_query.where(IdeaBlock.id.in_(readable_idea_block_ids))
    idea_block_count = int(session.scalar(idea_block_query) or 0)

    governance_query = select(GovernanceRecord.record_type).where(GovernanceRecord.domain_id == domain_id)
    if permitted_governance_record_types is not None:
        governance_query = governance_query.where(GovernanceRecord.record_type.in_(permitted_governance_record_types))
    governance_counts = Counter(session.execute(governance_query).scalars().all())
    governance_counts.pop(None, None)

    sections: list[str] = []
    if domain is not None:
        heading = f"Domain: {domain.name}"
        if domain.description:
            heading = f"{heading} — {domain.description}"
        sections.append(heading)
    if charter is not None and charter.purpose:
        sections.append(f"Charter (v{charter.version}): {charter.purpose}")
    if entity_counts:
        kinds = ", ".join(f"{count} {kind}" for kind, count in sorted(entity_counts.items()))
        sections.append(f"Records: {kinds}")
    if idea_block_count:
        sections.append(f"Claims extracted: {idea_block_count}")
    if governance_counts:
        kinds = ", ".join(f"{count} {kind}" for kind, count in sorted(governance_counts.items()))
        sections.append(f"Governance records: {kinds}")

    text = BRIEFING_SECTION_GAP.join(sections)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip() + "…"

    return {
        "domain_id": domain_id,
        "briefing": text,
        "truncated": truncated,
        "charter_version": charter.version if charter is not None else None,
        "entity_count": sum(entity_counts.values()),
        "idea_block_count": idea_block_count,
        "governance_counts": dict(sorted(governance_counts.items())),
    }
