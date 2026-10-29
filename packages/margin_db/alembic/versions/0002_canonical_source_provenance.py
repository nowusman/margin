"""Validate the canonical source provenance contract on the clean schema."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_canonical_source_provenance"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REMOVED_TABLES = frozenset({"source_sections", "source_spans", "source_element_spans", "knowledge_episodes"})
_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "source_pages": frozenset(
        {"id", "source_version_id", "page_number", "content_checksum", "classification", "metadata"}
    ),
    "source_elements": frozenset(
        {
            "id",
            "source_version_id",
            "source_page_id",
            "parent_element_id",
            "native_element_id",
            "kind",
            "ordinal",
            "text",
            "checksum",
            "char_start",
            "char_end",
            "bounding_box",
            "asset_key",
            "converter_method",
            "converter_version",
            "confidence",
            "metadata",
        }
    ),
    "source_page_outcomes": frozenset({"id", "extraction_run_id", "source_page_id", "outcome", "reason"}),
    "source_element_outcomes": frozenset(
        {
            "id",
            "extraction_run_id",
            "source_element_id",
            "outcome",
            "reason",
            "extractor",
            "verification_method",
            "verifier_version",
        }
    ),
}
_PROVENANCE_COLUMNS = (
    ("claim_evidence", "source_element_id", False),
    ("entity_mentions", "source_element_id", True),
    ("relationship_provenance", "source_element_id", True),
    ("unmapped_extractions", "source_element_id", True),
    ("span_extraction_attempts", "source_element_id", True),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    missing_tables = set(_REQUIRED_COLUMNS) - tables
    if missing_tables:
        raise RuntimeError(f"clean schema is missing canonical provenance tables: {sorted(missing_tables)}")

    present_removed_tables = _REMOVED_TABLES & tables
    if present_removed_tables:
        raise RuntimeError(f"clean schema contains removed provenance tables: {sorted(present_removed_tables)}")

    for table_name, required_columns in _REQUIRED_COLUMNS.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing_columns = required_columns - columns
        if missing_columns:
            raise RuntimeError(f"{table_name} is missing canonical columns: {sorted(missing_columns)}")

    for table_name, column_name, required in _PROVENANCE_COLUMNS:
        if table_name not in tables:
            continue
        columns_by_name = {column["name"]: column for column in inspector.get_columns(table_name)}
        column = columns_by_name.get(column_name)
        if column is None or (required and column["nullable"]):
            raise RuntimeError(f"{table_name}.{column_name} must be a required canonical provenance link")
        if required:
            null_count = bind.execute(
                sa.text(f"SELECT count(*) FROM {table_name} WHERE {column_name} IS NULL")
            ).scalar_one()
            if null_count:
                raise RuntimeError(f"{table_name} contains {null_count} null {column_name} values")
        orphan_count = bind.execute(
            sa.text(
                f"SELECT count(*) FROM {table_name} row "
                "LEFT JOIN source_elements element ON element.id = row.source_element_id "
                f"WHERE row.{column_name} IS NOT NULL AND element.id IS NULL"
            )
        ).scalar_one()
        if orphan_count:
            raise RuntimeError(f"{table_name} contains {orphan_count} orphan source-element links")

    _assert_versions_have_pages(bind, tables)
    _assert_no_page_processing_state(inspector)


def downgrade() -> None:
    raise RuntimeError("0002_canonical_source_provenance is forward-only for the greenfield schema")


def _assert_versions_have_pages(bind: sa.Connection, tables: set[str]) -> None:
    if "source_document_versions" not in tables:
        return
    missing_pages = bind.execute(
        sa.text(
            "SELECT count(*) FROM source_document_versions version "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM source_pages page WHERE page.source_version_id = version.id"
            ")"
        )
    ).scalar_one()
    if missing_pages:
        raise RuntimeError(f"{missing_pages} source versions have no canonical source page")


def _assert_no_page_processing_state(inspector: sa.Inspector) -> None:
    page_columns = {column["name"] for column in inspector.get_columns("source_pages")}
    forbidden = page_columns & {"processing_status", "retry_count", "reason"}
    if forbidden:
        raise RuntimeError(f"source_pages contains processing state that belongs to run outcomes: {sorted(forbidden)}")
