"""Persist export bundles."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_export_bundles"
down_revision: str | Sequence[str] | None = "0016_personal_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "export_bundles"
_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ix_export_bundles_domain_id", ("domain_id",)),
    ("ix_export_bundles_requested_by", ("requested_by",)),
    ("ix_export_bundles_state", ("state",)),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE_NAME not in inspector.get_table_names():
        op.create_table(
            _TABLE_NAME,
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("domain_id", sa.Text(), nullable=False),
            sa.Column("requested_by", sa.Text(), nullable=False),
            sa.Column("state", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.Text(), nullable=True),
            sa.Column("manifest", sa.JSON(), nullable=False),
            sa.Column("records", sa.JSON(), nullable=False),
            sa.Column("scope", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = {item["name"] for item in sa.inspect(bind).get_indexes(_TABLE_NAME)}
    for name, columns in _INDEXES:
        if name in existing_indexes:
            continue
        op.create_index(name, _TABLE_NAME, list(columns))


def downgrade() -> None:
    raise RuntimeError("0017_export_bundles is forward-only for the clean target schema")
