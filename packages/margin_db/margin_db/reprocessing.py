from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.entity_models import EntityMention, EntityResolutionDecision
from margin_db.extraction_models import ExtractionRun, SpanExtractionAttempt
from margin_db.idea_block_resolution import create_idea_block_revision, current_idea_block_revision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision, ReprocessingDiff
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.review_models import ReviewTask
from margin_db.semantic_resolution import SEMANTIC_RESOLUTION_TASK_TYPE
from margin_db.source_models import SourceElement
from margin_schema.text_normalization import normalize_canonical_text


def stale_knowledge_for_versions(session: Session, version_ids: list[str]) -> list[str]:
    if not version_ids:
        return []
    element_ids = select(SourceElement.id).where(SourceElement.source_version_id.in_(version_ids))
    claims = list(
        session.scalars(
            select(IdeaBlock)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .where(ClaimEvidence.source_element_id.in_(element_ids))
            .distinct()
        ).all()
    )
    claim_ids = [claim.id for claim in claims]
    if not claim_ids:
        return []
    entity_ids = list(
        session.scalars(select(IdeaBlockEntity.entity_id).where(IdeaBlockEntity.idea_block_id.in_(claim_ids))).all()
    )
    claim_evidence = list(
        session.scalars(select(ClaimEvidence).where(ClaimEvidence.source_element_id.in_(element_ids))).all()
    )
    relationship_evidence = list(
        session.scalars(
            select(RelationshipProvenance).where(RelationshipProvenance.source_element_id.in_(element_ids))
        ).all()
    )
    closed_on = datetime.now(UTC).date()
    relationship_ids: set[str] = set()
    for item in claim_evidence:
        item.status = "stale"
        item.valid_to = item.valid_to or closed_on
    session.flush()
    active_claim_ids = set(
        session.scalars(
            select(ClaimEvidence.idea_block_id).where(
                ClaimEvidence.idea_block_id.in_(claim_ids), ClaimEvidence.status == "active"
            )
        ).all()
    )
    revisions = {
        block.id: revision
        for block, revision in session.execute(
            select(IdeaBlock, IdeaBlockRevision)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .where(IdeaBlock.id.in_(claim_ids))
        ).all()
    }
    for claim in claims:
        if claim.id not in active_claim_ids:
            revision = revisions.get(claim.id)
            if revision is not None and revision.publication_status in {"candidate", "verified"}:
                revision.publication_status = "stale"
    for item in relationship_evidence:
        relationship_ids.add(item.relationship_id)
        item.status = "stale"
        item.valid_to = item.valid_to or closed_on
    if relationship_ids:
        from margin_db.relationship_evidence import aggregate_relationship_evidence

        for relationship_id in relationship_ids:
            relationship = session.get(EntityRelationship, relationship_id)
            if relationship is not None:
                aggregate_relationship_evidence(session, relationship)
    session.flush()
    return sorted(set(entity_ids))


def finalize_reprocessing_diff(session: Session, extraction_run_id: str) -> ReprocessingDiff | None:
    run = session.get(ExtractionRun, extraction_run_id)
    if run is None:
        raise ValueError(f"extraction run not found: {extraction_run_id}")
    diff = session.scalar(select(ReprocessingDiff).where(ReprocessingDiff.current_version_id == run.source_version_id))
    if diff is None or diff.previous_version_id is None:
        return diff

    previous_claims = _claims(session, diff.previous_version_id)
    current_claims = _claims(session, diff.current_version_id)
    outcomes = _reconcile_claims(session, previous_claims, current_claims)
    added_claim_ids = sorted(item["current_claim_id"] for item in outcomes if item["outcome"] == "added")
    removed_claim_ids = sorted(
        item["previous_claim_id"] for item in outcomes if item["outcome"] in {"absent", "explicitly_superseded"}
    )

    diff.extraction_run_id = run.id
    diff.added_claim_ids = added_claim_ids
    diff.removed_claim_ids = removed_claim_ids
    diff.modified_claims = outcomes

    for outcome in outcomes:
        if outcome["outcome"] == "explicitly_superseded":
            _apply_explicit_supersession(session, outcome, actor="source_version_reconciliation")
        elif outcome["outcome"] == "changed" and outcome.get("current_claim_id"):
            _ensure_semantic_review_task(session, diff, outcome)

    previous_relationships = _relationships(session, {claim.id for claim in previous_claims})
    current_relationships = _relationships(session, {claim.id for claim in current_claims})
    diff.added_relationship_ids = sorted(current_relationships - previous_relationships)
    diff.removed_relationship_ids = sorted(previous_relationships - current_relationships)
    diff.changed_entity_resolutions = _changed_resolutions(
        session, {claim.id for claim in previous_claims}, {claim.id for claim in current_claims}
    )
    diff.newly_unresolved = sorted(
        set(
            session.scalars(
                select(SpanExtractionAttempt.source_element_id).where(
                    SpanExtractionAttempt.extraction_run_id == run.id,
                    SpanExtractionAttempt.outcome.in_(("failed", "deferred", "unsupported")),
                )
            ).all()
        )
        | _newly_unresolved_mentions(
            session,
            previous_claim_ids={claim.id for claim in previous_claims},
            current_claim_ids={claim.id for claim in current_claims},
        )
    )
    return diff


