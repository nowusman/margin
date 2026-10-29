from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.audit import write_audit_event
from margin_db.entity_catalog import ENTITY_RESOLUTION_TASK_TYPE
from margin_db.entity_models import Entity, EntityMention, EntityResolutionDecision
from margin_db.governance_extraction_review import (
    GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE,
    GOVERNANCE_RECORD_REVIEW_TASK_TYPE,
)
from margin_db.governance_models import GovernanceRecord, GovernanceRecordIdeaBlock, GovernanceRecordRevision
from margin_db.idea_block_resolution import current_idea_block_revision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRelation, IdeaBlockRevision
from margin_db.relationship_evidence import aggregate_relationship_evidence
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.review_models import ReviewTask, ReviewTaskComment
from margin_db.semantic_resolution import SEMANTIC_RESOLUTION_TASK_TYPE, compare_protected_fields
from margin_db.source_models import SourceDocumentVersion, SourceElement, UnmappedExtraction


class ReviewBundleError(ValueError):
    pass


def build_review_bundle(
    session: Session,
    task_id: uuid.UUID,
    *,
    allowed_source_element_ids: frozenset[str] | None = None,
    allowed_governance_record_ids: frozenset[str] | None = None,
) -> dict[str, object]:
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise ReviewBundleError(f"review task not found: {task_id}")
    idea_block_relation = _load_idea_block_relation_review(session, task)
    claim_ids = _task_claim_ids(session, task)
    claims = [
        claim
        for claim in session.scalars(select(IdeaBlock).where(IdeaBlock.id.in_(claim_ids))).all()
        if claim.current_revision is not None
    ]
    revisions: dict[str, IdeaBlockRevision] = {
        claim.id: claim.current_revision for claim in claims if claim.current_revision is not None
    }
    claim_evidence = list(
        session.scalars(select(ClaimEvidence).where(ClaimEvidence.idea_block_id.in_(claim_ids))).all()
    )
    if allowed_source_element_ids is not None:
        allowed_governance_revision_ids = frozenset(
            session.scalars(
                select(GovernanceRecordRevision.id).where(
                    GovernanceRecordRevision.governance_record_id.in_(allowed_governance_record_ids or ())
                )
            ).all()
        )
        claim_evidence = [
            item
            for item in claim_evidence
            if (item.source_element_id is not None and item.source_element_id in allowed_source_element_ids)
            or (
                item.source_element_id is None
                and (
                    allowed_governance_record_ids is None
                    or item.governance_record_revision_id in allowed_governance_revision_ids
                )
            )
        ]
    element_ids = {item.source_element_id for item in claim_evidence if item.source_element_id}
    if task.target_type in {"source_element", "extraction_candidate"}:
        element_ids.add(task.target_id)
    if task.target_type == GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE:
        element_ids.update(
            str(item["source_element_id"])
            for item in task.evidence or []
            if isinstance(item, dict) and item.get("source_element_id")
        )
    elements = {
        elem.id: elem for elem in session.scalars(select(SourceElement).where(SourceElement.id.in_(element_ids))).all()
    }
    versions = {
        version.id: version
        for version in session.scalars(
            select(SourceDocumentVersion).where(
                SourceDocumentVersion.id.in_({element.source_version_id for element in elements.values()})
            )
        ).all()
    }
    entities = _entities(session, claim_ids)
    evidence = [] if idea_block_relation is not None else _relationship_evidence(session, claim_ids)
    relationship_ids = {item.relationship_id for item in evidence}
    refuted_relationship_ids = {item.relationship_id for item in evidence if item.polarity == "refutes"}
    relationships = list(
        session.scalars(select(EntityRelationship).where(EntityRelationship.id.in_(relationship_ids))).all()
    )
    decisions = _resolution_decisions(session, claim_ids)
    evidence_by_claim: dict[str, list[ClaimEvidence]] = {}
    for item in claim_evidence:
        evidence_by_claim.setdefault(item.idea_block_id, []).append(item)
    unmapped = session.get(UnmappedExtraction, task.target_id) if task.target_type == "unmapped_extraction" else None
    claim_payloads = [
        {
            "id": claim.id,
            "statement": revisions[claim.id].statement,
            "type": revisions[claim.id].type,
            "confidence": revisions[claim.id].confidence,
            "status": revisions[claim.id].publication_status,
            "revision_id": revisions[claim.id].id,
            "evidence": [
                _claim_evidence_payload(session, item, elements.get(item.source_element_id), versions)
                for item in evidence_by_claim.get(claim.id, [])
            ],
        }
        for claim in claims
    ]
    all_revisions = [
        _revision_payload(revision)
        for revision in session.scalars(
            select(IdeaBlockRevision)
            .where(IdeaBlockRevision.idea_block_id.in_(claim_ids))
            .order_by(IdeaBlockRevision.idea_block_id, IdeaBlockRevision.revision_number)
        ).all()
    ]
    current_statements = [
        {"idea_block_id": claim.id, "revision": _revision_payload(revisions[claim.id])}
        for claim in sorted(claims, key=lambda item: item.id)
    ]
    semantic_resolution = _semantic_resolution_payload(session, task, claims, revisions)
    governance_review = _governance_review_payload(session, task, elements, versions)
    relation_payload = _idea_block_relation_payload(idea_block_relation) if idea_block_relation else None
    related_governance_records = (
        _related_governance_records(session, claim_ids, allowed_record_ids=allowed_governance_record_ids)
        if idea_block_relation
        else []
    )
    protected_fields = (semantic_resolution or {}).get("protected_fields", {})
    contradictions = [
        relationship.id
        for relationship in relationships
        if relationship.id in refuted_relationship_ids
        or relationship.relation_type == "contradicts"
        or (relationship.metadata_ or {}).get("has_contradiction")
    ]
    if relation_payload is not None:
        contradictions.append(str(relation_payload["id"]))
    decision_provenance = dict(task.metadata_ or {}).get("decision_provenance", {})
    if relation_payload is not None:
        decision_provenance = {
            **(decision_provenance if isinstance(decision_provenance, dict) else {}),
            "idea_block_relation": relation_payload["decision_provenance"],
        }
    return {
        "task": {
            "id": str(task.id),
            "status": task.status,
            "type": task.task_type,
            "reason": task.reason,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "target_type": task.target_type,
            "target_id": task.target_id,
        },
        "claims": claim_payloads,
        "current_statements": current_statements,
        "revisions": all_revisions,
        "evidence": [item for claim in claim_payloads for item in claim["evidence"]],
        "semantic_resolution": semantic_resolution,
        "governance_review": governance_review,
        "entities": entities,
        "entity_resolutions": decisions,
        "relationships": [
            {
                "id": relationship.id,
                "source_entity_id": relationship.source_entity_id,
                "target_entity_id": relationship.target_entity_id,
                "relation_type": relationship.relation_type,
                "status": relationship.status,
                "evidence": [_evidence_payload(item) for item in evidence if item.relationship_id == relationship.id],
            }
            for relationship in relationships
        ],
        "idea_block_relations": [relation_payload] if relation_payload is not None else [],
        "governance_records": related_governance_records,
        "contradictions": contradictions,
        "protected_field_differences": (
            protected_fields.get("differences", {}) if isinstance(protected_fields, dict) else {}
        ),
        "suggested_outcome": (task.metadata_ or {}).get("suggested_outcome") or task.recommendation,
        "decision_provenance": decision_provenance,
        "unmapped_extractions": (
            [
                {
                    "id": unmapped.id,
                    "kind": unmapped.kind,
                    "reason": unmapped.reason,
                    "raw_payload": unmapped.raw_payload,
                    "status": unmapped.status,
                }
            ]
            if unmapped is not None
            else []
        ),
    }


