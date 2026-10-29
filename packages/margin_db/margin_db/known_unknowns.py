"""Known unknowns: things the corpus refers to but does not yet record.

A source document may mention a system, a team or a concept that Margin has
never captured as a record of its own. That reference is not an error and not
noise: it is a precise statement about what is missing. Collecting those
references into tracked acquisition work turns an invisible hole into a task
somebody owns.

The placeholder is the review task rather than a draft entity. A draft entity
would occupy the unique label slot for that name, which would block the real
record from ever being created without first superseding the placeholder. The
task carries the same identity without that side effect.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.entity_models import EntityMention, EntityResolutionDecision
from margin_db.governance import create_review_tasks_for_findings
from margin_schema.findings import HygieneFinding
from margin_schema.ids import deterministic_id

KNOWN_UNKNOWN_RULE_ID = "known_unknown_reference"
KNOWN_UNKNOWN_PREFIX = "known_unknown_"
RESOLVED_OUTCOMES = ("linked", "resolved", "merged", "created")


@dataclass(frozen=True, slots=True)
class KnownUnknown:
    """A referenced name that has no record of its own."""

    domain_id: str
    text: str
    normalized_text: str
    proposed_type: str
    reference_count: int
    mention_ids: tuple[str, ...]

    @property
    def placeholder_id(self) -> str:
        """Return the stable identity of this known unknown."""

        return deterministic_id(
            KNOWN_UNKNOWN_PREFIX, {"domain_id": self.domain_id, "normalized_text": self.normalized_text}
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "placeholder_id": self.placeholder_id,
            "domain_id": self.domain_id,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "proposed_type": self.proposed_type,
            "reference_count": self.reference_count,
            "mention_ids": list(self.mention_ids),
        }


def detect_known_unknowns(
    session: Session, domain_id: str | None = None, *, readable_source_element_ids: frozenset[str] | None = None
) -> tuple[KnownUnknown, ...]:
    """Find the names the corpus refers to that no record resolves to.

    A mention quotes the source it was found in, so a reader may only be shown
    the mentions whose source they may read.
    """

    mention_query = select(EntityMention)
    if domain_id:
        mention_query = mention_query.where(EntityMention.domain_id == domain_id)
    if readable_source_element_ids is not None:
        mention_query = mention_query.where(EntityMention.source_element_id.in_(readable_source_element_ids))
    mentions = tuple(session.scalars(mention_query.order_by(EntityMention.id)).all())
    if not mentions:
        return ()

    resolved_mention_ids = {
        decision.mention_id
        for decision in session.scalars(
            select(EntityResolutionDecision).where(
                EntityResolutionDecision.is_current.is_(True), EntityResolutionDecision.entity_id.is_not(None)
            )
        ).all()
    }
    rejected_mention_ids = {
        decision.mention_id
        for decision in session.scalars(
            select(EntityResolutionDecision).where(
                EntityResolutionDecision.is_current.is_(True),
                EntityResolutionDecision.outcome.in_(("rejected", "dismissed")),
            )
        ).all()
    }

    grouped: dict[tuple[str, str, str], list[EntityMention]] = {}
    for mention in mentions:
        if mention.id in resolved_mention_ids or mention.id in rejected_mention_ids:
            continue
        key = (mention.domain_id, mention.normalized_text, mention.proposed_type)
        grouped.setdefault(key, []).append(mention)

    known_unknowns: list[KnownUnknown] = []
    for (mention_domain_id, normalized_text, proposed_type), members in sorted(grouped.items()):
        if not normalized_text:
            continue
        known_unknowns.append(
            KnownUnknown(
                domain_id=mention_domain_id,
                text=members[0].text,
                normalized_text=normalized_text,
                proposed_type=proposed_type,
                reference_count=len(members),
                mention_ids=tuple(member.id for member in members),
            )
        )
    return tuple(known_unknowns)


def known_unknown_findings(known_unknowns: tuple[KnownUnknown, ...]) -> list[HygieneFinding]:
    """Convert known unknowns into lint findings that become review work."""

    findings: list[HygieneFinding] = []
    for known_unknown in known_unknowns:
        findings.append(
            HygieneFinding(
                rule_id=KNOWN_UNKNOWN_RULE_ID,
                severity="warning",
                target_type="known_unknown",
                target_id=known_unknown.placeholder_id,
                message=(
                    f"'{known_unknown.text}' is referenced {known_unknown.reference_count} time(s) "
                    "but has no record of its own."
                ),
                evidence=(
                    {
                        "placeholder_id": known_unknown.placeholder_id,
                        "domain_id": known_unknown.domain_id,
                        "normalized_text": known_unknown.normalized_text,
                        "proposed_type": known_unknown.proposed_type,
                        "reference_count": str(known_unknown.reference_count),
                    },
                ),
                recommendation=("Capture this record, or record why it is referenced but not part of the domain."),
            )
        )
    return findings


def create_known_unknown_tasks(session: Session, known_unknowns: tuple[KnownUnknown, ...]) -> list:
    """Create or refresh the acquisition task for each known unknown."""

    return create_review_tasks_for_findings(session, known_unknown_findings(known_unknowns))