@dataclass(frozen=True)
class _ClaimView:
    block: IdeaBlock
    revision: IdeaBlockRevision
    source_element_id: str
    source_version_id: str
    element_checksum: str

    @property
    def id(self) -> str:
        return self.block.id

    @property
    def normalized_statement(self) -> str:
        return self.revision.normalized_statement

    @property
    def type(self) -> str:
        return self.revision.type

    @property
    def semantic_key(self) -> str:
        if self.revision.semantic_key:
            return self.revision.semantic_key
        return json.dumps(
            {
                "normalized_statement": self.normalized_statement,
                "type": self.revision.type,
                "polarity": self.revision.polarity,
                "qualifiers": self.revision.qualifiers or {},
                "valid_from": self.revision.valid_from.isoformat() if self.revision.valid_from else None,
                "valid_to": self.revision.valid_to.isoformat() if self.revision.valid_to else None,
            },
            sort_keys=True,
            default=str,
        )


def _claims(session: Session, version_id: str) -> list[_ClaimView]:
    rows = list(
        session.execute(
            select(IdeaBlock, IdeaBlockRevision, ClaimEvidence, SourceElement)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .where(SourceElement.source_version_id == version_id)
            .order_by(IdeaBlock.id, ClaimEvidence.created_at, ClaimEvidence.id)
        ).all()
    )
    views: dict[str, _ClaimView] = {}
    for block, revision, evidence, element in rows:
        views.setdefault(
            block.id,
            _ClaimView(
                block=block,
                revision=revision,
                source_element_id=evidence.source_element_id,
                source_version_id=element.source_version_id,
                element_checksum=element.checksum,
            ),
        )
    return list(views.values())


def _relationships(session: Session, claim_ids: set[str]) -> set[str]:
    if not claim_ids:
        return set()
    return set(
        session.scalars(
            select(RelationshipProvenance.relationship_id).where(
                RelationshipProvenance.idea_block_id.in_(claim_ids), RelationshipProvenance.polarity == "supports"
            )
        ).all()
    )


def _reconcile_claims(session: Session, previous: list[_ClaimView], current: list[_ClaimView]) -> list[dict[str, Any]]:
    previous_by_id = {claim.id: claim for claim in previous}
    unused_previous = set(previous_by_id)
    outcomes: list[dict[str, Any]] = []

    for claim in sorted(current, key=lambda item: item.id):
        exact_matches = [
            previous_by_id[previous_id]
            for previous_id in unused_previous
            if previous_by_id[previous_id].semantic_key == claim.semantic_key
        ]
        prior = _best_claim_match(session, claim, exact_matches)
        if prior is None:
            origin = _claim_origin(session, claim)
            origin_matches = [
                previous_by_id[previous_id]
                for previous_id in unused_previous
                if _claim_origin(session, previous_by_id[previous_id]) == origin and origin is not None
            ]
            prior = _best_claim_match(session, claim, origin_matches)
        if prior is None:
            outcomes.append(
                {
                    "outcome": "added",
                    "previous_claim_id": None,
                    "current_claim_id": claim.id,
                    "current_element_checksum": claim.element_checksum,
                }
            )
            continue

        unused_previous.remove(prior.id)
        outcome = "unchanged" if prior.element_checksum == claim.element_checksum else "changed"
        outcomes.append(
            {
                "outcome": outcome,
                "previous_claim_id": prior.id,
                "current_claim_id": claim.id,
                "previous_element_checksum": prior.element_checksum,
                "current_element_checksum": claim.element_checksum,
                "protected_field_differences": _protected_field_differences(prior, claim),
                "guarded_candidate": _guarded_semantic_candidate(prior, claim),
            }
        )

    explicit_targets = _explicit_supersession_targets(current)
    for previous_id in sorted(unused_previous):
        superseding_claim_id = next(
            (claim_id for claim_id, targets in explicit_targets.items() if previous_id in targets), None
        )
        outcomes.append(
            {
                "outcome": "explicitly_superseded" if superseding_claim_id else "absent",
                "previous_claim_id": previous_id,
                "current_claim_id": superseding_claim_id,
                "previous_element_checksum": previous_by_id[previous_id].element_checksum,
            }
        )
    return sorted(
        outcomes,
        key=lambda item: (
            str(item.get("outcome")),
            str(item.get("previous_claim_id") or ""),
            str(item.get("current_claim_id") or ""),
        ),
    )