def decide_review_bundle(
    session: Session,
    *,
    task_id: uuid.UUID,
    decision: str,
    actor_id: str | None,
    resolution: str,
    entity_resolution_targets: dict[str, str] | None = None,
) -> ReviewTask:
    if decision not in {"approved", "rejected"}:
        raise ReviewBundleError("decision must be approved or rejected")
    task = session.scalar(select(ReviewTask).where(ReviewTask.id == task_id).with_for_update())
    if task is None:
        raise ReviewBundleError(f"review task not found: {task_id}")
    if task.task_type == SEMANTIC_RESOLUTION_TASK_TYPE and task.target_type == "idea_block_pair":
        raise ReviewBundleError("semantic review tasks require one of the semantic review actions")
    if task.task_type == GOVERNANCE_RECORD_REVIEW_TASK_TYPE:
        raise ReviewBundleError("governance record review tasks require an authorized governance record operation")
    if task.task_type == ENTITY_RESOLUTION_TASK_TYPE and decision == "approved":
        raise ReviewBundleError(
            "entity_resolution_review cannot be approved with a generic bundle decision; "
            "resolve entity mentions first before exact attachment or Idea Block verification"
        )
    if task.status not in {"open", "assigned"}:
        raise ReviewBundleError(f"review task is already terminal: {task.status}")
    idea_block_relation = _load_idea_block_relation_review(session, task, require_verified=True)
    claim_ids = _task_claim_ids(session, task)
    if not claim_ids and task.target_type not in {"extraction_candidate", "unmapped_extraction"}:
        raise ReviewBundleError("review bundle contains no claims")

    if task.target_type == "unmapped_extraction":
        unmapped = session.get(UnmappedExtraction, task.target_id)
        if unmapped is None:
            raise ReviewBundleError("unmapped extraction not found")
        unmapped.status = "resolved" if decision == "approved" else "rejected"
        unmapped.resolution = {"decision": decision, "note": resolution, "actor_id": actor_id}
        unmapped.resolved_at = datetime.now(UTC)

    resolution_decisions = [] if idea_block_relation is not None else _resolution_decision_rows(session, claim_ids)
    if decision == "approved":
        for resolution_decision in resolution_decisions:
            if resolution_decision.status != "review_required":
                continue
            raise ReviewBundleError(
                "entity_resolution_review cannot be approved with a generic bundle decision; "
                "resolve entity mentions from the Entity Catalog first"
            )

    claim_status = "verified" if decision == "approved" else "rejected"
    claims = list(session.scalars(select(IdeaBlock).where(IdeaBlock.id.in_(claim_ids))).all())
    if task.target_type == "idea_block":
        from margin_db.review_transitions import ReviewTransitionError, transition_idea_block_status

        for claim in claims:
            try:
                current = current_idea_block_revision(session, claim)
                if current.publication_status != claim_status:
                    transition_idea_block_status(
                        session,
                        idea_block_id=claim.id,
                        target_status=claim_status,
                        actor=actor_id or "system",
                        note=resolution,
                    )
            except ReviewTransitionError as exc:
                raise ReviewBundleError(str(exc)) from exc

    for resolution_decision in resolution_decisions:
        resolution_decision.status = "resolved" if decision == "approved" else "rejected"
        resolution_decision.decided_by = actor_id
        resolution_decision.reviewed_at = datetime.now(UTC)

    if idea_block_relation is not None:
        if not resolution.strip():
            raise ReviewBundleError("contradiction review requires a resolution note")
        relation_before = {
            "status": idea_block_relation.status,
            "decision_provenance": dict(idea_block_relation.decision_provenance or {}),
        }
        if decision == "rejected":
            idea_block_relation.status = "rejected"
        idea_block_relation.decision_provenance = {
            **dict(idea_block_relation.decision_provenance or {}),
            "review_task_id": str(task.id),
            "decision": decision,
            "actor": actor_id or "system",
            "resolution": resolution.strip(),
        }
        write_audit_event(
            session,
            actor=actor_id or "system",
            action="idea_block_relation.contradiction_reviewed",
            target_type="idea_block_relation",
            target_id=idea_block_relation.id,
            before=relation_before,
            after={"status": idea_block_relation.status, "decision": decision, "review_task_id": str(task.id)},
        )
    else:
        evidence = _relationship_evidence(session, claim_ids)
        relationship_ids = {item.relationship_id for item in evidence}
        for item in evidence:
            if item.valid_to is not None or item.status == "stale":
                continue
            item.status = "verified" if decision == "approved" else "rejected"
            item.reviewed_by = actor_id
            item.reviewed_at = datetime.now(UTC)
        session.flush()
        for relationship in session.scalars(
            select(EntityRelationship).where(EntityRelationship.id.in_(relationship_ids))
        ).all():
            aggregate_relationship_evidence(session, relationship)

    task.status = decision
    task.resolution = resolution
    task.closed_at = datetime.now(UTC)
    task.updated_at = task.closed_at
    session.add(ReviewTaskComment(review_task_id=task.id, author_id=actor_id, comment=resolution))
    return task


