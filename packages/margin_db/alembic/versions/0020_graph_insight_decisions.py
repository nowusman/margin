"""Let a reader defer a knowledge gap as well as dismiss it.

A dismissal and a deferral are the same kind of decision about the same kind of
target, so they share one table. The table is renamed rather than duplicated:
one decision per reader and per target keeps the reader's intent legible, and
the previous table carried a column name that no longer described its rows.

The initial migration builds the schema from the current models, so on a fresh
database the decisions table already exists and only the previous table has to
be carried over and dropped.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_graph_insight_decisions"
down_revision: str | Sequence[str] | None = "0019_graph_dismissal_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TABLE = "graph_insight_dismissals"
_NEW_TABLE = "graph_insight_decisions"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if _NEW_TABLE not in table_names:
        op.create_table(
            _NEW_TABLE,
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("principal_id", sa.Text(), nullable=False),
            sa.Column("target_type", sa.Text(), nullable=False, server_default="connection"),
            sa.Column("target_id", sa.Text(), nullable=False),
            sa.Column("decision", sa.Text(), nullable=False, server_default="dismiss"),
            sa.Column("deferred_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_graph_insight_decisions_principal_id", _NEW_TABLE, ["principal_id"])
        op.create_index("ix_graph_insight_decisions_target_id", _NEW_TABLE, ["target_id"])
        op.create_index(
            "uq_graph_insight_decisions_principal_target",
            _NEW_TABLE,
            ["principal_id", "target_type", "target_id"],
            unique=True,
        )

    if _OLD_TABLE in table_names:
        op.execute(
            f"INSERT INTO {_NEW_TABLE} "
            "(id, principal_id, target_type, target_id, decision, deferred_until, reason, created_at) "
            f"SELECT id, principal_id, target_type, edge_id, 'dismiss', NULL, reason, created_at "
            f"FROM {_OLD_TABLE} "
            f"WHERE id NOT IN (SELECT id FROM {_NEW_TABLE})"
        )
        op.drop_table(_OLD_TABLE)


def downgrade() -> None:
    raise RuntimeError("0020_graph_insight_decisions is forward-only for the clean target schema")
