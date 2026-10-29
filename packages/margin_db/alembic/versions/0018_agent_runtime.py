"""Add the governed agent runtime."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_agent_runtime"
down_revision: str | Sequence[str] | None = "0017_export_bundles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[tuple[str, tuple[tuple[str, str, bool], ...], tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    (
        "agent_runs",
        (
            ("id", "sa.Text()", False),
            ("principal_id", "sa.Text()", False),
            ("domain_id", "sa.Text()", True),
            ("question", "sa.Text()", False),
            ("state", "sa.Text()", False),
            ("iteration_budget", "sa.Integer()", False),
            ("token_budget", "sa.Integer()", False),
            ("iterations_used", "sa.Integer()", False),
            ("tokens_used", "sa.Integer()", False),
            ("write_granted", "sa.Boolean()", False),
            ("answer", "sa.Text()", True),
            ("error", "sa.Text()", True),
            ("metadata", "sa.JSON()", False),
            ("created_at", "sa.DateTime(timezone=True)", False),
            ("started_at", "sa.DateTime(timezone=True)", True),
            ("completed_at", "sa.DateTime(timezone=True)", True),
            ("cancelled_at", "sa.DateTime(timezone=True)", True),
        ),
        (
            ("ix_agent_runs_principal_id", ("principal_id",)),
            ("ix_agent_runs_state", ("state",)),
            ("ix_agent_runs_created_at", ("created_at",)),
        ),
    ),
    (
        "agent_run_events",
        (
            ("id", "sa.Text()", False),
            ("run_id", "sa.Text()", False),
            ("sequence", "sa.Integer()", False),
            ("kind", "sa.Text()", False),
            ("payload", "sa.JSON()", False),
            ("created_at", "sa.DateTime(timezone=True)", False),
        ),
        (("ix_agent_run_events_run_sequence", ("run_id", "sequence")),),
    ),
    (
        "agent_proposals",
        (
            ("id", "sa.Text()", False),
            ("run_id", "sa.Text()", False),
            ("principal_id", "sa.Text()", False),
            ("target_type", "sa.Text()", False),
            ("target_id", "sa.Text()", False),
            ("path", "sa.Text()", False),
            ("previous_content", "sa.Text()", False),
            ("proposed_content", "sa.Text()", False),
            ("diff", "sa.Text()", False),
            ("summary", "sa.Text()", False),
            ("state", "sa.Text()", False),
            ("reviewed_by", "sa.Text()", True),
            ("reviewed_at", "sa.DateTime(timezone=True)", True),
            ("created_at", "sa.DateTime(timezone=True)", False),
        ),
        (
            ("ix_agent_proposals_run_id", ("run_id",)),
            ("ix_agent_proposals_state", ("state",)),
            ("ix_agent_proposals_principal_id", ("principal_id",)),
        ),
    ),
    (
        "agent_human_inputs",
        (
            ("id", "sa.Text()", False),
            ("run_id", "sa.Text()", False),
            ("request_key", "sa.Text()", False),
            ("title", "sa.Text()", False),
            ("description", "sa.Text()", False),
            ("fields", "sa.JSON()", False),
            ("state", "sa.Text()", False),
            ("response", "sa.JSON()", False),
            ("answered_by", "sa.Text()", True),
            ("answered_at", "sa.DateTime(timezone=True)", True),
            ("created_at", "sa.DateTime(timezone=True)", False),
        ),
        (("ix_agent_human_inputs_run_id", ("run_id",)), ("ix_agent_human_inputs_state", ("state",))),
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name, columns, indexes in _TABLES:
        inspector = sa.inspect(bind)
        if table_name not in inspector.get_table_names():
            op.create_table(
                table_name,
                *(sa.Column(name, _column_type(type_sql), nullable=nullable) for name, type_sql, nullable in columns),
                sa.PrimaryKeyConstraint("id"),
            )
        existing_indexes = {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}
        for name, index_columns in indexes:
            if name in existing_indexes:
                continue
            op.create_index(name, table_name, list(index_columns))


def _column_type(type_sql: str) -> sa.types.TypeEngine:
    if type_sql == "sa.Integer()":
        return sa.Integer()
    if type_sql == "sa.Boolean()":
        return sa.Boolean()
    if type_sql == "sa.JSON()":
        return sa.JSON()
    if type_sql == "sa.DateTime(timezone=True)":
        return sa.DateTime(timezone=True)
    return sa.Text()


def downgrade() -> None:
    raise RuntimeError("0018_agent_runtime is forward-only for the clean target schema")