def _task_claim_ids(session: Session, task: ReviewTask) -> set[str]:
    if task.target_type == "idea_block":
        return {task.target_id}
    if task.target_type == "entity_relationship":
        return set(
            session.scalars(
                select(RelationshipProvenance.idea_block_id).where(
                    RelationshipProvenance.relationship_id == task.target_id
                )
            ).all()
        )
    if task.target_type == "entity":
        return set(
            session.scalars(
                select(IdeaBlockRevision.idea_block_id)
                .join(EntityResolutionDecision, EntityResolutionDecision.mention_id == EntityMention.id)
                .join(IdeaBlockRevision, IdeaBlockRevision.id == EntityMention.idea_block_revision_id)
                .where(
                    EntityResolutionDecision.entity_id == task.target_id, EntityResolutionDecision.is_current.is_(True)
                )
            ).all()
        )
    if task.target_type == "idea_block_pair":
        return set(_pair_ids(session, task))
    if task.target_type == "idea_block_relation":
        relation = session.get(IdeaBlockRelation, task.target_id)
        if relation is None:
            return set()
        return {relation.source_idea_block_id, relation.target_idea_block_id}
    if task.target_type == GOVERNANCE_RECORD_CANDIDATE_TARGET_TYPE:
        metadata_ids = (task.metadata_ or {}).get("idea_block_ids")
        if isinstance(metadata_ids, list):
            return {str(item) for item in metadata_ids if isinstance(item, str) and item}
    return set()


