"""Create the complete greenfield Margin schema.

The application has no deployed legacy databases, so the schema is created in
one revision from the authoritative SQLAlchemy metadata.  PostgreSQL-only
extensions, indexes, and audit triggers are added after the tables exist.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from alembic import op
from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from margin_db import audit_models as _audit_models  # noqa: E402,F401
from margin_db.base import Base  # noqa: E402

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    Base.metadata.create_all(bind=bind)
    _create_operational_indexes()

    if bind.dialect.name == "postgresql":
        _create_retrieval_indexes()
        _create_audit_function()
        _create_audit_triggers()
        _create_audit_events_guard()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table_name, _ in _AUDIT_TABLES:
            op.execute(text(f"DROP TRIGGER IF EXISTS audit_{table_name} ON {table_name}"))
        op.execute(text("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events"))
        op.execute(text("DROP FUNCTION IF EXISTS margin_audit_events_immutable()"))
        op.execute(text("DROP FUNCTION IF EXISTS margin_audit_row_change()"))

    Base.metadata.drop_all(bind=bind)


def _create_operational_indexes() -> None:
    indexes = (
        ("ix_conversion_quality_reports_source_document_id", "conversion_quality_reports", ("source_document_id",)),
        ("ix_embedding_records_target_type", "embedding_records", ("target_type",)),
        ("ix_embedding_records_model_id", "embedding_records", ("model_id",)),
        ("ix_embedding_records_index_version", "embedding_records", ("index_version",)),
        (
            "ix_embedding_records_model_index_target",
            "embedding_records",
            ("model_id", "index_version", "target_type", "target_id"),
        ),
        ("ix_ingestion_jobs_status", "ingestion_jobs", ("status",)),
        ("ix_ingestion_jobs_source_document_id", "ingestion_jobs", ("source_document_id",)),
        ("ix_ingestion_jobs_source_version_id", "ingestion_jobs", ("source_version_id",)),
        ("ix_relationship_provenance_source_element_id", "relationship_provenance", ("source_element_id",)),
        ("ix_review_task_comments_review_task_id", "review_task_comments", ("review_task_id",)),
    )
    for index_name, table_name, columns in indexes:
        op.create_index(index_name, table_name, list(columns), if_not_exists=True)


def _create_retrieval_indexes() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_entities_retrieval_fts
        ON entities USING gin (
            to_tsvector('simple', coalesce(preferred_label, '') || ' ' || coalesce(definition, ''))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_source_elements_retrieval_fts
        ON source_elements USING gin (
            to_tsvector(
                'simple',
                coalesce(text, '') || ' ' || coalesce(kind, '')
            )
        )
        """
    )
    _create_trigram_index(
        "ix_entities_retrieval_trgm", "entities", "coalesce(preferred_label, '') || ' ' || coalesce(definition, '')"
    )
    _create_trigram_index(
        "ix_source_elements_retrieval_trgm", "source_elements", "coalesce(text, '') || ' ' || coalesce(kind, '')"
    )


def _create_trigram_index(index_name: str, table_name: str, expression: str) -> None:
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {index_name}
        ON {table_name} USING gin (({expression}) gin_trgm_ops)
        """
    )


def _create_audit_function() -> None:
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION margin_audit_row_change()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                before_row jsonb;
                after_row jsonb;
                audited_id text;
                actor_name text;
                redacted_columns text[] := ARRAY[
                    'attributes', 'snapshot', 'title', 'statement', 'normalized_statement', 'search_text',
                    'text', 'definition', 'preferred_label', 'normalized_label', 'source_quote', 'raw_predicate',
                    'metadata', 'qualifiers', 'provenance', 'dublin_core', 'source_uri', 'object_key',
                    'external_issue_url', 'reason', 'recommendation', 'resolution', 'comment', 'evidence'
                ]::text[];
            BEGIN
                actor_name := coalesce(nullif(current_setting('margin.actor', true), ''), 'database');

                IF TG_OP = 'INSERT' THEN
                    after_row := to_jsonb(NEW) - redacted_columns;
                    audited_id := after_row ->> TG_ARGV[0];
                ELSIF TG_OP = 'UPDATE' THEN
                    before_row := to_jsonb(OLD) - redacted_columns;
                    after_row := to_jsonb(NEW) - redacted_columns;
                    audited_id := after_row ->> TG_ARGV[0];
                    IF to_jsonb(OLD) = to_jsonb(NEW) THEN
                        RETURN NEW;
                    END IF;
                ELSE
                    before_row := to_jsonb(OLD) - redacted_columns;
                    audited_id := before_row ->> TG_ARGV[0];
                END IF;

                INSERT INTO audit_events (
                    id, actor, action, target_type, target_id, before, after, correlation_id, git_commit_sha, created_at
                ) VALUES (
                    gen_random_uuid(), actor_name, lower(TG_OP), TG_TABLE_NAME, audited_id,
                    before_row, after_row, NULL, NULL, now()
                );

                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END;
            $$;
            """
        )
    )


_AUDIT_TABLES: tuple[tuple[str, str], ...] = (
    ("users", "id"),
    ("groups", "id"),
    ("domains", "id"),
    ("source_documents", "id"),
    ("source_document_versions", "id"),
    ("source_pages", "id"),
    ("source_elements", "id"),
    ("source_page_outcomes", "id"),
    ("source_element_outcomes", "id"),
    ("entities", "id"),
    ("idea_blocks", "id"),
    ("entity_relationships", "id"),
    ("governance_records", "id"),
    ("governance_record_revisions", "id"),
    ("governance_record_idea_blocks", "governance_record_id"),
    ("governance_record_entities", "governance_record_id"),
    ("governance_record_relations", "id"),
    ("review_tasks", "id"),
    ("review_task_comments", "id"),
    ("ingestion_jobs", "id"),
    ("conversion_quality_reports", "id"),
)


def _create_audit_triggers() -> None:
    existing_tables = set(inspect(op.get_bind()).get_table_names())
    for table_name, pk_column in _AUDIT_TABLES:
        if table_name not in existing_tables:
            continue
        op.execute(
            text(
                f"""
                CREATE TRIGGER audit_{table_name}
                AFTER INSERT OR UPDATE OR DELETE ON {table_name}
                FOR EACH ROW EXECUTE FUNCTION margin_audit_row_change('{pk_column}')
                """
            )
        )


def _create_audit_events_guard() -> None:
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION margin_audit_events_immutable()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'audit_events is append-only';
            END;
            $$;
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TRIGGER audit_events_immutable
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION margin_audit_events_immutable()
            """
        )
    )
