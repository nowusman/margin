"""Read models for the canonical governance aggregate."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from margin_db.audit_models import AuditEvent
from margin_db.entity_models import Entity
from margin_db.governance_models import (
    GovernanceRecord,
    GovernanceRecordEntity,
    GovernanceRecordIdeaBlock,
    GovernanceRecordRelation,
    GovernanceRecordRevision,
)
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock


def governance_record_query(
    *,
    record_type: str | None = None,
    lifecycle_status: str | None = None,
    owner_id: str | None = None,
    domain_id: str | None = None,
    external_issue_id: str | None = None,
    allowed_record_types: frozenset[str] | None = None,
    allowed_domain_ids: frozenset[str] | None = None,
) -> Select[tuple[GovernanceRecord]]:
    statement = select(GovernanceRecord).order_by(GovernanceRecord.updated_at.desc(), GovernanceRecord.id)
    if record_type:
        statement = statement.where(GovernanceRecord.record_type == record_type)
    if lifecycle_status:
        statement = statement.where(GovernanceRecord.lifecycle_status == lifecycle_status)
    if owner_id:
        statement = statement.where(GovernanceRecord.owner_id == owner_id)
    if domain_id:
        statement = statement.where(GovernanceRecord.domain_id == domain_id)
    if external_issue_id:
        statement = statement.where(GovernanceRecord.external_issue_id == external_issue_id)
    if allowed_record_types is not None:
        statement = statement.where(GovernanceRecord.record_type.in_(allowed_record_types))
    if allowed_domain_ids is not None:
        statement = statement.where(GovernanceRecord.domain_id.in_(allowed_domain_ids))
    return statement


def list_governance_records(
    session: Session,
    *,
    record_type: str | None = None,
    lifecycle_status: str | None = None,
    owner_id: str | None = None,
    domain_id: str | None = None,
    external_issue_id: str | None = None,
    allowed_record_types: frozenset[str] | None = None,
    allowed_domain_ids: frozenset[str] | None = None,
) -> list[GovernanceRecord]:
    return list(
        session.scalars(
            governance_record_query(
                record_type=record_type,
                lifecycle_status=lifecycle_status,
                owner_id=owner_id,
                domain_id=domain_id,
                external_issue_id=external_issue_id,
                allowed_record_types=allowed_record_types,
                allowed_domain_ids=allowed_domain_ids,
            )
        ).all()
    )


def governance_dashboard_metrics(session: Session, domain_id: str) -> dict[str, object]:
    """Return one domain's governance-derived home-page metrics."""

    records_query = select(GovernanceRecord).where(GovernanceRecord.domain_id == domain_id)
    risks = records_query.where(GovernanceRecord.record_type == "risk")
    waivers = records_query.where(GovernanceRecord.record_type == "waiver")
    debt = records_query.where(GovernanceRecord.record_type == "technical_debt")
    risk_volume = int(session.scalar(select(func.count()).select_from(risks.subquery())) or 0)
    waiver_volume = int(session.scalar(select(func.count()).select_from(waivers.subquery())) or 0)
    debt_volume = int(
        session.scalar(
            select(func.count()).select_from(debt.where(GovernanceRecord.lifecycle_status != "resolved").subquery())
        )
        or 0
    )
    high_severity_risks = int(
        session.scalar(
            select(func.count()).select_from(
                risks.where(GovernanceRecord.lifecycle_status != "closed", GovernanceRecord.severity >= 3).subquery()
            )
        )
        or 0
    )
    expired_waivers = int(
        session.scalar(
            select(func.count()).select_from(
                waivers.where(
                    GovernanceRecord.lifecycle_status == "approved",
                    GovernanceRecord.expiry_date.is_not(None),
                    GovernanceRecord.expiry_date < date.today(),
                ).subquery()
            )
        )
        or 0
    )
    accepted_adr_exists = (
        select(GovernanceRecordEntity.governance_record_id)
        .join(GovernanceRecord, GovernanceRecord.id == GovernanceRecordEntity.governance_record_id)
        .where(
            GovernanceRecordEntity.entity_id == Entity.id,
            GovernanceRecord.record_type == "adr",
            GovernanceRecord.lifecycle_status == "accepted",
        )
        .exists()
    )
    missing_adr_coverage = int(
        session.scalar(
            select(func.count())
            .select_from(Entity)
            .where(
                Entity.domain_id == domain_id,
                Entity.lifecycle_status == "active",
                Entity.ontology_type.in_(("system", "container", "component", "api", "event")),
                ~accepted_adr_exists,
            )
        )
        or 0
    )
    top_risks = list(
        session.scalars(
            risks.where(GovernanceRecord.lifecycle_status != "closed")
            .order_by(GovernanceRecord.severity.desc(), GovernanceRecord.updated_at.desc(), GovernanceRecord.id)
            .limit(5)
        ).all()
    )
    recent_adrs = list(
        session.scalars(
            records_query.where(GovernanceRecord.record_type == "adr")
            .order_by(GovernanceRecord.updated_at.desc(), GovernanceRecord.id)
            .limit(5)
        ).all()
    )
    top_debt = list(
        session.scalars(
            debt.where(GovernanceRecord.lifecycle_status != "resolved")
            .order_by(GovernanceRecord.updated_at.desc(), GovernanceRecord.id)
            .limit(5)
        ).all()
    )
    return {
        "risk_volume": risk_volume,
        "waiver_volume": waiver_volume,
        "debt_volume": debt_volume,
        "high_severity_risks": high_severity_risks,
        "expired_waivers": expired_waivers,
        "missing_adr_coverage": missing_adr_coverage,
        "recent_adrs": [_dashboard_record(record) for record in recent_adrs],
        "top_risks": [_dashboard_record(record) for record in top_risks],
        "top_debt": [_dashboard_record(record) for record in top_debt],
    }


