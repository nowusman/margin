"""Transactional record, link, and revision operations for governance."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from margin_db.audit import redact_audit_payload, write_audit_event
from margin_db.entity_models import Entity
from margin_db.governance_models import (
    GovernanceRecord,
    GovernanceRecordEntity,
    GovernanceRecordIdeaBlock,
    GovernanceRecordRelation,
    GovernanceRecordRevision,
)
from margin_db.governance_types import GovernanceError, validate_dates, validate_record_fields, validate_record_type
from margin_db.knowledge_models import IdeaBlock, IdeaBlockRevision
from margin_schema.ontology import governance_relationship_definition, validate_governance_relationship_endpoints

INITIAL_STATES = {
    "adr": "proposed",
    "risk": "identified",
    "waiver": "proposed",
    "technical_debt": "identified",
    "architecture_backlog": "open",
}
_MUTABLE_FIELDS = frozenset(
    {
        "title",
        "owner_id",
        "steward_id",
        "effective_date",
        "review_date",
        "expiry_date",
        "target_date",
        "priority",
        "severity",
        "external_issue_id",
        "external_issue_url",
        "attributes",
    }
)


def create_record(
    session: Session,
    *,
    business_key: str,
    record_type: str,
    title: str,
    domain_id: str,
    attributes: Mapping[str, object],
    actor: str,
    process: str = "api",
    reason: str = "record created",
    record_id: str | None = None,
    owner_id: str | None = None,
    steward_id: str | None = None,
    effective_date: date | None = None,
    review_date: date | None = None,
    expiry_date: date | None = None,
    target_date: date | None = None,
    priority: str | None = None,
    severity: int | None = None,
    external_issue_id: str | None = None,
    external_issue_url: str | None = None,
    backlog_origin: GovernanceRecord | None = None,
    correlation_id: str | None = None,
) -> GovernanceRecord:
    record_type = validate_record_type(record_type)
    clean_actor = _required_text(actor, "actor")
    clean_process = _required_text(process, "process")
    clean_reason = _required_text(reason, "reason")
    clean_key = _required_text(business_key, "business_key")
    clean_title = _required_text(title, "title")
    clean_domain = _required_text(domain_id, "domain_id")
    if not isinstance(attributes, Mapping):
        raise GovernanceError("attributes must be an object")
    payload = dict(attributes)
    originating_record_id = payload.get("originating_governance_record_id")
    has_record_origin = isinstance(originating_record_id, str) and bool(originating_record_id.strip())
    if has_record_origin and backlog_origin is None:
        raise GovernanceError("Architecture Backlog with a governance origin requires the explicit backlog operation")
    if backlog_origin is not None:
        if record_type != "architecture_backlog":
            raise GovernanceError("backlog origin is only valid for Architecture Backlog records")
        if not has_record_origin or originating_record_id != backlog_origin.id:
            raise GovernanceError("Architecture Backlog origin does not match the resolved source record")
    clean_priority = _optional_text(priority, "priority")
    validate_record_fields(record_type, payload, priority=clean_priority, target_date=target_date)
    expiry_date = _validated_waiver_expiry(record_type, payload, expiry_date)
    validate_dates(
        effective_date=effective_date, review_date=review_date, expiry_date=expiry_date, target_date=target_date
    )
    record = GovernanceRecord(
        id=record_id or f"gov_{uuid4().hex}",
        business_key=clean_key,
        record_type=record_type,
        title=clean_title,
        domain_id=clean_domain,
        lifecycle_status=INITIAL_STATES[record_type],
        owner_id=_optional_text(owner_id, "owner_id"),
        steward_id=_optional_text(steward_id, "steward_id"),
        effective_date=effective_date,
        review_date=review_date,
        expiry_date=expiry_date,
        target_date=target_date,
        priority=clean_priority,
        severity=_severity_value(severity),
        external_issue_id=_optional_text(external_issue_id, "external_issue_id"),
        external_issue_url=_optional_text(external_issue_url, "external_issue_url"),
        attributes=payload,
    )
    session.add(record)
    session.flush()
    revision = append_revision(session, record=record, actor=clean_actor, process=clean_process, reason=clean_reason)
    write_audit_event(
        session,
        actor=clean_actor,
        action="governance.record.created",
        target_type="governance_record",
        target_id=record.id,
        after={
            "new_revision_id": revision.id,
            "record_type": record.record_type,
            "status": record.lifecycle_status,
            "process": clean_process,
            "reason": clean_reason,
        },
        correlation_id=correlation_id,
    )
    if backlog_origin is not None:
        add_record_relation(
            session,
            source=record,
            target=backlog_origin,
            relation_type="corrects",
            actor=clean_actor,
            status="verified",
            provenance={"reason": clean_reason},
            expected_source_revision_id=record.current_revision_id,
            expected_target_revision_id=backlog_origin.current_revision_id,
            correlation_id=correlation_id,
        )
    return record


def update_mutable_fields(
    session: Session,
    *,
    record: GovernanceRecord,
    changes: Mapping[str, object],
    actor: str,
    reason: str,
    expected_revision_id: str,
    process: str = "api",
    correlation_id: str | None = None,
) -> GovernanceRecord:
    _require_expected_revision(record, expected_revision_id, "record")
    if not changes:
        raise GovernanceError("at least one mutable field is required")
    unknown = sorted(set(changes) - _MUTABLE_FIELDS)
    if unknown:
        raise GovernanceError(f"immutable or unknown governance fields: {', '.join(unknown)}")

    clean_actor = _required_text(actor, "actor")
    clean_process = _required_text(process, "process")
    clean_reason = _required_text(reason, "reason")
    prospective_attributes = dict(record.attributes)
    prospective_values: dict[str, object] = {
        "title": record.title,
        "effective_date": record.effective_date,
        "review_date": record.review_date,
        "expiry_date": record.expiry_date,
        "target_date": record.target_date,
    }
    normalized_changes: dict[str, object] = {}
    for field_name, value in changes.items():
        if field_name == "attributes":
            if not isinstance(value, Mapping):
                raise GovernanceError("attributes must be an object")
            prospective_attributes = dict(value)
            normalized_changes[field_name] = prospective_attributes
        elif field_name == "title":
            normalized_changes[field_name] = _required_text(value, "title")
            prospective_values[field_name] = normalized_changes[field_name]
        elif field_name in {"external_issue_id", "external_issue_url"}:
            normalized_changes[field_name] = _optional_text(value, field_name)
        elif field_name in {"owner_id", "steward_id", "priority"}:
            normalized_changes[field_name] = _optional_text(value, field_name)
        elif field_name == "severity":
            normalized_changes[field_name] = _severity_value(value)
        else:
            normalized_changes[field_name] = value
            if field_name in prospective_values:
                prospective_values[field_name] = value
    validate_record_fields(
        record.record_type,
        prospective_attributes,
        priority=normalized_changes.get("priority", record.priority),
        target_date=prospective_values["target_date"],
    )
    prospective_values["expiry_date"] = _validated_waiver_expiry(
        record.record_type, prospective_attributes, cast(date | None, prospective_values["expiry_date"])
    )
    validate_dates(
        effective_date=prospective_values["effective_date"],
        review_date=prospective_values["review_date"],
        expiry_date=prospective_values["expiry_date"],
        target_date=prospective_values["target_date"],
    )
    before = snapshot_record(record)
    previous_revision_id = record.current_revision_id
    for field_name, value in normalized_changes.items():
        setattr(record, field_name, value)
    revision = append_revision(session, record=record, actor=clean_actor, process=clean_process, reason=clean_reason)
    write_audit_event(
        session,
        actor=clean_actor,
        action="governance.record.updated",
        target_type="governance_record",
        target_id=record.id,
        before={"previous_revision_id": previous_revision_id, **audit_snapshot_record(record, before)},
        after={
            "new_revision_id": revision.id,
            "process": clean_process,
            "reason": clean_reason,
            **audit_snapshot_record(record),
        },
        correlation_id=correlation_id,
    )
    return record


def append_revision(
    session: Session, *, record: GovernanceRecord, actor: str, process: str, reason: str
) -> GovernanceRecordRevision:
    validate_record_type(record.record_type)
    validate_record_fields(
        record.record_type, dict(record.attributes), priority=record.priority, target_date=record.target_date
    )
    record.expiry_date = _validated_waiver_expiry(record.record_type, record.attributes, record.expiry_date)
    validate_dates(
        effective_date=record.effective_date,
        review_date=record.review_date,
        expiry_date=record.expiry_date,
        target_date=record.target_date,
    )
    revision_number = (
        session.scalar(
            select(func.max(GovernanceRecordRevision.revision_number)).where(
                GovernanceRecordRevision.governance_record_id == record.id
            )
        )
        or 0
    ) + 1
    revision = GovernanceRecordRevision(
        id=f"govr_{uuid4().hex}",
        governance_record_id=record.id,
        revision_number=revision_number,
        snapshot=snapshot_record(record),
        transition_reason=_required_text(reason, "reason"),
        actor=_required_text(actor, "actor"),
        process=_required_text(process, "process"),
    )
    session.add(revision)
    session.flush()
    record.current_revision_id = revision.id
    return revision


def link_idea_block(
    session: Session,
    *,
    record: GovernanceRecord,
    idea_block_id: str,
    role: str,
    display_order: int,
    actor: str,
    reason: str,
    expected_revision_id: str,
    correlation_id: str | None = None,
) -> GovernanceRecordIdeaBlock:
    _require_expected_revision(record, expected_revision_id, "record")
    clean_actor = _required_text(actor, "actor")
    clean_reason = _required_text(reason, "reason")
    clean_role = _required_text(role, "role")
    if isinstance(display_order, bool) or not isinstance(display_order, int) or display_order < 0:
        raise GovernanceError("display_order must be a non-negative integer")
    idea_block = session.get(IdeaBlock, idea_block_id)
    if idea_block is None:
        raise GovernanceError("Idea Block not found")
    if idea_block.domain_id != record.domain_id:
        raise GovernanceError("Idea Block must be in the governance record domain")
    if (
        idea_block.merged_into_id is not None
        or idea_block.tombstoned_at is not None
        or idea_block.archived_at is not None
    ):
        raise GovernanceError("Idea Block is not active")
    current_revision = session.get(IdeaBlockRevision, idea_block.current_revision_id)
    if current_revision is None or current_revision.idea_block_id != idea_block.id:
        raise GovernanceError("Idea Block has no valid current revision")
    link = session.get(
        GovernanceRecordIdeaBlock,
        {"governance_record_id": record.id, "idea_block_id": idea_block_id, "role": clean_role},
    )
    previous_revision_id = record.current_revision_id
    before: dict[str, object] | None = None
    if link is None:
        link = GovernanceRecordIdeaBlock(
            governance_record_id=record.id, idea_block_id=idea_block_id, role=clean_role, display_order=display_order
        )
        session.add(link)
    else:
        before = {
            "previous_revision_id": previous_revision_id,
            "idea_block_id": idea_block_id,
            "role": link.role,
            "display_order": link.display_order,
        }
        link.display_order = display_order
    revision = append_revision(session, record=record, actor=clean_actor, process="api", reason=clean_reason)
    write_audit_event(
        session,
        actor=clean_actor,
        action="governance.idea_block.linked",
        target_type="governance_record",
        target_id=record.id,
        before=before,
        after={
            "new_revision_id": revision.id,
            "idea_block_id": idea_block_id,
            "role": link.role,
            "display_order": display_order,
            "process": "api",
            "reason": clean_reason,
        },
        correlation_id=correlation_id,
    )
    return link


def unlink_idea_block(
    session: Session,
    *,
    record: GovernanceRecord,
    idea_block_id: str,
    role: str,
    actor: str,
    reason: str,
    expected_revision_id: str,
    correlation_id: str | None = None,
) -> None:
    _require_expected_revision(record, expected_revision_id, "record")
    clean_actor = _required_text(actor, "actor")
    clean_reason = _required_text(reason, "reason")
    clean_role = _required_text(role, "role")
    link = session.get(
        GovernanceRecordIdeaBlock,
        {"governance_record_id": record.id, "idea_block_id": idea_block_id, "role": clean_role},
    )
    if link is None:
        raise GovernanceError("Idea Block link not found")
    previous_revision_id = record.current_revision_id
    before = {
        "previous_revision_id": previous_revision_id,
        "idea_block_id": idea_block_id,
        "role": clean_role,
        "display_order": link.display_order,
    }
    session.delete(link)
    revision = append_revision(session, record=record, actor=clean_actor, process="api", reason=clean_reason)
    write_audit_event(
        session,
        actor=clean_actor,
        action="governance.idea_block.unlinked",
        target_type="governance_record",
        target_id=record.id,
        before=before,
        after={"new_revision_id": revision.id, "process": "api", "reason": clean_reason},
        correlation_id=correlation_id,
    )


def link_entity(
    session: Session,
    *,
    record: GovernanceRecord,
    entity_id: str,
    role: str,
    actor: str,
    reason: str,
    expected_revision_id: str,
    correlation_id: str | None = None,
) -> GovernanceRecordEntity:
    _require_expected_revision(record, expected_revision_id, "record")
    clean_actor = _required_text(actor, "actor")
    clean_reason = _required_text(reason, "reason")
    entity = session.get(Entity, entity_id)
    if entity is None:
        raise GovernanceError("Entity not found")
    if entity.lifecycle_status != "active":
        raise GovernanceError("Entity is not active")
    if entity.domain_id not in {None, record.domain_id}:
        raise GovernanceError("Entity must be global or in the governance record domain")
    clean_role = _required_text(role, "role")
    link = session.get(
        GovernanceRecordEntity, {"governance_record_id": record.id, "entity_id": entity_id, "role": clean_role}
    )
    if link is None:
        previous_revision_id = record.current_revision_id
        link = GovernanceRecordEntity(governance_record_id=record.id, entity_id=entity_id, role=clean_role)
        session.add(link)
        revision = append_revision(session, record=record, actor=clean_actor, process="api", reason=clean_reason)
        write_audit_event(
            session,
            actor=clean_actor,
            action="governance.entity.linked",
            target_type="governance_record",
            target_id=record.id,
            before={"previous_revision_id": previous_revision_id},
            after={
                "new_revision_id": revision.id,
                "entity_id": entity_id,
                "role": clean_role,
                "process": "api",
                "reason": clean_reason,
            },
            correlation_id=correlation_id,
        )
    return link


def unlink_entity(
    session: Session,
    *,
    record: GovernanceRecord,
    entity_id: str,
    role: str,
    actor: str,
    reason: str,
    expected_revision_id: str,
    correlation_id: str | None = None,
) -> None:
    _require_expected_revision(record, expected_revision_id, "record")
    clean_actor = _required_text(actor, "actor")
    clean_reason = _required_text(reason, "reason")
    clean_role = _required_text(role, "role")
    link = session.get(
        GovernanceRecordEntity, {"governance_record_id": record.id, "entity_id": entity_id, "role": clean_role}
    )
    if link is None:
        raise GovernanceError("Entity link not found")
    previous_revision_id = record.current_revision_id
    session.delete(link)
    revision = append_revision(session, record=record, actor=clean_actor, process="api", reason=clean_reason)
    write_audit_event(
        session,
        actor=clean_actor,
        action="governance.entity.unlinked",
        target_type="governance_record",
        target_id=record.id,
        before={"previous_revision_id": previous_revision_id, "entity_id": entity_id, "role": role},
        after={"new_revision_id": revision.id, "process": "api", "reason": clean_reason},
        correlation_id=correlation_id,
    )


def add_record_relation(
    session: Session,
    *,
    source: GovernanceRecord,
    target: GovernanceRecord,
    relation_type: str,
    actor: str,
    status: str = "candidate",
    valid_from: date | None = None,
    valid_to: date | None = None,
    provenance: Mapping[str, object] | None = None,
    expected_source_revision_id: str | None = None,
    expected_target_revision_id: str | None = None,
    correlation_id: str | None = None,
) -> GovernanceRecordRelation:
    if relation_type == "supersedes":
        raise GovernanceError("use the explicit ADR supersession operation")
    return _persist_record_relation(
        session,
        source=source,
        target=target,
        relation_type=relation_type,
        actor=actor,
        status=status,
        valid_from=valid_from,
        valid_to=valid_to,
        provenance=provenance,
        expected_source_revision_id=expected_source_revision_id,
        expected_target_revision_id=expected_target_revision_id,
        correlation_id=correlation_id,
    )


def add_supersession_relation(
    session: Session,
    *,
    source: GovernanceRecord,
    target: GovernanceRecord,
    actor: str,
    valid_from: date | None = None,
    valid_to: date | None = None,
    provenance: Mapping[str, object] | None = None,
    expected_source_revision_id: str | None = None,
    expected_target_revision_id: str | None = None,
    correlation_id: str | None = None,
) -> GovernanceRecordRelation:
    if source.record_type != "adr" or target.record_type != "adr":
        raise GovernanceError("supersession must connect two ADR records")
    return _persist_record_relation(
        session,
        source=source,
        target=target,
        relation_type="supersedes",
        actor=actor,
        status="verified",
        valid_from=valid_from,
        valid_to=valid_to,
        provenance=provenance,
        expected_source_revision_id=expected_source_revision_id,
        expected_target_revision_id=expected_target_revision_id,
        correlation_id=correlation_id,
    )


def _persist_record_relation(
    session: Session,
    *,
    source: GovernanceRecord,
    target: GovernanceRecord,
    relation_type: str,
    actor: str,
    status: str = "candidate",
    valid_from: date | None = None,
    valid_to: date | None = None,
    provenance: Mapping[str, object] | None = None,
    expected_source_revision_id: str | None = None,
    expected_target_revision_id: str | None = None,
    correlation_id: str | None = None,
) -> GovernanceRecordRelation:
    _require_expected_revision(source, expected_source_revision_id, "source")
    _require_expected_revision(target, expected_target_revision_id, "target")
    clean_actor = _required_text(actor, "actor")
    if provenance is not None and not isinstance(provenance, Mapping):
        raise GovernanceError("relation provenance must be an object")
    clean_reason = _required_text((provenance or {}).get("reason"), "reason")
    if not isinstance(relation_type, str):
        raise GovernanceError("relation type must be text")
    try:
        definition = governance_relationship_definition(relation_type)
    except ValueError as exc:
        raise GovernanceError(str(exc)) from exc
    relation_type = definition.name
    if not isinstance(status, str):
        raise GovernanceError("relation status must be text")
    if source.id == target.id:
        raise GovernanceError("governance relation endpoints must differ")
    if source.domain_id != target.domain_id:
        raise GovernanceError("governance relations cannot cross domains")
    try:
        validate_governance_relationship_endpoints(relation_type, source.record_type, target.record_type)
    except ValueError as exc:
        raise GovernanceError(str(exc)) from exc
    if valid_from is not None and (not isinstance(valid_from, date) or isinstance(valid_from, datetime)):
        raise GovernanceError("relation valid_from must be a date")
    if valid_to is not None and (not isinstance(valid_to, date) or isinstance(valid_to, datetime)):
        raise GovernanceError("relation valid_to must be a date")
    if status not in {"candidate", "verified", "rejected", "stale"}:
        raise GovernanceError(f"invalid governance relation status: {status}")
    if valid_from is not None and valid_to is not None and valid_to < valid_from:
        raise GovernanceError("relation valid_to cannot precede valid_from")
    source_previous_revision_id = source.current_revision_id
    target_previous_revision_id = target.current_revision_id
    relation = session.scalar(
        select(GovernanceRecordRelation).where(
            GovernanceRecordRelation.source_record_id == source.id,
            GovernanceRecordRelation.target_record_id == target.id,
            GovernanceRecordRelation.relation_type == relation_type,
        )
    )
    before: dict[str, object] | None = None
    if relation is None:
        relation = GovernanceRecordRelation(
            id=f"govrel_{uuid4().hex}",
            source_record_id=source.id,
            target_record_id=target.id,
            relation_type=relation_type,
            status=status,
            valid_from=valid_from,
            valid_to=valid_to,
            provenance=dict(provenance or {}),
        )
        session.add(relation)
    else:
        before = {
            "source_previous_revision_id": source_previous_revision_id,
            "target_previous_revision_id": target_previous_revision_id,
            "status": relation.status,
            "valid_from": _date_value(relation.valid_from),
            "valid_to": _date_value(relation.valid_to),
            "provenance": dict(relation.provenance or {}),
        }
        relation.status = status
        relation.valid_from = valid_from
        relation.valid_to = valid_to
        relation.provenance = dict(provenance or {})
    source_revision = append_revision(session, record=source, actor=clean_actor, process="api", reason=clean_reason)
    target_revision = append_revision(session, record=target, actor=clean_actor, process="api", reason=clean_reason)
    write_audit_event(
        session,
        actor=clean_actor,
        action="governance.relation.changed",
        target_type="governance_record_relation",
        target_id=relation.id,
        before=before
        or {
            "source_previous_revision_id": source_previous_revision_id,
            "target_previous_revision_id": target_previous_revision_id,
        },
        after={
            "source_record_id": source.id,
            "target_record_id": target.id,
            "relation_type": relation_type,
            "status": status,
            "valid_from": _date_value(valid_from),
            "valid_to": _date_value(valid_to),
            "source_new_revision_id": source_revision.id,
            "target_new_revision_id": target_revision.id,
            "process": "api",
            "reason": clean_reason,
        },
        correlation_id=correlation_id,
    )
    return relation


def snapshot_record(record: GovernanceRecord) -> dict[str, object]:
    return {
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
        "attributes": dict(record.attributes),
    }


def audit_snapshot_record(record: GovernanceRecord, snapshot: Mapping[str, object] | None = None) -> dict[str, object]:
    """Return a record state safe for audit metadata without user-supplied content."""

    safe_snapshot = redact_audit_payload(snapshot if snapshot is not None else snapshot_record(record))
    if not isinstance(safe_snapshot, dict):
        raise TypeError("governance audit snapshot must be an object")
    safe_snapshot.pop("attributes", None)
    return safe_snapshot


def _date_value(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GovernanceError(f"{field_name} is required")
    return value.strip()


def _optional_text(value: object, field_name: str = "external reference") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GovernanceError(f"{field_name} must be text")
    return value.strip() or None


def _require_expected_revision(record: GovernanceRecord, expected_revision_id: str | None, endpoint_name: str) -> None:
    if not isinstance(expected_revision_id, str) or not expected_revision_id.strip():
        raise GovernanceError(f"{endpoint_name} expected revision is required")
    if record.current_revision_id != expected_revision_id:
        raise GovernanceError(f"{endpoint_name} governance record has a newer revision; refresh and retry")


def _severity_value(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GovernanceError("severity must be a non-negative integer")
    return value


def _validated_waiver_expiry(
    record_type: str, attributes: Mapping[str, object], expiry_date: date | None
) -> date | None:
    """Keep the common waiver expiry field aligned with its typed attribute."""

    if record_type != "waiver":
        return expiry_date
    attribute_value = attributes.get("expiry_date")
    if not isinstance(attribute_value, str) or not attribute_value.strip():
        raise GovernanceError("waiver requires expiry_date")
    try:
        attribute_date = date.fromisoformat(attribute_value.strip())
    except ValueError as exc:
        raise GovernanceError("waiver expiry_date must use ISO date format YYYY-MM-DD") from exc
    if expiry_date is None:
        return attribute_date
    if not isinstance(expiry_date, date) or expiry_date != attribute_date:
        raise GovernanceError("waiver expiry_date must match the typed attribute")
    return expiry_date
