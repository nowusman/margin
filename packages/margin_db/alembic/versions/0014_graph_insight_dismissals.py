"""Record reader dismissals of surfaced graph connections."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_graph_insight_dismissals"
down_revision: str | Sequence[str] | None = "0013_unified_job_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "graph_insight_dismissals"
_INDEXES = (
    "ix_graph_insight_dismissals_principal_id",
    "ix_graph_insight_dismissals_edge_id",
    "uq_graph_insight_dismissals_principal_edge",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE_NAME not in inspector.get_table_names():
        op.create_table(
            _TABLE_NAME,
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("principal_id", sa.Text(), nullable=False),
            sa.Column("edge_id", sa.Text(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = {item["name"] for item in sa.inspect(bind).get_indexes(_TABLE_NAME)}
    if "ix_graph_insight_dismissals_principal_id" not in existing_indexes:
        op.create_index("ix_graph_insight_dismissals_principal_id", _TABLE_NAME, ["principal_id"])
    if "ix_graph_insight_dismissals_edge_id" not in existing_indexes:
        op.create_index("ix_graph_insight_dismissals_edge_id", _TABLE_NAME, ["edge_id"])
    if "uq_graph_insight_dismissals_principal_edge" not in existing_indexes:
        op.create_index(
            "uq_graph_insight_dismissals_principal_edge", _TABLE_NAME, ["principal_id", "edge_id"], unique=True
        )


def downgrade() -> None:
    raise RuntimeError("0014_graph_insight_dismissals is forward-only for the clean target schema")