def _load_idea_block_relation_review(
    session: Session, task: ReviewTask, *, require_verified: bool = False
) -> IdeaBlockRelation | None:
    if task.target_type != "idea_block_relation":
        return None
    if task.task_type != "governance_contradiction_review":
        raise ReviewBundleError("only contradiction review tasks can target an IdeaBlock relation")
    relation = session.get(IdeaBlockRelation, task.target_id)
    if relation is None:
        raise ReviewBundleError("contradiction review references a missing IdeaBlock relation")
    if relation.relation_type != "contradicts":
        raise ReviewBundleError("contradiction review references a non-contradiction relation")
    if require_verified and relation.status != "verified":
        raise ReviewBundleError("contradiction review is stale because the relation is no longer verified")
    return relation


def _idea_block_relation_payload(relation: IdeaBlockRelation) -> dict[str, object]:
    return {
        "id": relation.id,
        "source_idea_block_id": relation.source_idea_block_id,
        "target_idea_block_id": relation.target_idea_block_id,
        "relation_type": relation.relation_type,
        "direction": relation.direction,
        "status": relation.status,
        "confidence": float(relation.confidence),
        "valid_from": relation.valid_from.isoformat() if relation.valid_from else None,
        "valid_to": relation.valid_to.isoformat() if relation.valid_to else None,
        "decision_provenance": dict(relation.decision_provenance or {}),
    }


def _related_governance_records(
    session: Session, claim_ids: set[str], *, allowed_record_ids: frozenset[str] | None = None
) -> list[dict[str, object]]:
    if not claim_ids:
        return []
    record_ids = session.scalars(
        select(GovernanceRecordIdeaBlock.governance_record_id)
        .where(GovernanceRecordIdeaBlock.idea_block_id.in_(claim_ids))
        .distinct()
    ).all()
    record_query = select(GovernanceRecord).where(GovernanceRecord.id.in_(record_ids))
    if allowed_record_ids is not None:
        record_query = record_query.where(GovernanceRecord.id.in_(allowed_record_ids))
    records = session.scalars(record_query).all()
    payloads = [_governance_record_payload(record) for record in sorted(records, key=lambda item: item.id)]
    return [payload for payload in payloads if payload is not None]


def _pair_ids(session: Session, task: ReviewTask) -> tuple[str, str] | tuple[()]:
    metadata_ids = (task.metadata_ or {}).get("idea_block_ids")
    if (
        isinstance(metadata_ids, list)
        and len(metadata_ids) == 2
        and all(isinstance(item, str) for item in metadata_ids)
    ):
        return str(metadata_ids[0]), str(metadata_ids[1])
    blocks = list(session.scalars(select(IdeaBlock.id)).all())
    for left_id in blocks:
        for right_id in blocks:
            if left_id != right_id and f"{left_id}:{right_id}" == task.target_id:
                return left_id, right_id
    return ()


