"""Make IngestionJob the single persisted ingestion lifecycle."""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_unified_job_lifecycle"
down_revision: str | Sequence[str] | None = "0012_remove_adapters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INGESTION_STATUSES = (
    "received",
    "converting",
    "extracting",
    "resolving",
    "embedding",
    "review_required",
    "completed",
    "failed",
    "quarantined",
)
_STATUS_CHECK = "status in (" + ", ".join(f"'{status}'" for status in _INGESTION_STATUSES) + ")"


def upgrade() -> None:
    bind = op.get_bind()
    _require_empty_ingestion_lifecycle(bind)
    _ensure_ingestion_status_constraint(bind)
    _ensure_child_link(
        bind,
        table_name="extraction_runs",
        index_name="ix_extraction_runs_ingestion_job_id",
        foreign_key_name="fk_extraction_runs_ingestion_job_id",
    )
    _ensure_child_link(
        bind,
        table_name="embedding_jobs",
        index_name="ix_embedding_jobs_ingestion_job_id",
        foreign_key_name="fk_embedding_jobs_ingestion_job_id",
    )
    _assert_lifecycle_schema(bind)


def downgrade() -> None:
    raise RuntimeError("0013_unified_job_lifecycle is forward-only for the clean target schema")


def _require_empty_ingestion_lifecycle(bind: sa.Connection) -> None:
    if "ingestion_jobs" not in sa.inspect(bind).get_table_names():
        raise RuntimeError("0013_unified_job_lifecycle requires the ingestion_jobs table")
    job_count = bind.execute(sa.text("SELECT count(*) FROM ingestion_jobs")).scalar_one()
    if job_count:
        raise RuntimeError(
            "0013_unified_job_lifecycle requires an empty target database; "
            "drain or rebuild instead of mapping historical job states"
        )


def _ensure_ingestion_status_constraint(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    checks = {item.get("name"): item.get("sqltext", "") for item in inspector.get_check_constraints("ingestion_jobs")}
    current = checks.get("ck_ingestion_jobs_status", "")
    if _matches_status_check(current):
        return
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE ingestion_jobs DROP CONSTRAINT IF EXISTS ck_ingestion_jobs_status")
        op.execute("ALTER TABLE ingestion_jobs ADD CONSTRAINT ck_ingestion_jobs_status " f"CHECK ({_STATUS_CHECK})")
        op.execute("ALTER TABLE ingestion_jobs ALTER COLUMN status SET DEFAULT 'received'")
        return
    with op.batch_alter_table("ingestion_jobs", recreate="always") as batch:
        if "ck_ingestion_jobs_status" in checks:
            batch.drop_constraint("ck_ingestion_jobs_status", type_="check")
        batch.create_check_constraint("ck_ingestion_jobs_status", _STATUS_CHECK)
        batch.alter_column("status", server_default=sa.text("'received'"))


def _ensure_child_link(bind: sa.Connection, *, table_name: str, index_name: str, foreign_key_name: str) -> None:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        raise RuntimeError(f"0013_unified_job_lifecycle requires {table_name}")
    columns = {item["name"] for item in inspector.get_columns(table_name)}
    foreign_keys = inspector.get_foreign_keys(table_name)
    links_parent = any(
        item.get("referred_table") == "ingestion_jobs" and "ingestion_job_id" in item.get("constrained_columns", ())
        for item in foreign_keys
    )
    indexes = {item["name"] for item in inspector.get_indexes(table_name)}
    if "ingestion_job_id" in columns and links_parent:
        if index_name not in indexes:
            op.create_index(index_name, table_name, ["ingestion_job_id"])
        return
    if bind.dialect.name == "postgresql":
        if "ingestion_job_id" not in columns:
            op.add_column(table_name, sa.Column("ingestion_job_id", sa.Text(), nullable=True))
        if not links_parent:
            op.create_foreign_key(
                foreign_key_name, table_name, "ingestion_jobs", ["ingestion_job_id"], ["id"], ondelete="SET NULL"
            )
        if index_name not in indexes:
            op.create_index(index_name, table_name, ["ingestion_job_id"])
        return
    with op.batch_alter_table(table_name, recreate="always") as batch:
        if "ingestion_job_id" not in columns:
            batch.add_column(sa.Column("ingestion_job_id", sa.Text(), nullable=True))
        if not links_parent:
            batch.create_foreign_key(
                foreign_key_name, "ingestion_jobs", ["ingestion_job_id"], ["id"], ondelete="SET NULL"
            )
        if index_name not in indexes:
            batch.create_index(index_name, ["ingestion_job_id"])


def _assert_lifecycle_schema(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    status_checks = {
        item.get("name"): item.get("sqltext", "") for item in inspector.get_check_constraints("ingestion_jobs")
    }
    if not _matches_status_check(status_checks.get("ck_ingestion_jobs_status", "")):
        raise RuntimeError("0013_unified_job_lifecycle did not enforce the parent job status constraint")
    for table_name in ("extraction_runs", "embedding_jobs"):
        columns = {item["name"] for item in inspector.get_columns(table_name)}
        if "ingestion_job_id" not in columns:
            raise RuntimeError(f"0013_unified_job_lifecycle did not link {table_name}")


def _normalized(value: str) -> str:
    return "".join(value.casefold().split())


def _matches_status_check(sqltext: str) -> bool:
    normalized = _normalized(sqltext)
    if normalized == _normalized(_STATUS_CHECK):
        return True
    values = {value.casefold() for value in re.findall(r"'([^']+)'", sqltext)}
    return values == set(_INGESTION_STATUSES) and ("statusin(" in normalized or "status=any(array[" in normalized)
