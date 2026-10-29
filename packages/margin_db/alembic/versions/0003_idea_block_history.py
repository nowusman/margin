"""Validate the stable Idea Block and immutable revision contract."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_idea_block_history"
down_revision: str | Sequence[str] | None = "0002_canonical_source_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "idea_blocks": frozenset(
        {"id", "domain_id", "current_revision_id", "merged_into_id", "tombstoned_at", "archived_at"}
    ),
    "idea_block_revisions": frozenset(
        {
            "id",
            "idea_block_id",
            "revision_number",
            "statement",
            "normalized_statement",
            "type",
            "polarity",
            "qualifiers",
            "valid_from",
            "valid_to",
            "publication_status",
            "ontology_version",
            "actor",
            "process",
            "change_reason",
            "created_at",
        }
    ),
    "idea_block_relations": frozenset(
        {
            "id",
            "source_idea_block_id",
            "target_idea_block_id",
            "relation_type",
            "direction",
            "status",
            "valid_from",
            "valid_to",
            "confidence",
            "decision_provenance",
        }
    ),
}
_REMOVED_PARENT_COLUMNS = frozenset(
    {
        "statement",
        "normalized_statement",
        "type",
        "confidence",
        "status",
        "extraction_method",
        "polarity",
        "qualifiers",
        "valid_from",
        "valid_to",
        "metadata",
    }
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    missing_tables = set(_REQUIRED_COLUMNS) - tables
    if missing_tables:
        raise RuntimeError(f"clean schema is missing Idea Block tables: {sorted(missing_tables)}")

    for table_name, required_columns in _REQUIRED_COLUMNS.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing_columns = required_columns - columns
        if missing_columns:
            raise RuntimeError(f"{table_name} is missing immutable-revision columns: {sorted(missing_columns)}")

    parent_columns = {column["name"] for column in inspector.get_columns("idea_blocks")}
    retained_parent_columns = parent_columns & _REMOVED_PARENT_COLUMNS
    if retained_parent_columns:
        raise RuntimeError(f"idea_blocks still stores revision semantics: {sorted(retained_parent_columns)}")

    _assert_current_revisions(bind)
    _assert_revision_links(bind)
    _assert_embedding_targets(bind, tables)


def downgrade() -> None:
    raise RuntimeError("0003_idea_block_history is forward-only for the greenfield schema")


def _assert_current_revisions(bind: sa.Connection) -> None:
    missing = bind.execute(
        sa.text(
            "SELECT count(*) FROM idea_blocks block "
            "WHERE block.current_revision_id IS NULL "
            "OR NOT EXISTS ("
            "SELECT 1 FROM idea_block_revisions revision "
            "WHERE revision.id = block.current_revision_id "
            "AND revision.idea_block_id = block.id"
            ")"
        )
    ).scalar_one()
    if missing:
        raise RuntimeError(f"{missing} Idea Blocks do not point to one current revision")


def _assert_revision_links(bind: sa.Connection) -> None:
    missing = bind.execute(
        sa.text(
            "SELECT count(*) FROM claim_evidence evidence "
            "WHERE evidence.idea_block_revision_id IS NULL "
            "OR NOT EXISTS ("
            "SELECT 1 FROM idea_block_revisions revision "
            "WHERE revision.id = evidence.idea_block_revision_id "
            "AND revision.idea_block_id = evidence.idea_block_id"
            ")"
        )
    ).scalar_one()
    if missing:
        raise RuntimeError(f"{missing} evidence rows do not point to their Idea Block revision")


def _assert_embedding_targets(bind: sa.Connection, tables: set[str]) -> None:
    for table_name in ("embedding_records", "embedding_jobs"):
        if table_name not in tables:
            continue
        invalid = bind.execute(
            sa.text(
                f"SELECT count(*) FROM {table_name} item "
                "WHERE item.target_type = 'idea_block' "
                "OR (item.target_type = 'idea_block_revision' AND NOT EXISTS ("
                "SELECT 1 FROM idea_block_revisions revision WHERE revision.id = item.target_id"
                "))"
            )
        ).scalar_one()
        if invalid:
            raise RuntimeError(f"{table_name} contains {invalid} non-revision Idea Block targets")