def _semantic_resolution_payload(
    session: Session, task: ReviewTask, claims: list[IdeaBlock], revisions: dict[str, IdeaBlockRevision]
) -> dict[str, object] | None:
    if task.target_type != "idea_block_pair":
        return None
    by_id = {claim.id: claim for claim in claims}
    parts = _pair_ids(session, task)
    if len(parts) != 2 or not all(part in by_id for part in parts):
        return {"suggested_outcome": task.recommendation, "decision_provenance": dict(task.metadata_ or {})}
    left_id, right_id = parts
    left_revision = revisions.get(left_id)
    right_revision = revisions.get(right_id)
    if left_revision is None or right_revision is None:
        return {"suggested_outcome": task.recommendation, "decision_provenance": dict(task.metadata_ or {})}
    comparison = compare_protected_fields(
        left_revision,
        right_revision,
        left_domain_id=by_id[left_id].domain_id,
        right_domain_id=by_id[right_id].domain_id,
        left_entity_ids=_idea_block_entity_ids(session, left_id),
        right_entity_ids=_idea_block_entity_ids(session, right_id),
    )
    return {
        "left_idea_block_id": left_id,
        "right_idea_block_id": right_id,
        "protected_fields": comparison.to_dict(),
        "suggested_outcome": task.recommendation,
        "decision_provenance": {
            **dict(task.metadata_ or {}),
            "review_task_id": str(task.id),
            "stored_suggestion": list(task.evidence or []),
        },
    }


def _governance_review_payload(
    session: Session, task: ReviewTask, elements: dict[str, SourceElement], versions: dict[str, SourceDocumentVersion]
) -> dict[str, object] | None:
    if task.task_type != GOVERNANCE_RECORD_REVIEW_TASK_TYPE:
        return None
    metadata = dict(task.metadata_ or {})
    candidate = metadata.get("governance_candidate")
    if not isinstance(candidate, dict):
        return {
            "candidate": None,
            "matched_record": None,
            "changed_fields": [],
            "source_elements": [],
            "related_idea_block_ids": [],
            "proposed_lifecycle_action": None,
            "identity_status": "invalid_candidate_payload",
        }
    matched_record_id = metadata.get("matched_record_id")
    matched_record = session.get(GovernanceRecord, matched_record_id) if isinstance(matched_record_id, str) else None
    source_elements = [
        _governance_candidate_evidence_payload(session, evidence, elements, versions)
        for evidence in task.evidence or []
        if isinstance(evidence, dict)
    ]
    return {
        "candidate": candidate,
        "matched_record": _governance_record_payload(matched_record),
        "changed_fields": _governance_changed_fields(candidate, matched_record),
        "source_elements": source_elements,
        "related_idea_block_ids": list(_task_claim_ids(session, task)),
        "proposed_lifecycle_action": candidate.get("proposed_lifecycle_action"),
        "identity_status": metadata.get("identity_status"),
    }


def _governance_candidate_evidence_payload(
    session: Session,
    evidence: dict[str, object],
    elements: dict[str, SourceElement],
    versions: dict[str, SourceDocumentVersion],
) -> dict[str, object]:
    source_element_id = evidence.get("source_element_id")
    element = elements.get(str(source_element_id)) if source_element_id else None
    return {
        "source_element_id": source_element_id,
        "source_quote": evidence.get("source_quote"),
        "quote_char_start": evidence.get("quote_char_start"),
        "quote_char_end": evidence.get("quote_char_end"),
        "evidence_kind": evidence.get("evidence_kind"),
        "polarity": evidence.get("polarity"),
        "source_version": _source_version_payload(versions.get(element.source_version_id) if element else None),
        "context": _element_context(session, element),
    }


def _governance_record_payload(record: GovernanceRecord | None) -> dict[str, object] | None:
    if record is None:
        return None
    revision = record.current_revision
    return {
        "id": record.id,
        "business_key": record.business_key,
        "record_type": record.record_type,
        "title": record.title,
        "domain_id": record.domain_id,
        "lifecycle_status": record.lifecycle_status,
        "attributes": record.attributes,
        "current_revision": _governance_revision_payload(revision),
    }


