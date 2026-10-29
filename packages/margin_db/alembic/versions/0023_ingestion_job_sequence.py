"""Give the ingestion history of a source document a deterministic order.

Jobs for one document were previously ordered by their creation timestamp,
which is not unique: the platform clock only advances every few milliseconds,
so two jobs can share a timestamp and the document's latest job became
ambiguous. A per-document sequence makes the order of the history explicit.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_ingestion_job_sequence"
down_revision: str | Sequence[str] | None = "0022_resolution_sequence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "ingestion_jobs"


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
        "  SELECT COUNT(*) FROM ingestion_jobs earlier"
        "  WHERE earlier.source_document_id = ingestion_jobs.source_document_id"
        "    AND ("
        "      earlier.created_at < ingestion_jobs.created_at"
        "      OR (earlier.created_at = ingestion_jobs.created_at"
        "          AND earlier.id <= ingestion_jobs.id)"
        "    )"
        ")"
    )
    op.create_index("ix_ingestion_jobs_source_sequence", _TABLE_NAME, ["source_document_id", "sequence"])


def downgrade() -> None:
    raise RuntimeError("0023_ingestion_job_sequence is forward-only for the clean target schema")