def _best_claim_match(session: Session, current: _ClaimView, candidates: list[_ClaimView]) -> _ClaimView | None:
    if not candidates:
        return None
    current_origin = _claim_origin(session, current)
    return sorted(candidates, key=lambda item: (_claim_origin(session, item) != current_origin, item.id))[0]


def _protected_field_differences(previous: _ClaimView, current: _ClaimView) -> list[dict[str, Any]]:
    fields = {
        "type": (previous.revision.type, current.revision.type),
        "polarity": (previous.revision.polarity, current.revision.polarity),
        "qualifiers": (previous.revision.qualifiers or {}, current.revision.qualifiers or {}),
        "valid_from": (previous.revision.valid_from, current.revision.valid_from),
        "valid_to": (previous.revision.valid_to, current.revision.valid_to),
    }
    return [
        {"field": name, "previous": before, "current": after}
        for name, (before, after) in fields.items()
        if before != after
    ]


def _guarded_semantic_candidate(previous: _ClaimView, current: _ClaimView) -> bool:
    return (
        previous.revision.type == current.revision.type
        and previous.revision.polarity == current.revision.polarity
        and (previous.revision.qualifiers or {}) == (current.revision.qualifiers or {})
    )


def _explicit_supersession_targets(claims: list[_ClaimView]) -> dict[str, set[str]]:
    targets: dict[str, set[str]] = {}
    for claim in claims:
        metadata = claim.revision.metadata_ or {}
        values: list[Any] = []
        for key in ("supersedes_claim_id", "supersedes_idea_block_id", "supersedes"):
            value = metadata.get(key)
            if isinstance(value, list):
                values.extend(value)
            elif value is not None:
                values.append(value)
        exact_ids = {str(value) for value in values if isinstance(value, str) and value.strip()}
        if exact_ids:
            targets[claim.id] = exact_ids
    return targets


def _apply_explicit_supersession(session: Session, outcome: dict[str, Any], *, actor: str) -> None:
    previous_id = outcome.get("previous_claim_id")
    if not previous_id:
        return
    block = session.get(IdeaBlock, previous_id)
    if block is None:
        return
    current = current_idea_block_revision(session, block)
    if current.publication_status == "superseded":
        return
    create_idea_block_revision(
        session,
        block,
        statement=current.statement,
        normalized_statement=current.normalized_statement,
        type=current.type,
        confidence=current.confidence,
        polarity=current.polarity,
        qualifiers=current.qualifiers or {},
        valid_from=current.valid_from,
        valid_to=current.valid_to,
        publication_status="superseded",
        actor=actor,
        process="source_version_reconciliation",
        change_reason="validated source data explicitly superseded this knowledge",
        metadata=current.metadata_,
        ontology_version=current.ontology_version,
        semantic_key=current.semantic_key,
        semantic_key_version=current.semantic_key_version,
    )


