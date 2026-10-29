"""Narrative views composed from canonical records.

A narrative is generated on request and discarded. Its structure is
deterministic: the same corpus always produces the same shape, because the
headings, the ordering and the selection rules do not vary. Its wording comes
from the records themselves rather than from a generator, which means a
narrative can never assert something the corpus does not contain.

Everything a narrative states is attributed. Verified records are shown as
verified; authored material such as a charter is marked as authored; anything
that is only inferred is marked as inferred and never presented as fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.charter import active_charter
from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision
from margin_db.user_models import Domain

NARRATIVE_MAX_CHARS = 40_000
MAX_RECORDS_PER_SECTION = 25
MAX_CLAIMS = 200
MAX_DECISIONS = 15

_DECISION_TYPES = ("adr",)


@dataclass(frozen=True, slots=True)
class NarrativeReference:
    """A record a narrative statement is drawn from."""

    kind: str
    id: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "label": self.label}


def compose_domain_narrative(
    session: Session,
    domain_id: str,
    *,
    readable_entity_ids: frozenset[str],
    readable_idea_block_ids: frozenset[str],
    readable_source_element_ids: frozenset[str],
    permitted_governance_record_types: frozenset[str],
    max_chars: int = NARRATIVE_MAX_CHARS,
) -> dict[str, object]:
    """Compose the narrative view of one domain from readable records."""

    domain = session.get(Domain, domain_id)
    charter = active_charter(session, domain_id)

    entities = tuple(
        session.scalars(
            select(Entity)
            .where(Entity.domain_id == domain_id, Entity.lifecycle_status == "active")
            .order_by(Entity.ontology_type, Entity.preferred_label, Entity.id)
        ).all()
    )
    readable_entities = tuple(entity for entity in entities if entity.id in readable_entity_ids)

    claims = _claims(
        session,
        domain_id,
        readable_idea_block_ids,
        publication_status="verified",
        readable_source_element_ids=readable_source_element_ids,
    )
    inferred = _claims(
        session,
        domain_id,
        readable_idea_block_ids,
        publication_status="candidate",
        readable_source_element_ids=readable_source_element_ids,
    )
    decisions = _decisions(session, domain_id, permitted_governance_record_types)

    sections: list[dict[str, object]] = []
    references: list[NarrativeReference] = []
    lines: list[str] = []

    title = domain.name if domain is not None else domain_id
    lines.append(f"# {title}")
    references.append(NarrativeReference(kind="domain", id=domain_id, label=title))

    if charter is not None and charter.purpose:
        lines.append("")
        lines.append(f"*Charter (v{charter.version}, authored):* {charter.purpose}")
        references.append(NarrativeReference(kind="charter", id=charter.id, label=f"Charter v{charter.version}"))
    elif domain is not None and domain.description:
        lines.append("")
        lines.append(domain.description)

    grouped: dict[str, list[Entity]] = {}
    for entity in readable_entities:
        grouped.setdefault(entity.ontology_type, []).append(entity)

    if grouped:
        lines.append("")
        lines.append("## What this domain covers")
        for kind in sorted(grouped):
            members = grouped[kind]
            lines.append("")
            lines.append(f"### {kind.replace('_', ' ').title()} ({len(members)})")
            for entity in members[:MAX_RECORDS_PER_SECTION]:
                description = (entity.definition or "").strip()
                suffix = f" — {description}" if description else ""
                lines.append(f"- **{entity.preferred_label}**{suffix}")
                references.append(NarrativeReference(kind="entity", id=entity.id, label=entity.preferred_label))
            if len(members) > MAX_RECORDS_PER_SECTION:
                lines.append(f"- _… and {len(members) - MAX_RECORDS_PER_SECTION} more_")

    if claims:
        lines.append("")
        lines.append("## Verified claims")
        for statement, reference in claims[:MAX_CLAIMS]:
            lines.append(f"- {statement}")
            references.append(reference)
        sections.append({"title": "Verified claims", "count": len(claims)})

    if inferred:
        lines.append("")
        lines.append("## Claims awaiting verification")
        lines.append("")
        lines.append(
            "_These statements are drawn from the corpus and are not yet verified. "
            "They are shown so the domain's gaps are visible, not as established facts._"
        )
        for statement, reference in inferred[:MAX_CLAIMS]:
            lines.append(f"- {statement}")
            references.append(reference)
        sections.append({"title": "Claims awaiting verification", "count": len(inferred)})

    if decisions:
        lines.append("")
        lines.append("## Decisions")
        for title_text, reference in decisions[:MAX_DECISIONS]:
            lines.append(f"- **{title_text}**")
            references.append(reference)
        sections.append({"title": "Decisions", "count": len(decisions)})

    if not grouped and not claims and not inferred and not decisions:
        lines.append("")
        lines.append("_No readable records have been extracted for this domain yet._")

    markdown = "\n".join(lines).strip()
    truncated = len(markdown) > max_chars
    if truncated:
        markdown = markdown[:max_chars].rsplit("\n", 1)[0].rstrip() + "\n\n_… truncated._"

    return {
        "domain_id": domain_id,
        "title": title,
        "markdown": markdown,
        "truncated": truncated,
        "charter_version": charter.version if charter is not None else None,
        "entity_count": len(readable_entities),
        "claim_count": len(claims),
        "inferred_claim_count": len(inferred),
        "decision_count": len(decisions),
        "references": [reference.as_dict() for reference in references],
    }


def _claims(
    session: Session,
    domain_id: str,
    readable_idea_block_ids: frozenset[str],
    *,
    publication_status: str,
    readable_source_element_ids: frozenset[str],
) -> tuple[tuple[str, NarrativeReference], ...]:
    """Return the statements of one knowledge state with a readable evidence quote."""

    if not readable_idea_block_ids:
        return ()
    rows = session.execute(
        select(IdeaBlockRevision.statement, IdeaBlockRevision.idea_block_id)
        .join(IdeaBlock, IdeaBlock.id == IdeaBlockRevision.idea_block_id)
        .where(
            IdeaBlock.domain_id == domain_id,
            IdeaBlockRevision.idea_block_id.in_(readable_idea_block_ids),
            IdeaBlockRevision.publication_status == publication_status,
        )
        .order_by(IdeaBlockRevision.id)
    ).all()
    if not rows:
        return ()

    evidence_by_block: dict[str, str] = {}
    for evidence in session.scalars(
        select(ClaimEvidence)
        .where(
            ClaimEvidence.idea_block_id.in_({row[1] for row in rows}),
            ClaimEvidence.status == "active",
            ClaimEvidence.source_element_id.in_(readable_source_element_ids),
        )
        .order_by(ClaimEvidence.id)
    ).all():
        quote = (evidence.source_quote or "").strip()
        if quote:
            evidence_by_block.setdefault(evidence.idea_block_id, quote)

    claims: list[tuple[str, NarrativeReference]] = []
    for statement, idea_block_id in rows:
        text = (statement or "").strip()
        if not text:
            continue
        quote = evidence_by_block.get(idea_block_id, "")
        rendered = f"{text} _({quote})_" if quote else text
        claims.append((rendered, NarrativeReference(kind="idea_block", id=idea_block_id, label=text[:80])))
    return tuple(claims)


def _decisions(
    session: Session, domain_id: str, permitted_governance_record_types: frozenset[str]
) -> tuple[tuple[str, NarrativeReference], ...]:
    """Return the decisions recorded against a domain the reader may read.

    Governance records are category-scoped, so a reader who may read the
    domain is not automatically allowed to read every record type in it.
    """

    records = session.scalars(
        select(GovernanceRecord)
        .where(
            GovernanceRecord.domain_id == domain_id,
            GovernanceRecord.record_type.in_(permitted_governance_record_types),
            GovernanceRecord.lifecycle_status == "accepted",
        )
        .order_by(GovernanceRecord.id)
    ).all()
    return tuple(
        (record.title, NarrativeReference(kind="governance_record", id=record.id, label=record.title))
        for record in records
        if record.title
    )