def _governance_revision_payload(revision: GovernanceRecordRevision | None) -> dict[str, object] | None:
    if revision is None:
        return None
    return {
        "id": revision.id,
        "revision_number": revision.revision_number,
        "snapshot": revision.snapshot,
        "transition_reason": revision.transition_reason,
        "actor": revision.actor,
        "process": revision.process,
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
    }


def _governance_changed_fields(
    candidate: dict[str, object], record: GovernanceRecord | None
) -> list[dict[str, object]]:
    candidate_fields = {
        "record_type": candidate.get("record_type"),
        "title": candidate.get("title"),
        "attributes": candidate.get("attributes", {}),
    }
    if candidate.get("business_key") is not None:
        candidate_fields["business_key"] = candidate.get("business_key")
    current = _governance_record_payload(record) or {}
    differences: list[dict[str, object]] = []
    for field_name in sorted(candidate_fields):
        _append_governance_field_differences(
            differences, field_name=field_name, current=current.get(field_name), candidate=candidate_fields[field_name]
        )
    return differences


def _append_governance_field_differences(
    differences: list[dict[str, object]], *, field_name: str, current: object, candidate: object
) -> None:
    if isinstance(current, dict) and isinstance(candidate, dict):
        for key in sorted(set(current) | set(candidate)):
            _append_governance_field_differences(
                differences, field_name=f"{field_name}.{key}", current=current.get(key), candidate=candidate.get(key)
            )
        return
    if current != candidate:
        differences.append({"field": field_name, "current": current, "candidate": candidate})


def _idea_block_entity_ids(session: Session, idea_block_id: str) -> tuple[str, ...]:
    return tuple(
        session.scalars(
            select(IdeaBlockEntity.entity_id)
            .where(IdeaBlockEntity.idea_block_id == idea_block_id)
            .order_by(IdeaBlockEntity.entity_id)
        ).all()
    )


def _entities(session: Session, claim_ids: set[str]) -> list[dict[str, object]]:
    rows = session.execute(
        select(IdeaBlockEntity, Entity)
        .join(Entity, Entity.id == IdeaBlockEntity.entity_id)
        .where(IdeaBlockEntity.idea_block_id.in_(claim_ids))
    ).all()
    return [
        {
            "idea_block_id": link.idea_block_id,
            "id": entity.id,
            "name": entity.preferred_label,
            "kind": entity.ontology_type,
            "role": link.role,
            "status": entity.lifecycle_status,
        }
        for link, entity in rows
    ]


def _resolution_decisions(session: Session, claim_ids: set[str]) -> list[dict[str, object]]:
    rows = session.execute(
        select(EntityMention, EntityResolutionDecision)
        .join(EntityResolutionDecision, EntityResolutionDecision.mention_id == EntityMention.id)
        .join(IdeaBlockRevision, IdeaBlockRevision.id == EntityMention.idea_block_revision_id)
        .where(IdeaBlockRevision.idea_block_id.in_(claim_ids), EntityResolutionDecision.is_current.is_(True))
    ).all()
    return [
        {
            "id": decision.id,
            "mention_id": mention.id,
            "mention": mention.text,
            "entity_id": decision.entity_id,
            "outcome": decision.outcome,
            "confidence": decision.confidence,
            "candidate_entity_ids": decision.candidate_entity_ids,
            "candidates": _candidate_entities(session, decision.candidate_entity_ids),
            "status": decision.status,
        }
        for mention, decision in rows
    ]


def _revision_payload(revision: IdeaBlockRevision) -> dict[str, object]:
    return {
        "id": revision.id,
        "idea_block_id": revision.idea_block_id,
        "revision_number": revision.revision_number,
        "statement": revision.statement,
        "normalized_statement": revision.normalized_statement,
        "type": revision.type,
        "confidence": revision.confidence,
        "polarity": revision.polarity,
        "qualifiers": revision.qualifiers,
        "valid_from": revision.valid_from.isoformat() if revision.valid_from else None,
        "valid_to": revision.valid_to.isoformat() if revision.valid_to else None,
        "publication_status": revision.publication_status,
        "semantic_key": revision.semantic_key,
        "semantic_key_version": revision.semantic_key_version,
        "ontology_version": revision.ontology_version,
        "actor": revision.actor,
        "process": revision.process,
        "change_reason": revision.change_reason,
        "metadata": revision.metadata_,
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
    }


