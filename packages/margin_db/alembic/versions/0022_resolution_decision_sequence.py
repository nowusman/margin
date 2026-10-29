"""Give the resolution history of a mention a deterministic order.

Decisions for one mention were previously ordered by their creation timestamp,
which is not unique: two decisions can land in the same clock tick, and the
identifier that broke the tie was random. The current decision could therefore
be picked at random from the history. A per-mention sequence makes the order
of the history explicit.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_resolution_sequence"
down_revision: str | Sequence[str] | None = "0021_domain_context_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "entity_resolution_decisions"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE_NAME not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns(_TABLE_NAME)}
    if "sequence" in columns:
        return
    op.add_column(_TABLE_NAME, sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"))
    op.execute(
        f"UPDATE {_TABLE_NAME} SET sequence = ("
        "  SELECT COUNT(*) FROM entity_resolution_decisions earlier"
        "  WHERE earlier.mention_id = entity_resolution_decisions.mention_id"
        "    AND ("
        "      earlier.created_at < entity_resolution_decisions.created_at"
        "      OR (earlier.created_at = entity_resolution_decisions.created_at"
        "          AND earlier.id <= entity_resolution_decisions.id)"
        "    )"
        ")"
    )
    op.create_index("ix_entity_resolution_decisions_mention_sequence", _TABLE_NAME, ["mention_id", "sequence"])


def downgrade() -> None:
    raise RuntimeError("0022_resolution_decision_sequence is forward-only for the clean target schema")
