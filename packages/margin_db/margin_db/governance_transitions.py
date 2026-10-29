"""Explicit lifecycle operations for canonical governance records."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime

from sqlalchemy.orm import Session

from margin_db.audit import write_audit_event
from margin_db.governance_models import GovernanceRecord
from margin_db.governance_records import (
    _require_expected_revision,
    _required_text,
    _validated_waiver_expiry,
    add_supersession_relation,
    append_revision,
    audit_snapshot_record,
    create_record,
    snapshot_record,
)
from margin_db.governance_types import BACKLOG_ORIGIN_STATUSES, GovernanceError, validate_transition


def transition_record(
    session: Session,
    *,
    record: GovernanceRecord,
    target_status: str,
    actor: str,
    reason: str,
    expected_revision_id: str,
    process: str = "api",
    attributes: Mapping[str, object] | None = None,
    correlation_id: str | None = None,
) -> GovernanceRecord:
    if (record.record_type, target_status) in {
        ("adr", "superseded"),
        ("risk", "accepted"),
        ("risk", "closed"),
        ("waiver", "revoked"),
        ("technical_debt", "resolved"),
        ("architecture_backlog", "completed"),
    }:
        raise GovernanceError("use the explicit lifecycle operation for this transition")
    return _apply_transition(
        session,
        record=record,
        target_status=target_status,
        actor=actor,
        reason=reason,
        process=process,
        attributes=attributes,
        expected_revision_id=expected_revision_id,
        audit_action="governance.record.transitioned",
        correlation_id=correlation_id,
    )


def _apply_transition(
    session: Session,
    *,
    record: GovernanceRecord,
    target_status: str,
    actor: str,
    reason: str,
    process: str,
    attributes: Mapping[str, object] | None,
    expected_revision_id: str,
    audit_action: str,
    correlation_id: str | None,
) -> GovernanceRecord:
    clean_actor = _required_text(actor, "actor")
    clean_process = _required_text(process, "process")
    clean_reason = _required_text(reason, "reason")
    _require_expected_revision(record, expected_revision_id, "record")
    before = snapshot_record(record)
    previous_revision_id = record.current_revision_id
    if attributes is not None and not isinstance(attributes, Mapping):
        raise GovernanceError("attributes must be an object")
    next_attributes = dict(attributes) if attributes is not None else dict(record.attributes)
    validate_transition(record.record_type, record.lifecycle_status, target_status, next_attributes)
    if record.record_type == "waiver":
        record.expiry_date = _validated_waiver_expiry(record.record_type, next_attributes, record.expiry_date)
    if attributes is not None:
        record.attributes = next_attributes
    record.lifecycle_status = target_status
    revision = append_revision(session, record=record, actor=clean_actor, process=clean_process, reason=clean_reason)
    write_audit_event(
        session,
        actor=clean_actor,
        action=audit_action,
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


def supersede_record(
    session: Session,
    *,
    record: GovernanceRecord,
    successor: GovernanceRecord,
    actor: str,
    reason: str,
    expected_source_revision_id: str | None = None,
    expected_successor_revision_id: str | None = None,
    correlation_id: str | None = None,
) -> GovernanceRecord:
    if record.record_type != "adr" or successor.record_type != "adr":
        raise GovernanceError("only ADR records can supersede another ADR")
    if record.domain_id != successor.domain_id:
        raise GovernanceError("supersession cannot cross domains")
    _require_expected_revision(record, expected_source_revision_id, "source")
    _require_expected_revision(successor, expected_successor_revision_id, "successor")
    if expected_source_revision_id is None:
        raise GovernanceError("source expected revision is required")
    _apply_transition(
        session,
        record=record,
        target_status="superseded",
        actor=actor,
        reason=reason,
        process="api",
        attributes=None,
        expected_revision_id=expected_source_revision_id,
        audit_action="governance.record.superseded",
        correlation_id=correlation_id,
    )
    add_supersession_relation(
        session,
        source=successor,
        target=record,
        actor=actor,
        provenance={"reason": reason},
        expected_source_revision_id=successor.current_revision_id,
        expected_target_revision_id=record.current_revision_id,
        correlation_id=correlation_id,
    )
    return record


def accept_risk(
    session: Session,
    *,
    record: GovernanceRecord,
    actor: str,
    reason: str,
    expected_revision_id: str,
    correlation_id: str | None = None,
) -> GovernanceRecord:
    if record.record_type != "risk":
        raise GovernanceError("only risk records can be accepted")
    return _apply_transition(
        session,
        record=record,
        target_status="accepted",
        actor=actor,
        reason=reason,
        process="api",
        attributes=None,
        expected_revision_id=expected_revision_id,
        audit_action="governance.risk.accepted",
        correlation_id=correlation_id,
    )


def close_risk(
    session: Session,
    *,
    record: GovernanceRecord,
    actor: str,
    reason: str,
    expected_revision_id: str,
    correlation_id: str | None = None,
) -> GovernanceRecord:
    if record.record_type != "risk":
        raise GovernanceError("only risk records can be closed")
    return _apply_transition(
        session,
        record=record,
        target_status="closed",
        actor=actor,
        reason=reason,
        process="api",
        attributes=None,
        expected_revision_id=expected_revision_id,
        audit_action="governance.risk.closed",
        correlation_id=correlation_id,
    )


def revoke_waiver(
    session: Session,
    *,
    record: GovernanceRecord,
    actor: str,
    reason: str,
    expected_revision_id: str,
    correlation_id: str | None = None,
) -> GovernanceRecord:
    if record.record_type != "waiver":
        raise GovernanceError("only waiver records can be revoked")
    return _apply_transition(
        session,
        record=record,
        target_status="revoked",
        actor=actor,
        reason=reason,
        process="api",
        attributes=None,
        expected_revision_id=expected_revision_id,
        audit_action="governance.waiver.revoked",
        correlation_id=correlation_id,
    )


def resolve_technical_debt(
    session: Session,
    *,
    record: GovernanceRecord,
    resolution_note: str,
    actor: str,
    reason: str,
    expected_revision_id: str,
    correlation_id: str | None = None,
) -> GovernanceRecord:
    if record.record_type != "technical_debt":
        raise GovernanceError("only technical-debt records can be resolved")
    return _apply_transition(
        session,
        record=record,
        target_status="resolved",
        actor=actor,
        reason=reason,
        process="api",
        attributes={**record.attributes, "resolution_note": resolution_note},
        expected_revision_id=expected_revision_id,
        audit_action="governance.technical_debt.resolved",
        correlation_id=correlation_id,
    )


def complete_architecture_backlog(
    session: Session,
    *,
    record: GovernanceRecord,
    completion_note: str,
    actor: str,
    reason: str,
    expected_revision_id: str,
    correlation_id: str | None = None,
) -> GovernanceRecord:
    if record.record_type != "architecture_backlog":
        raise GovernanceError("only Architecture Backlog records can be completed")
    return _apply_transition(
        session,
        record=record,
        target_status="completed",
        actor=actor,
        reason=reason,
        process="api",
        attributes={**record.attributes, "completion_note": completion_note},
        expected_revision_id=expected_revision_id,
        audit_action="governance.architecture_backlog.completed",
        correlation_id=correlation_id,
    )


def renew_waiver(
    session: Session,
    *,
    record: GovernanceRecord,
    expiry_date: date,
    actor: str,
    reason: str,
    expected_revision_id: str,
    correlation_id: str | None = None,
) -> GovernanceRecord:
    """Extend an approved waiver through an explicit audited operation."""

    if record.record_type != "waiver":
        raise GovernanceError("only waiver records can be renewed")
    if not isinstance(expiry_date, date) or isinstance(expiry_date, datetime):
        raise GovernanceError("waiver expiry_date must be a date")
    if record.lifecycle_status != "approved":
        raise GovernanceError("only approved waiver records can be renewed")
    if record.expiry_date is not None and expiry_date <= record.expiry_date:
        raise GovernanceError("waiver renewal must extend the current expiry date")
    if record.effective_date is not None and expiry_date < record.effective_date:
        raise GovernanceError("waiver expiry cannot precede its effective date")
    clean_actor = _required_text(actor, "actor")
    clean_reason = _required_text(reason, "reason")
    _require_expected_revision(record, expected_revision_id, "record")

    before = snapshot_record(record)
    previous_revision_id = record.current_revision_id
    record.expiry_date = expiry_date
    record.attributes = {**record.attributes, "expiry_date": expiry_date.isoformat()}
    revision = append_revision(session, record=record, actor=clean_actor, process="api", reason=clean_reason)
    write_audit_event(
        session,
        actor=clean_actor,
        action="governance.waiver.renewed",
        target_type="governance_record",
        target_id=record.id,
        before={"previous_revision_id": previous_revision_id, **audit_snapshot_record(record, before)},
        after={
            "new_revision_id": revision.id,
            "process": "api",
            "reason": clean_reason,
            **audit_snapshot_record(record),
        },
        correlation_id=correlation_id,
    )
    return record


def create_architecture_backlog(
    session: Session,
    *,
    origin: GovernanceRecord,
    business_key: str,
    title: str,
    pending_correction: str,
    actor: str,
    reason: str,
    priority: str,
    target_date: date,
    expected_origin_revision_id: str | None = None,
    correlation_id: str | None = None,
) -> GovernanceRecord:
    if origin.record_type not in BACKLOG_ORIGIN_STATUSES:
        raise GovernanceError("Architecture Backlog origin must be ADR, waiver, risk, or technical debt")
    if origin.lifecycle_status not in BACKLOG_ORIGIN_STATUSES[origin.record_type]:
        raise GovernanceError(
            "Architecture Backlog origin must be an interim ADR, approved waiver, accepted risk, "
            "or unresolved technical debt"
        )
    _require_expected_revision(origin, expected_origin_revision_id, "origin")
    clean_priority = _required_text(priority, "priority")
    backlog = create_record(
        session,
        business_key=business_key,
        record_type="architecture_backlog",
        title=title,
        domain_id=origin.domain_id,
        attributes={"pending_correction": pending_correction, "originating_governance_record_id": origin.id},
        actor=actor,
        reason=reason,
        priority=clean_priority,
        target_date=target_date,
        backlog_origin=origin,
        correlation_id=correlation_id,
    )
    return backlog