def _source_version_payload(version: SourceDocumentVersion | None) -> dict[str, object] | None:
    if version is None:
        return None
    return {
        "id": version.id,
        "status": version.status,
        "source_document_id": version.source_document_id,
        "version_sha256": version.version_sha256,
        "superseded_by_version_id": version.superseded_by_version_id,
    }


def _claim_evidence_payload(
    session: Session, evidence: ClaimEvidence, element: SourceElement | None, versions: dict[str, SourceDocumentVersion]
) -> dict[str, object]:
    return {
        "id": evidence.id,
        "source_quote": evidence.source_quote,
        "source_element_id": evidence.source_element_id,
        "evidence_kind": evidence.evidence_kind,
        "polarity": evidence.polarity,
        "status": evidence.status,
        "valid_from": evidence.valid_from.isoformat() if evidence.valid_from else None,
        "valid_to": evidence.valid_to.isoformat() if evidence.valid_to else None,
        "source_version": _source_version_payload(versions.get(element.source_version_id) if element else None),
        "context": _element_context(session, element),
    }


def _candidate_entities(session: Session, entity_ids: list[str]) -> list[dict[str, str]]:
    if not entity_ids:
        return []
    entities = session.scalars(select(Entity).where(Entity.id.in_(entity_ids))).all()
    return [
        {"id": entity.id, "name": entity.preferred_label, "kind": entity.ontology_type}
        for entity in sorted(entities, key=lambda item: (item.preferred_label.casefold(), item.id))
    ]


def _resolution_decision_rows(session: Session, claim_ids: set[str]) -> list[EntityResolutionDecision]:
    if not claim_ids:
        return []
    return list(
        session.scalars(
            select(EntityResolutionDecision)
            .join(EntityMention, EntityMention.id == EntityResolutionDecision.mention_id)
            .join(IdeaBlockRevision, IdeaBlockRevision.id == EntityMention.idea_block_revision_id)
            .where(IdeaBlockRevision.idea_block_id.in_(claim_ids), EntityResolutionDecision.is_current.is_(True))
        ).all()
    )


def _relationship_evidence(session: Session, claim_ids: set[str]) -> list[RelationshipProvenance]:
    if not claim_ids:
        return []
    return list(
        session.scalars(select(RelationshipProvenance).where(RelationshipProvenance.idea_block_id.in_(claim_ids))).all()
    )


def _element_context(session: Session, element: SourceElement | None) -> dict[str, object]:
    if element is None:
        return {
            "section_path": None,
            "section_heading": None,
            "page_number": None,
            "kind": None,
            "char_start": None,
            "char_end": None,
            "bounding_box": None,
            "before": None,
            "current": None,
            "after": None,
        }
    siblings = list(
        session.scalars(
            select(SourceElement)
            .where(SourceElement.source_version_id == element.source_version_id)
            .order_by(SourceElement.ordinal, SourceElement.id)
        ).all()
    )
    index = next((idx for idx, current in enumerate(siblings) if current.id == element.id), 0)
    metadata = element.metadata_ or {}
    return {
        "section_path": metadata.get("structural_path") or metadata.get("section_path"),
        "section_heading": metadata.get("heading") or metadata.get("section_heading"),
        "page_number": element.page_number,
        "kind": element.kind,
        "char_start": element.char_start,
        "char_end": element.char_end,
        "bounding_box": element.bounding_box,
        "before": siblings[index - 1].text if index else None,
        "current": element.text,
        "after": siblings[index + 1].text if index + 1 < len(siblings) else None,
    }


def _evidence_payload(evidence: RelationshipProvenance) -> dict[str, object]:
    return {
        "id": evidence.id,
        "idea_block_id": evidence.idea_block_id,
        "source_element_id": evidence.source_element_id,
        "confidence": evidence.confidence,
        "polarity": evidence.polarity,
        "status": evidence.status,
        "valid_from": evidence.valid_from.isoformat() if evidence.valid_from else None,
        "valid_to": evidence.valid_to.isoformat() if evidence.valid_to else None,
    }
