"""Update model_configurations table for capabilities, secret_reference, and new purposes."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from margin_db.base import JsonDict

revision: str = "0004_model_catalog_capabilities"
down_revision: str | Sequence[str] | None = "0003_idea_block_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "display_name",
        "purpose",
        "runtime",
        "model_name",
        "endpoint_url",
        "secret_reference",
        "capabilities",
        "context_length",
        "max_tokens",
        "temperature_default",
        "license",
        "hardware_profile",
        "default_scopes",
        "system_prompt",
        "status",
        "last_test_status",
        "last_test_latency_ms",
        "last_test_detail",
        "last_tested_at",
        "created_at",
        "updated_at",
    }
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "model_configurations" not in tables:
        raise RuntimeError("model_configurations table does not exist")

    columns = {column["name"] for column in inspector.get_columns("model_configurations")}

    if "api_key" in columns:
        op.drop_column("model_configurations", "api_key")

    if "secret_reference" not in columns:
        op.add_column("model_configurations", sa.Column("secret_reference", sa.Text(), nullable=True))

    if "capabilities" not in columns:
        op.add_column("model_configurations", sa.Column("capabilities", JsonDict, nullable=False, server_default="{}"))

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE model_configurations DROP CONSTRAINT IF EXISTS ck_model_configurations_purpose")
        op.execute(
            "ALTER TABLE model_configurations ADD CONSTRAINT ck_model_configurations_purpose "
            "CHECK (purpose in ('extraction', 'vision_ocr', 'answer_synthesis', 'semantic_resolution'))"
        )

    # Re-inspect to verify required schema contract
    inspector = sa.inspect(bind)
    updated_columns = {column["name"] for column in inspector.get_columns("model_configurations")}

    if "api_key" in updated_columns:
        raise RuntimeError("model_configurations still contains api_key")

    missing = _REQUIRED_COLUMNS - updated_columns
    if missing:
        raise RuntimeError(f"model_configurations is missing required columns: {sorted(missing)}")


def downgrade() -> None:
    raise RuntimeError("0004_model_catalog_capabilities is forward-only for the greenfield schema")
