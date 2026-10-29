"""Strict contracts for the five governance record types."""

from __future__ import annotations

from datetime import date, datetime
from typing import Final

__all__ = [
    "BACKLOG_ORIGIN_STATUSES",
    "GovernanceError",
    "LIFECYCLE_STATES",
    "RECORD_TYPES",
    "REQUIRED_ATTRIBUTES",
    "validate_dates",
    "validate_record_fields",
    "validate_record_type",
    "validate_transition",
]


class GovernanceError(ValueError):
    """Raised when a governance request violates the lifecycle contract."""


RECORD_TYPES: Final[frozenset[str]] = frozenset({"adr", "risk", "waiver", "technical_debt", "architecture_backlog"})
LIFECYCLE_STATES: Final[dict[str, frozenset[str]]] = {
    "adr": frozenset({"proposed", "accepted", "superseded", "deprecated", "rejected"}),
    "risk": frozenset({"identified", "mitigating", "accepted", "closed"}),
    "waiver": frozenset({"proposed", "approved", "expired", "revoked", "rejected"}),
    "technical_debt": frozenset({"identified", "accepted", "planned", "in_progress", "resolved"}),
    "architecture_backlog": frozenset({"open", "planned", "in_progress", "blocked", "completed", "cancelled"}),
}
REQUIRED_ATTRIBUTES: Final[dict[str, frozenset[str]]] = {
    "adr": frozenset({"context", "decision", "rationale", "consequences"}),
    "risk": frozenset({"likelihood", "impact", "severity", "treatment"}),
    "waiver": frozenset({"standard_or_control", "scope", "approver", "reason", "compensating_control"}),
    "technical_debt": frozenset({"category", "remediation", "effort", "interest_or_impact"}),
    "architecture_backlog": frozenset({"pending_correction"}),
}
BACKLOG_ORIGIN_STATUSES: Final[dict[str, frozenset[str]]] = {
    "adr": frozenset({"proposed", "accepted"}),
    "waiver": frozenset({"approved"}),
    "risk": frozenset({"accepted"}),
    "technical_debt": frozenset({"identified", "accepted", "planned", "in_progress"}),
}
OPTIONAL_ATTRIBUTES: Final[dict[str, frozenset[str]]] = {
    "adr": frozenset(),
    "risk": frozenset({"mitigation"}),
    "waiver": frozenset({"expiry_date"}),
    "technical_debt": frozenset({"resolution_note"}),
    "architecture_backlog": frozenset({"originating_governance_record_id", "manual_origin", "completion_note"}),
}
TRANSITIONS: Final[dict[str, dict[str, frozenset[str]]]] = {
    "adr": {
        "proposed": frozenset({"accepted", "rejected"}),
        "accepted": frozenset({"superseded", "deprecated"}),
        "superseded": frozenset(),
        "deprecated": frozenset(),
        "rejected": frozenset(),
    },
    "risk": {
        "identified": frozenset({"mitigating", "accepted", "closed"}),
        "mitigating": frozenset({"accepted", "closed"}),
        "accepted": frozenset({"mitigating", "closed"}),
        "closed": frozenset(),
    },
    "waiver": {
        "proposed": frozenset({"approved", "rejected"}),
        "approved": frozenset({"expired", "revoked"}),
        "expired": frozenset(),
        "revoked": frozenset(),
        "rejected": frozenset(),
    },
    "technical_debt": {
        "identified": frozenset({"accepted", "planned"}),
        "accepted": frozenset({"planned", "resolved"}),
        "planned": frozenset({"in_progress", "resolved"}),
        "in_progress": frozenset({"resolved"}),
        "resolved": frozenset(),
    },
    "architecture_backlog": {
        "open": frozenset({"planned", "cancelled"}),
        "planned": frozenset({"in_progress", "blocked", "cancelled"}),
        "in_progress": frozenset({"blocked", "completed", "planned", "cancelled"}),
        "blocked": frozenset({"planned", "in_progress", "cancelled"}),
        "completed": frozenset(),
        "cancelled": frozenset(),
    },
}


def validate_record_type(record_type: str) -> str:
    if not isinstance(record_type, str) or record_type not in RECORD_TYPES:
        raise GovernanceError(f"unknown governance record type: {record_type}")
    return record_type