def _dashboard_record(record: GovernanceRecord) -> dict[str, object]:
    attributes = record.attributes or {}
    severity = attributes.get("severity", record.severity)
    return {
        "id": record.id,
        "title": record.title,
        "record_type": record.record_type,
        "lifecycle_status": record.lifecycle_status,
        "severity": str(severity) if severity is not None else "medium",
        "priority": record.priority,
        "impact": str(attributes.get("interest_or_impact", "")),
        "date": record.updated_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def governance_record_details(
    session: Session,
    records: Iterable[GovernanceRecord],
    *,
    allowed_source_element_ids: frozenset[str] | None = None,
    allowed_idea_block_ids: frozenset[str] | None = None,
    allowed_entity_ids: frozenset[str] | None = None,
    allowed_related_record_ids: frozenset[str] | None = None,
) -> list[dict[str, object]]:
    rows = list(records)
    by_id = _detail_for_records(
        session,
        rows,
        allowed_source_element_ids=allowed_source_element_ids,
        allowed_idea_block_ids=allowed_idea_block_ids,
        allowed_entity_ids=allowed_entity_ids,
        allowed_related_record_ids=allowed_related_record_ids,
    )
    return [by_id[record.id] for record in rows]


def governance_record_source_element_ids(session: Session, records: Iterable[GovernanceRecord]) -> frozenset[str]:
    """Find source evidence identities without hydrating their quotes."""

    rows = list(records)
    if not rows:
        return frozenset()
    record_ids = [record.id for record in rows]
    revision_ids = session.scalars(
        select(GovernanceRecordRevision.id).where(GovernanceRecordRevision.governance_record_id.in_(record_ids))
    ).all()
    idea_block_ids = session.scalars(
        select(GovernanceRecordIdeaBlock.idea_block_id).where(
            GovernanceRecordIdeaBlock.governance_record_id.in_(record_ids)
        )
    ).all()
    if not revision_ids and not idea_block_ids:
        return frozenset()
    source_ids = session.scalars(
        select(ClaimEvidence.source_element_id).where(
            (ClaimEvidence.idea_block_id.in_(idea_block_ids))
            | (ClaimEvidence.governance_record_revision_id.in_(revision_ids)),
            ClaimEvidence.source_element_id.is_not(None),
        )
    ).all()
    return frozenset(source_id for source_id in source_ids if source_id is not None)


def governance_record_linked_ids(
    session: Session, records: Iterable[GovernanceRecord]
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return link identities so callers can authorize each detail surface first."""

    record_ids = [record.id for record in records]
    if not record_ids:
        return frozenset(), frozenset(), frozenset()
    idea_block_ids = frozenset(
        session.scalars(
            select(GovernanceRecordIdeaBlock.idea_block_id).where(
                GovernanceRecordIdeaBlock.governance_record_id.in_(record_ids)
            )
        ).all()
    )
    entity_ids = frozenset(
        session.scalars(
            select(GovernanceRecordEntity.entity_id).where(GovernanceRecordEntity.governance_record_id.in_(record_ids))
        ).all()
    )
    relation_rows = session.execute(
        select(GovernanceRecordRelation.source_record_id, GovernanceRecordRelation.target_record_id).where(
            GovernanceRecordRelation.source_record_id.in_(record_ids)
            | GovernanceRecordRelation.target_record_id.in_(record_ids)
        )
    ).all()
    related_record_ids = frozenset(
        record_id for relation in relation_rows for record_id in relation if record_id not in record_ids
    )
    return idea_block_ids, entity_ids, related_record_ids


def _detail_for_records(
    session: Session,
    records: list[GovernanceRecord],
    *,
    allowed_source_element_ids: frozenset[str] | None,
    allowed_idea_block_ids: frozenset[str] | None = None,
    allowed_entity_ids: frozenset[str] | None = None,
    allowed_related_record_ids: frozenset[str] | None = None,
) -> dict[str, dict[str, object]]:
    if not records:
        return {}
    record_ids = [record.id for record in records]
    revisions = session.scalars(
        select(GovernanceRecordRevision)
        .where(GovernanceRecordRevision.governance_record_id.in_(record_ids))
        .order_by(GovernanceRecordRevision.governance_record_id, GovernanceRecordRevision.revision_number)
    ).all()
    revision_ids = [revision.id for revision in revisions]
    idea_links_query = select(GovernanceRecordIdeaBlock).where(
        GovernanceRecordIdeaBlock.governance_record_id.in_(record_ids)
    )
    if allowed_idea_block_ids is not None:
        idea_links_query = idea_links_query.where(GovernanceRecordIdeaBlock.idea_block_id.in_(allowed_idea_block_ids))
    idea_links = session.scalars(
        idea_links_query.order_by(GovernanceRecordIdeaBlock.display_order, GovernanceRecordIdeaBlock.idea_block_id)
    ).all()
    entity_links_query = select(GovernanceRecordEntity).where(
        GovernanceRecordEntity.governance_record_id.in_(record_ids)
    )
    if allowed_entity_ids is not None:
        entity_links_query = entity_links_query.where(GovernanceRecordEntity.entity_id.in_(allowed_entity_ids))
    entity_links = session.scalars(
        entity_links_query.order_by(GovernanceRecordEntity.entity_id, GovernanceRecordEntity.role)
    ).all()
    relations_query = select(GovernanceRecordRelation).where(
        GovernanceRecordRelation.source_record_id.in_(record_ids)
        | GovernanceRecordRelation.target_record_id.in_(record_ids)
    )
    if allowed_related_record_ids is not None:
        visible_record_ids = allowed_related_record_ids | frozenset(record_ids)
        relations_query = relations_query.where(
            GovernanceRecordRelation.source_record_id.in_(visible_record_ids),
            GovernanceRecordRelation.target_record_id.in_(visible_record_ids),
        )
    relations = session.scalars(
        relations_query.order_by(GovernanceRecordRelation.created_at, GovernanceRecordRelation.id)
    ).all()
    relation_ids = [relation.id for relation in relations]
    audit_events = session.scalars(
        select(AuditEvent)
        .where(
            or_(
                (AuditEvent.target_type == "governance_record") & AuditEvent.target_id.in_(record_ids),
                (AuditEvent.target_type == "governance_record_relation") & AuditEvent.target_id.in_(relation_ids),
            )
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id)
    ).all()
    idea_block_ids = [link.idea_block_id for link in idea_links]
    evidence: list[ClaimEvidence] = []
    if idea_block_ids or revision_ids:
        evidence_query = select(ClaimEvidence).where(
            ClaimEvidence.status == "active",
            (ClaimEvidence.idea_block_id.in_(idea_block_ids) & ClaimEvidence.source_element_id.is_not(None))
            | ClaimEvidence.governance_record_revision_id.in_(revision_ids),
        )
        if allowed_source_element_ids is not None:
            evidence_query = evidence_query.where(
                ClaimEvidence.source_element_id.is_(None)
                | ClaimEvidence.source_element_id.in_(allowed_source_element_ids)
            )
        evidence = list(session.scalars(evidence_query).all())
    blocks: dict[str, IdeaBlock] = {
        block.id: block for block in session.scalars(select(IdeaBlock).where(IdeaBlock.id.in_(idea_block_ids))).all()
    }
    revisions_by_record: dict[str, list[GovernanceRecordRevision]] = defaultdict(list)
    for revision in revisions:
        revisions_by_record[revision.governance_record_id].append(revision)
    links_by_record: dict[str, list[GovernanceRecordIdeaBlock]] = defaultdict(list)
    for link in idea_links:
        links_by_record[link.governance_record_id].append(link)
    entities_by_record: dict[str, list[GovernanceRecordEntity]] = defaultdict(list)
    for link in entity_links:
        entities_by_record[link.governance_record_id].append(link)
    relations_by_record: dict[str, list[GovernanceRecordRelation]] = defaultdict(list)
    for relation in relations:
        relations_by_record[relation.source_record_id].append(relation)
        if relation.target_record_id != relation.source_record_id:
            relations_by_record[relation.target_record_id].append(relation)
    audit_by_record: dict[str, list[AuditEvent]] = defaultdict(list)
    relation_by_id = {relation.id: relation for relation in relations}
    for event in audit_events:
        if event.target_type == "governance_record":
            audit_by_record[event.target_id].append(event)
            continue
        relation = relation_by_id.get(event.target_id)
        if relation is not None:
            audit_by_record[relation.source_record_id].append(event)
            if relation.target_record_id != relation.source_record_id:
                audit_by_record[relation.target_record_id].append(event)
    evidence_by_record: dict[str, list[ClaimEvidence]] = defaultdict(list)
    revision_to_record = {revision.id: revision.governance_record_id for revision in revisions}
    block_to_records: dict[str, set[str]] = defaultdict(set)
    for link in idea_links:
        block_to_records[link.idea_block_id].add(link.governance_record_id)
    for item in evidence:
        if item.governance_record_revision_id in revision_to_record:
            evidence_by_record[revision_to_record[item.governance_record_revision_id]].append(item)
        for linked_record_id in block_to_records.get(item.idea_block_id, set()):
            evidence_by_record[linked_record_id].append(item)
    return {
        record.id: {
            **serialize_record(record),
            "current_revision": _serialize_revision(
                next((item for item in revisions_by_record[record.id] if item.id == record.current_revision_id), None)
            ),
            "history": [_serialize_revision(item) for item in revisions_by_record[record.id]],
            "idea_blocks": [
                {
                    "id": link.idea_block_id,
                    "role": link.role,
                    "display_order": link.display_order,
                    "domain_id": (block.domain_id if (block := blocks.get(link.idea_block_id)) is not None else None),
                }
                for link in links_by_record[record.id]
            ],
            "entities": [{"id": link.entity_id, "role": link.role} for link in entities_by_record[record.id]],
            "relations": [_serialize_relation(item) for item in relations_by_record[record.id]],
            "audit": [_serialize_audit_event(item) for item in audit_by_record[record.id]],
            "evidence": [_serialize_evidence(item) for item in evidence_by_record[record.id]],
        }
        for record in records
    }


def serialize_record(record: GovernanceRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "business_key": record.business_key,
        "record_type": record.record_type,
        "title": record.title,
        "domain_id": record.domain_id,
        "lifecycle_status": record.lifecycle_status,
        "owner_id": record.owner_id,
        "steward_id": record.steward_id,
        "effective_date": _date_value(record.effective_date),
        "review_date": _date_value(record.review_date),
        "expiry_date": _date_value(record.expiry_date),
        "target_date": _date_value(record.target_date),
        "priority": record.priority,
        "severity": record.severity,
        "external_issue_id": record.external_issue_id,
        "external_issue_url": record.external_issue_url,
        "schema_version": record.schema_version,
        "attributes": record.attributes,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _serialize_revision(revision: GovernanceRecordRevision | None) -> dict[str, object] | None:
    if revision is None:
        return None
    return {
        "id": revision.id,
        "revision_number": revision.revision_number,
        "snapshot": revision.snapshot,
        "transition_reason": revision.transition_reason,
        "actor": revision.actor,
        "process": revision.process,
        "created_at": revision.created_at.isoformat(),
    }


def _serialize_relation(relation: GovernanceRecordRelation) -> dict[str, object]:
    return {
        "id": relation.id,
        "source_record_id": relation.source_record_id,
        "target_record_id": relation.target_record_id,
        "relation_type": relation.relation_type,
        "status": relation.status,
        "valid_from": _date_value(relation.valid_from),
        "valid_to": _date_value(relation.valid_to),
        "provenance": relation.provenance,
    }


def _serialize_evidence(evidence: ClaimEvidence) -> dict[str, object]:
    return {
        "id": evidence.id,
        "idea_block_id": evidence.idea_block_id,
        "idea_block_revision_id": evidence.idea_block_revision_id,
        "source_element_id": evidence.source_element_id,
        "governance_record_revision_id": evidence.governance_record_revision_id,
        "source_quote": evidence.source_quote,
        "polarity": evidence.polarity,
        "status": evidence.status,
    }


def _serialize_audit_event(event: AuditEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "action": event.action,
        "actor": event.actor,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "correlation_id": event.correlation_id,
        "created_at": event.created_at.isoformat(),
    }


def _date_value(value) -> str | None:
    return value.isoformat() if value else None
