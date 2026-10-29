"""Add versioned Idea Block semantic keys and evidence identity constraints."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

workspace_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(workspace_root / "packages"))
sys.path.insert(0, str(workspace_root / "packages" / "margin_db"))
sys.path.insert(0, str(workspace_root / "packages" / "margin_schema"))

from margin_db.semantic_resolution import SEMANTIC_KEY_VERSION, canonical_semantic_key  # noqa: E402

revision: str = "0006_semantic_keys"
down_revision: str | Sequence[str] | None = "0005_embedding_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    revision_columns = {column["name"] for column in inspector.get_columns("idea_block_revisions")}
    if "semantic_key" not in revision_columns:
        op.add_column("idea_block_revisions", sa.Column("semantic_key", sa.Text(), nullable=True))
    if "semantic_key_version" not in revision_columns:
        op.add_column(
            "idea_block_revisions",
            sa.Column(
                "semantic_key_version", sa.Text(), nullable=False, server_default=sa.text(f"'{SEMANTIC_KEY_VERSION}'")
            ),
        )

    _populate_semantic_keys(bind)
    _assert_semantic_keys(bind)
    if bind.dialect.name == "postgresql":
        op.alter_column("idea_block_revisions", "semantic_key", nullable=False)
    else:
        with op.batch_alter_table("idea_block_revisions", recreate="always") as batch:
            batch.alter_column("semantic_key", existing_type=sa.Text(), nullable=False)
    _create_semantic_key_index(bind)
    _create_evidence_identity_index(bind)
    _extend_model_purpose_check(bind)


def downgrade() -> None:
    raise RuntimeError("0006_semantic_keys is forward-only for the greenfield schema")


def _populate_semantic_keys(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT revision.id, revision.idea_block_id, block.domain_id, "
            "revision.normalized_statement, revision.type, revision.polarity, "
            "revision.qualifiers, revision.valid_from, revision.valid_to "
            "FROM idea_block_revisions revision "
            "JOIN idea_blocks block ON block.id = revision.idea_block_id"
        )
    ).mappings()
    entity_query = sa.text(
        "SELECT entity_id FROM idea_block_entities " "WHERE idea_block_id = :idea_block_id ORDER BY entity_id"
    )
    for row in rows:
        qualifiers = row["qualifiers"]
        if isinstance(qualifiers, str):
            qualifiers = json.loads(qualifiers) if qualifiers else {}
        entity_ids = bind.execute(entity_query, {"idea_block_id": row["idea_block_id"]}).scalars().all()
        key = canonical_semantic_key(
            domain_id=row["domain_id"],
            normalized_statement=row["normalized_statement"],
            type=row["type"],
            polarity=row["polarity"],
            qualifiers=qualifiers or {},
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            resolved_entity_ids=entity_ids,
        )
        bind.execute(
            sa.text(
                "UPDATE idea_block_revisions "
                "SET semantic_key = :semantic_key, semantic_key_version = :semantic_key_version "
                "WHERE id = :revision_id"
            ),
            {"semantic_key": key, "semantic_key_version": SEMANTIC_KEY_VERSION, "revision_id": row["id"]},
        )


def _assert_semantic_keys(bind: sa.Connection) -> None:
    missing = bind.execute(
        sa.text(
            "SELECT count(*) FROM idea_block_revisions "
            "WHERE semantic_key IS NULL OR semantic_key = '' "
            "OR semantic_key_version IS NULL OR semantic_key_version = ''"
        )
    ).scalar_one()
    if missing:
        raise RuntimeError(f"{missing} Idea Block revisions are missing semantic keys")


def _create_semantic_key_index(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_idea_block_revisions_verified_semantic_key "
            "ON idea_block_revisions (semantic_key_version, semantic_key) "
            "WHERE publication_status = 'verified' AND semantic_key IS NOT NULL"
        )
    )


def _create_evidence_identity_index(bind: sa.Connection) -> None:
    bind.execute(sa.text("DROP INDEX IF EXISTS uq_claim_evidence_exact_identity"))
    bind.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_claim_evidence_document_location_polarity_run "
            "ON claim_evidence (idea_block_revision_id, source_element_id, "
            "COALESCE(quote_char_start, -1), COALESCE(quote_char_end, -1), polarity, "
            "COALESCE(extraction_run_id, '')) WHERE source_element_id IS NOT NULL"
        )
    )


def _extend_model_purpose_check(bind: sa.Connection) -> None:
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text("ALTER TABLE model_configurations DROP CONSTRAINT IF EXISTS ck_model_configurations_purpose"))
    bind.execute(
        sa.text(
            "ALTER TABLE model_configurations ADD CONSTRAINT ck_model_configurations_purpose "
            "CHECK (purpose in ('extraction', 'vision_ocr', 'answer_synthesis', 'semantic_resolution'))"
        )
    )
