"""Let a domain configure the context budget it is read under.

The budget is global by default, and a domain may set its own because a large
operational domain and a small reference domain need different amounts of
surrounding context for the same question.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_domain_context_budget"
down_revision: str | Sequence[str] | None = "0020_graph_insight_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "domains"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE_NAME not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns(_TABLE_NAME)}
    if "context_budget_chars" in columns:
        return
    op.add_column(_TABLE_NAME, sa.Column("context_budget_chars", sa.Integer(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("0021_domain_context_budget is forward-only for the clean target schema")
