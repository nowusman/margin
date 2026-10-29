"""Consolidate embedding targets and enforce the approved vector contract."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_embedding_targets"
down_revision: str | Sequence[str] | None = "0004_model_catalog_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIMENSIONS = 384
_TARGET_TYPES = "('source_element', 'idea_block_revision')"


def upgrade() -> None:
    bind = op.get_bind()
    _assert_target_rows_are_migratable(bind)
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE embedding_records ALTER COLUMN embedding TYPE vector(384) " "USING embedding::vector(384)"
        )
        op.execute("ALTER TABLE embedding_records DROP CONSTRAINT IF EXISTS ck_embedding_records_target_type")
        op.execute("ALTER TABLE embedding_records DROP CONSTRAINT IF EXISTS ck_embedding_records_dimensions")
        op.execute(
            "ALTER TABLE embedding_records ADD CONSTRAINT ck_embedding_records_target_type "
            f"CHECK (target_type in {_TARGET_TYPES})"
        )
        op.execute(
            "ALTER TABLE embedding_records ADD CONSTRAINT ck_embedding_records_dimensions "
            f"CHECK (dimensions = {_DIMENSIONS})"
        )
        op.execute("ALTER TABLE embedding_jobs DROP CONSTRAINT IF EXISTS ck_embedding_jobs_target_type")
        op.execute(
            "ALTER TABLE embedding_jobs ADD CONSTRAINT ck_embedding_jobs_target_type "
            f"CHECK (target_type in {_TARGET_TYPES})"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_embedding_records_idea_block_revision_hnsw "
            "ON embedding_records USING hnsw (embedding vector_cosine_ops) "
            "WHERE target_type = 'idea_block_revision'"
        )
        return

    _replace_sqlite_checks(
        "embedding_records",
        {
            "ck_embedding_records_target_type": f"target_type in {_TARGET_TYPES}",
            "ck_embedding_records_dimensions": f"dimensions = {_DIMENSIONS}",
        },
    )
    _replace_sqlite_checks("embedding_jobs", {"ck_embedding_jobs_target_type": f"target_type in {_TARGET_TYPES}"})


def downgrade() -> None:
    raise RuntimeError("0005_embedding_targets is forward-only for the greenfield schema")


def _assert_target_rows_are_migratable(bind: sa.Connection) -> None:
    invalid_records = bind.execute(
        sa.text(
            "SELECT count(*) FROM embedding_records "
            "WHERE target_type NOT IN ('source_element', 'idea_block_revision') "
            "OR dimensions <> :dimensions"
        ),
        {"dimensions": _DIMENSIONS},
    ).scalar_one()
    invalid_jobs = bind.execute(
        sa.text(
            "SELECT count(*) FROM embedding_jobs WHERE target_type NOT IN ('source_element', 'idea_block_revision')"
        )
    ).scalar_one()
    if invalid_records or invalid_jobs:
        raise RuntimeError(
            "0005_embedding_targets found unsupported embedding rows; "
            "rebuild the clean target database instead of selecting survivors"
        )


def _replace_sqlite_checks(table_name: str, checks: dict[str, str]) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_check_constraints(table_name) if item.get("name")}
    with op.batch_alter_table(table_name, recreate="always") as batch:
        for name in checks:
            if name in existing:
                batch.drop_constraint(name, type_="check")
        for name, condition in checks.items():
            batch.create_check_constraint(name, condition)
