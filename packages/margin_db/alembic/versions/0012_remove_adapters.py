"""Remove external-adapter persistence after local parsing cutover."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_remove_adapters"
down_revision: str | Sequence[str] | None = "0011_remove_compiled_git"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REMOVED_TABLES = ("adapter_runs", "adapter_contracts", "third_party_provenance")


def upgrade() -> None:
    bind = op.get_bind()
    table_names = set(sa.inspect(bind).get_table_names())
    for table_name in _REMOVED_TABLES:
        if table_name in table_names:
            op.drop_table(table_name)
    remaining = sorted(set(sa.inspect(bind).get_table_names()).intersection(_REMOVED_TABLES))
    if remaining:
        raise RuntimeError("0012_remove_adapters left tables: " + ", ".join(remaining))


def downgrade() -> None:
    raise RuntimeError("0012_remove_adapters is forward-only for the clean target schema")
