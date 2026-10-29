"""Record authored domain charters."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_domain_charters"
down_revision: str | Sequence[str] | None = "0014_graph_insight_dismissals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "domain_charters"
_INDEXES = (
    ("ix_domain_charters_domain_id", ["domain_id"], False),
    ("ix_domain_charters_domain_status", ["domain_id", "status"], False),
    ("uq_domain_charters_domain_version", ["domain_id", "version"], True),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE_NAME not in inspector.get_table_names():
        op.create_table(
            _TABLE_NAME,
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("domain_id", sa.Text(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("author_principal_id", sa.Text(), nullable=False),
            sa.Column("purpose", sa.Text(), nullable=False),
            sa.Column("scope_inclusions", sa.Text(), nullable=False),
            sa.Column("scope_exclusions", sa.Text(), nullable=False),
            sa.Column("terminology", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["domain_id"], ["domains.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = {item["name"] for item in sa.inspect(bind).get_indexes(_TABLE_NAME)}
    for name, columns, unique in _INDEXES:
        if name in existing_indexes:
            continue
        op.create_index(name, _TABLE_NAME, columns, unique=unique)


def downgrade() -> None:
    raise RuntimeError("0015_domain_charters is forward-only for the clean target schema")
