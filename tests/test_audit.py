from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import create_app
from margin_db.audit import audit_report_query, write_audit_event
from margin_db.audit_models import AuditEvent
from margin_db.base import Base
from margin_db.user_models import Domain, DomainPermission


def test_audit_writer_creates_audit_event_with_json_payloads() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        event = write_audit_event(
            session,
            actor="test-user",
            action="create",
            target_type="entity",
            target_id="ent_customer_platform",
            before=None,
            after={"name": "Customer Platform", "confidence": "verified"},
            git_commit_sha="abc123",
        )
        event_id = event.id
        session.commit()

    with Session(engine) as session:
        persisted = session.scalar(select(AuditEvent).where(AuditEvent.id == event_id))

    assert persisted is not None
    assert persisted.actor == "test-user"
    assert persisted.action == "create"
    assert persisted.target_id == "ent_customer_platform"
    assert persisted.after == {"name": "Customer Platform", "confidence": "verified"}
    assert persisted.git_commit_sha == "abc123"


def test_audit_writer_redacts_restricted_nested_payloads() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        event = write_audit_event(
            session,
            actor="test-user",
            action="update",
            target_type="governance_record_relation",
            target_id="relation-1",
            before={"provenance": {"reason": "Secret source text"}, "resolution": "Restricted note"},
            after={"title": "Restricted title", "reason": "Keep the audit reason"},
        )
        event_id = event.id
        session.commit()

    with Session(engine) as session:
        persisted = session.scalar(select(AuditEvent).where(AuditEvent.id == event_id))

    assert persisted is not None
    assert persisted.before == {"provenance": "[REDACTED]", "resolution": "[REDACTED]"}
    assert persisted.after == {"title": "[REDACTED]", "reason": "Keep the audit reason"}


def test_initial_migration_defines_row_change_audit_triggers() -> None:
    migration = Path("packages/margin_db/alembic/versions/0001_initial_schema.py").read_text(encoding="utf-8")

    assert "INSERT INTO audit_events" in migration
    assert "AFTER INSERT OR UPDATE OR DELETE" in migration
    assert "audit_events_immutable" in migration
    assert "'source_quote'" in migration
    assert "'attributes'" in migration
    assert "'title'" in migration
    assert "'external_issue_url'" in migration
    assert "'resolution'" in migration
    assert "to_jsonb(OLD) - redacted_columns" in migration
    assert "to_jsonb(NEW) - redacted_columns" in migration
    assert "IF to_jsonb(OLD) = to_jsonb(NEW)" in migration
    assert "::text[]" in migration
    for table_name in (
        "domains",
        "source_documents",
        "entities",
        "idea_blocks",
        "entity_relationships",
        "governance_record_idea_blocks",
        "governance_record_entities",
    ):
        column = "governance_record_id" if table_name.startswith("governance_record_") else "id"
        assert f'("{table_name}", "{column}")' in migration


def test_audit_report_query_filters_by_actor() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        write_audit_event(session, actor="alice", action="create", target_type="entity", target_id="ent_1")
        write_audit_event(session, actor="bob", action="update", target_type="entity", target_id="ent_2")
        session.commit()

    with Session(engine) as session:
        rows = session.scalars(audit_report_query(actor="alice")).all()

    assert len(rows) == 1
    assert rows[0].actor == "alice"


def test_audit_report_api_requires_review_permission() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        session.add(Domain(id="OSS", name="OSS"))
        session.add(DomainPermission(domain_id="OSS", principal_type="user", principal_id="alice", permission="review"))
        write_audit_event(session, actor="alice", action="update", target_type="entity", target_id="ent_1")
        session.commit()

    app = create_app()
    app.state.session_factory = session_factory
    client = TestClient(app)

    denied = client.get("/api/admin/audit-events", headers={"x-margin-user": "bob"})
    assert denied.status_code == 403

    allowed = client.get(
        "/api/admin/audit-events",
        params={"actor": "alice"},
        headers={"x-margin-user": "alice", "x-margin-groups": "margin-admin"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["audit_events"][0]["target_id"] == "ent_1"