def _ensure_semantic_review_task(session: Session, diff: ReprocessingDiff, outcome: dict[str, Any]) -> None:
    previous_id = outcome.get("previous_claim_id")
    current_id = outcome.get("current_claim_id")
    if not previous_id or not current_id or previous_id == current_id:
        return
    target_id = f"{previous_id}:{current_id}"
    existing = session.scalar(
        select(ReviewTask).where(
            ReviewTask.task_type == SEMANTIC_RESOLUTION_TASK_TYPE,
            ReviewTask.target_type == "idea_block_pair",
            ReviewTask.target_id == target_id,
        )
    )
    if existing is not None:
        return
    blocks = {block_id: session.get(IdeaBlock, block_id) for block_id in (previous_id, current_id)}
    revision_ids = {
        block_id: current_idea_block_revision(session, block).id
        for block_id, block in blocks.items()
        if block is not None and block.current_revision_id
    }
    session.add(
        ReviewTask(
            task_type=SEMANTIC_RESOLUTION_TASK_TYPE,
            target_type="idea_block_pair",
            target_id=target_id,
            reason=f"Source version reconciliation requires review for {previous_id} and {current_id}",
            recommendation=str(outcome["outcome"]),
            evidence=[
                {
                    "previous_claim_id": previous_id,
                    "current_claim_id": current_id,
                    "diff_id": diff.id,
                    "outcome": outcome["outcome"],
                }
            ],
            metadata_={
                "idea_block_ids": [previous_id, current_id],
                "idea_block_revision_ids": revision_ids,
                "suggested_outcome": outcome["outcome"],
                "protected_field_differences": outcome.get("protected_field_differences", []),
                "decision_provenance": {
                    "source": "source_version_reconciliation",
                    "diff_id": diff.id,
                    "current_version_id": diff.current_version_id,
                },
            },
        )
    )


def _claim_origin(session: Session, claim: _ClaimView) -> str | None:
    metadata = claim.revision.metadata_ or {}
    value = metadata.get("record_key") or metadata.get("governance_title")
    if value:
        return f"record:{normalize_canonical_text(str(value))}"
    element = session.get(SourceElement, claim.source_element_id)
    if element is None:
        return None
    return f"position:{element.native_element_id or element.ordinal}:{element.kind}:{element.char_start}:{claim.type}"


def _changed_resolutions(
    session: Session, previous_claim_ids: set[str], current_claim_ids: set[str]
) -> list[dict[str, object]]:
    previous = _resolution_map(session, previous_claim_ids)
    current = _resolution_map(session, current_claim_ids)
    return [
        {
            "mention": mention[0],
            "kind": mention[1],
            "previous_entity_id": previous[mention],
            "current_entity_id": current[mention],
        }
        for mention in sorted(previous.keys() & current.keys())
        if previous[mention] != current[mention]
    ]


def _resolution_map(session: Session, claim_ids: set[str]) -> dict[tuple[str, str], str | None]:
    if not claim_ids:
        return {}
    rows = session.execute(
        select(EntityMention.normalized_text, EntityMention.proposed_type, EntityResolutionDecision.entity_id)
        .join(EntityResolutionDecision, EntityResolutionDecision.mention_id == EntityMention.id)
        .join(IdeaBlockRevision, IdeaBlockRevision.id == EntityMention.idea_block_revision_id)
        .where(IdeaBlockRevision.idea_block_id.in_(claim_ids), EntityResolutionDecision.is_current.is_(True))
    ).all()
    return {(str(mention), kind): entity_id for mention, kind, entity_id in rows}


def _newly_unresolved_mentions(
    session: Session, *, previous_claim_ids: set[str], current_claim_ids: set[str]
) -> set[str]:
    previous_keys = _unresolved_mention_keys(session, previous_claim_ids)
    if not current_claim_ids:
        return set()
    rows = session.execute(
        select(EntityMention.id, EntityMention.normalized_text, EntityMention.proposed_type)
        .join(EntityResolutionDecision, EntityResolutionDecision.mention_id == EntityMention.id)
        .join(IdeaBlockRevision, IdeaBlockRevision.id == EntityMention.idea_block_revision_id)
        .where(
            IdeaBlockRevision.idea_block_id.in_(current_claim_ids),
            EntityResolutionDecision.is_current.is_(True),
            EntityResolutionDecision.status == "review_required",
        )
    ).all()
    return {
        mention_id
        for mention_id, normalized_text, proposed_kind in rows
        if (str(normalized_text), proposed_kind) not in previous_keys
    }


def _unresolved_mention_keys(session: Session, claim_ids: set[str]) -> set[tuple[str, str]]:
    if not claim_ids:
        return set()
    rows = session.execute(
        select(EntityMention.normalized_text, EntityMention.proposed_type)
        .join(EntityResolutionDecision, EntityResolutionDecision.mention_id == EntityMention.id)
        .join(IdeaBlockRevision, IdeaBlockRevision.id == EntityMention.idea_block_revision_id)
        .where(
            IdeaBlockRevision.idea_block_id.in_(claim_ids),
            EntityResolutionDecision.is_current.is_(True),
            EntityResolutionDecision.status == "review_required",
        )
    ).all()
    return {(str(normalized_text), str(proposed_kind)) for normalized_text, proposed_kind in rows}