def validate_attributes(record_type: str, attributes: dict[str, object], *, target_status: str | None = None) -> None:
    validate_record_type(record_type)
    if not isinstance(attributes, dict):
        raise GovernanceError("governance attributes must be an object")
    if any(not isinstance(key, str) or not key.strip() for key in attributes):
        raise GovernanceError(f"{record_type} attribute keys must be non-empty text")
    allowed = REQUIRED_ATTRIBUTES[record_type] | OPTIONAL_ATTRIBUTES[record_type]
    unknown = sorted(set(attributes) - allowed)
    if unknown:
        raise GovernanceError(f"{record_type} has unsupported fields: {', '.join(unknown)}")
    invalid = sorted(
        key for key, value in attributes.items() if key in allowed and (not isinstance(value, str) or not value.strip())
    )
    if invalid:
        raise GovernanceError(f"{record_type} fields must be non-empty text: {', '.join(invalid)}")
    missing = sorted(key for key in REQUIRED_ATTRIBUTES[record_type] if not _present(attributes.get(key)))
    if record_type == "architecture_backlog":
        has_record_origin = _present(attributes.get("originating_governance_record_id"))
        has_manual_origin = _present(attributes.get("manual_origin"))
        if has_record_origin == has_manual_origin:
            missing.append("exactly one of originating_governance_record_id or manual_origin")
    if record_type == "waiver" and not _present(attributes.get("expiry_date")):
        missing.append("expiry_date")
    if missing:
        raise GovernanceError(f"{record_type} is missing required fields: {', '.join(missing)}")
    if record_type == "waiver":
        try:
            date.fromisoformat(str(attributes["expiry_date"]))
        except (TypeError, ValueError) as exc:
            raise GovernanceError("waiver expiry_date must use ISO date format YYYY-MM-DD") from exc
    if (
        target_status == "completed"
        and record_type == "architecture_backlog"
        and not _present(attributes.get("completion_note"))
    ):
        raise GovernanceError("architecture_backlog requires completion_note before completion")
    if (
        target_status == "resolved"
        and record_type == "technical_debt"
        and not _present(attributes.get("resolution_note"))
    ):
        raise GovernanceError("technical_debt requires resolution_note before resolution")


def validate_record_fields(
    record_type: str,
    attributes: dict[str, object],
    *,
    priority: object,
    target_date: object,
    target_status: str | None = None,
) -> None:
    """Validate JSON attributes and type-specific scalar fields together."""

    validate_attributes(record_type, attributes, target_status=target_status)
    if record_type == "architecture_backlog":
        if not isinstance(priority, str) or not priority.strip():
            raise GovernanceError("architecture_backlog requires priority")
        if not isinstance(target_date, date) or isinstance(target_date, datetime):
            raise GovernanceError("architecture_backlog requires target_date")


def validate_dates(
    *, effective_date: date | None, review_date: date | None, expiry_date: date | None, target_date: date | None
) -> None:
    for field_name, value in (
        ("effective_date", effective_date),
        ("review_date", review_date),
        ("expiry_date", expiry_date),
        ("target_date", target_date),
    ):
        if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise GovernanceError(f"{field_name} must be a date")
    if review_date and effective_date and review_date < effective_date:
        raise GovernanceError("review_date cannot precede effective_date")
    if expiry_date and effective_date and expiry_date < effective_date:
        raise GovernanceError("expiry_date cannot precede effective_date")
    if target_date and effective_date and target_date < effective_date:
        raise GovernanceError("target_date cannot precede effective_date")


def validate_transition(
    record_type: str, current_status: str, target_status: str, attributes: dict[str, object]
) -> None:
    validate_record_type(record_type)
    if target_status not in LIFECYCLE_STATES[record_type]:
        raise GovernanceError(f"invalid {record_type} lifecycle state: {target_status}")
    if target_status not in TRANSITIONS[record_type].get(current_status, frozenset()):
        raise GovernanceError(f"{record_type} cannot transition from {current_status} to {target_status}")
    validate_attributes(record_type, attributes, target_status=target_status)


def _present(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))
