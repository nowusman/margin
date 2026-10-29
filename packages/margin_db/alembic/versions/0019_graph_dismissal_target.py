"""Allow a knowledge gap to be dismissed as well as a connection."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_graph_dismissal_target"
down_revision: str | Sequence[str] | None = "0018_agent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "graph_insight_dismissals"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE_NAME not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns(_TABLE_NAME)}
    if "target_type" in columns:
        return
    op.add_column(_TABLE_NAME, sa.Column("target_type", sa.Text(), nullable=False, server_default="connection"))
    op.create_index("ix_graph_insight_dismissals_owner_target", _TABLE_NAME, ["principal_id", "target_type"])


def downgrade() -> None:
    raise RuntimeError("0019_graph_dismissal_target is forward-only for the clean target schema")
