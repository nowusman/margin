from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from margin_db.audit_models import AuditEvent

_REDACTED = "[REDACTED]"
_SENSITIVE_AUDIT_KEYS = frozenset(
    {
        "api_key",
        "attributes",
        "authorization",
        "comment",
        "credential",
        "decision_provenance",
        "definition",
        "dublin_core",
        "evidence",
        "external_issue_url",
        "metadata",
        "normalized_label",
        "normalized_statement",
        "object_key",
        "password",
        "preferred_label",
        "provenance",
        "qualifiers",
        "raw_evidence",
        "raw_predicate",
        "resolution",
        "restricted_evidence",
        "restricted_text",
        "search_text",
        "secret",
        "snapshot",
        "source_content",
        "source_quote",
        "source_uri",
        "statement",
        "text",
        "title",
        "token",
    }
)


def redact_audit_payload(value: object) -> object:
    """Remove user-supplied or restricted content from audit metadata."""

    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().casefold().replace("-", "_")
            redacted[str(key)] = _REDACTED if normalized_key in _SENSITIVE_AUDIT_KEYS else redact_audit_payload(item)
        return redacted
    if isinstance(value, list | tuple):
        return [redact_audit_payload(item) for item in value]
    return value


def _redacted_mapping(value: Mapping[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    payload = redact_audit_payload(dict(value))
    if not isinstance(payload, dict):
        raise TypeError("audit payload must be an object")
    return payload


def write_audit_event(
    session: Session,
    *,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    before: Mapping[str, object] | None = None,
    after: Mapping[str, object] | None = None,
    correlation_id: str | None = None,
    git_commit_sha: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=_redacted_mapping(before),
        after=_redacted_mapping(after),
        correlation_id=correlation_id,
        git_commit_sha=git_commit_sha,
    )
    session.add(event)
    session.flush()
    return event


def audit_report_query(
    *,
    actor: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Select[tuple[AuditEvent]]:
    query = select(AuditEvent).order_by(AuditEvent.created_at.desc())
    if actor:
        query = query.where(AuditEvent.actor == actor)
    if target_type:
        query = query.where(AuditEvent.target_type == target_type)
    if target_id:
        query = query.where(AuditEvent.target_id == target_id)
    if date_from:
        query = query.where(AuditEvent.created_at >= date_from)
    if date_to:
        query = query.where(AuditEvent.created_at <= date_to)
    return query
