"""Semantic lint over the canonical knowledge layer.

The structural lint asks whether a record is sound. This lint asks whether the
corpus still agrees with itself: whether a claim that looks current is in fact
unverified, whether two verified claims contradict each other, and whether a
verified claim still has the evidence it was published on.

Every finding is a candidate. The lint never changes a knowledge state; it
raises review work, and a human decides.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRelation, IdeaBlockRevision
from margin_schema.findings import HygieneFinding

STALE_RULE_ID = "semantic_stale_claim"
CONTRADICTION_RULE_ID = "semantic_contradiction"
MISSING_SUPPORT_RULE_ID = "semantic_missing_support"

CONTRADICTION_RELATION = "contradicts"


def run_semantic_lint(session: Session) -> list[HygieneFinding]:
    """Report the semantic findings across the corpus."""

    findings: list[HygieneFinding] = []
    findings.extend(_stale_claims(session))
    findings.extend(_contradictions(session))
    findings.extend(_missing_support(session))
    return findings


def _stale_claims(session: Session) -> list[HygieneFinding]:
    """Report claims that carry entity links but are not verified.

    A claim is stale when it still looks current to a reader, because it is
    still attached to the entities it describes, but its current revision is no
    longer verified.
    """

    rows = session.execute(
        select(IdeaBlock.id, IdeaBlock.domain_id, IdeaBlockRevision.id, IdeaBlockRevision.statement)
        .join(IdeaBlockEntity, IdeaBlockEntity.idea_block_id == IdeaBlock.id)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(IdeaBlockRevision.publication_status != "verified")
        .distinct()
    ).all()
    return [
        HygieneFinding(
            rule_id=STALE_RULE_ID,
            severity="warning",
            target_type="idea_block",
            target_id=idea_block_id,
            message=(
                "A claim that is still attached to its records is no longer verified. "
                "Readers may be relying on a statement that is no longer current."
            ),
            evidence=(
                {
                    "idea_block_id": idea_block_id,
                    "revision_id": revision_id,
                    "domain_id": domain_id or "",
                    "statement": statement or "",
                },
            ),
            recommendation="Re-verify the claim against its evidence, or withdraw it.",
        )
        for idea_block_id, domain_id, revision_id, statement in rows
    ]


def _contradictions(session: Session) -> list[HygieneFinding]:
    """Report verified claims that contradict another verified claim."""

    rows = session.execute(
        select(
            IdeaBlockRelation.id,
            IdeaBlockRelation.source_idea_block_id,
            IdeaBlockRelation.target_idea_block_id,
            IdeaBlockRelation.relation_type,
        )
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(
            IdeaBlockRelation.relation_type == CONTRADICTION_RELATION,
            IdeaBlockRelation.status == "active",
            IdeaBlockRevision.publication_status == "verified",
        )
    ).all()
    return [
        HygieneFinding(
            rule_id=CONTRADICTION_RULE_ID,
            severity="warning",
            target_type="idea_block",
            target_id=source_idea_block_id,
            message=("Two verified claims contradict each other. " "A reader could follow either one and be misled."),
            evidence=(
                {
                    "relation_id": relation_id,
                    "source_idea_block_id": source_idea_block_id,
                    "target_idea_block_id": target_idea_block_id,
                    "relation_type": relation_type,
                },
            ),
            recommendation=("Decide which claim is current, supersede the other, and record the reason."),
        )
        for relation_id, source_idea_block_id, target_idea_block_id, relation_type in rows
    ]


def _missing_support(session: Session) -> list[HygieneFinding]:
    """Report verified claims that no longer carry active evidence."""

    rows = session.execute(
        select(IdeaBlock.id, IdeaBlock.domain_id, IdeaBlockRevision.id, IdeaBlockRevision.statement)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(IdeaBlockRevision.publication_status == "verified")
    ).all()

    findings: list[HygieneFinding] = []
    for idea_block_id, domain_id, revision_id, statement in rows:
        supported = session.scalar(
            select(ClaimEvidence.id)
            .where(ClaimEvidence.idea_block_id == idea_block_id, ClaimEvidence.status == "active")
            .limit(1)
        )
        if supported is not None:
            continue
        findings.append(
            HygieneFinding(
                rule_id=MISSING_SUPPORT_RULE_ID,
                severity="warning",
                target_type="idea_block",
                target_id=idea_block_id,
                message="A verified claim has no active evidence behind it.",
                evidence=(
                    {
                        "idea_block_id": idea_block_id,
                        "revision_id": revision_id,
                        "domain_id": domain_id or "",
                        "statement": statement or "",
                    },
                ),
                recommendation=("Re-attach the evidence the claim was published on, or withdraw the claim."),
            )
        )
    return findings
