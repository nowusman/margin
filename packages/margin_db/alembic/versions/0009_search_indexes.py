"""Add clean-target searchable Idea Block revision records and search indexes."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_search_indexes"
down_revision: str | Sequence[str] | None = "0008_unified_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIRED_TABLES = frozenset(
    {
        "idea_blocks",
        "idea_block_revisions",
        "idea_block_entities",
        "entities",
        "entity_aliases",
        "embedding_records",
        "embedding_jobs",
        "search_materialization_jobs",
        "search_embedding_assignments",
    }
)
_REQUIRED_COLUMNS = {
    "idea_blocks": {"domain_id", "current_revision_id", "merged_into_id", "tombstoned_at", "archived_at"},
    "idea_block_revisions": {
        "id",
        "idea_block_id",
        "search_text",
        "search_vector",
        "publication_status",
        "valid_from",
        "valid_to",
    },
    "embedding_records": {"target_type", "target_id", "model_id", "index_version", "dimensions", "embedding"},
    "search_materialization_jobs": {"idea_block_revision_id", "reason", "status"},
    "search_embedding_assignments": {"model_id", "index_version", "dimensions", "status"},
}


def upgrade() -> None:
    """Validate the matching model release and add PostgreSQL-only access paths."""

    bind = op.get_bind()
    _prepare_search_columns(bind)
    _assert_required_schema(bind)
    _assert_current_revision_links(bind)
    _assert_fixed_embedding_dimension(bind)
    _enqueue_initial_materialization(bind)
    if bind.dialect.name != "postgresql":
        return
    _assert_generated_search_vector(bind)
    _create_postgresql_indexes()


def downgrade() -> None:
    raise RuntimeError("0009_search_indexes is forward-only for the clean target schema")


def _assert_required_schema(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    missing_tables = sorted(_REQUIRED_TABLES - table_names)
    if missing_tables:
        raise RuntimeError("0009_search_indexes missing tables: " + ", ".join(missing_tables))
    missing_columns: list[str] = []
    for table_name, required in _REQUIRED_COLUMNS.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing_columns.extend(f"{table_name}.{column}" for column in sorted(required - columns))
    if missing_columns:
        raise RuntimeError("0009_search_indexes missing columns: " + ", ".join(missing_columns))


def _assert_current_revision_links(bind: sa.Connection) -> None:
    mismatch = bind.execute(
        sa.text(
            "SELECT block.id FROM idea_blocks block "
            "LEFT JOIN idea_block_revisions revision "
            "ON revision.id = block.current_revision_id "
            "AND revision.idea_block_id = block.id "
            "WHERE block.current_revision_id IS NOT NULL AND revision.id IS NULL LIMIT 1"
        )
    ).scalar_one_or_none()
    if mismatch is not None:
        raise RuntimeError(f"0009_search_indexes found invalid current IdeaBlock revision link: {mismatch}")


def _prepare_search_columns(bind: sa.Connection) -> None:
    _create_search_work_tables(bind)
    inspector = sa.inspect(bind)
    revision_columns = {column["name"] for column in inspector.get_columns("idea_block_revisions")}
    if bind.dialect.name != "postgresql":
        if "search_text" not in revision_columns:
            op.add_column(
                "idea_block_revisions",
                sa.Column("search_text", sa.Text(), nullable=False, server_default=sa.text("''")),
            )
        if "search_vector" not in revision_columns:
            op.add_column("idea_block_revisions", sa.Column("search_vector", sa.Text(), nullable=True))
        return
    if "search_text" not in revision_columns:
        op.execute("ALTER TABLE idea_block_revisions ADD COLUMN search_text text NOT NULL DEFAULT ''")
    if "search_vector" in revision_columns:
        op.execute("DROP INDEX IF EXISTS ix_idea_block_revisions_search_vector_gin")
        op.execute("ALTER TABLE idea_block_revisions DROP COLUMN search_vector")
    op.execute(
        "ALTER TABLE idea_block_revisions ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED"
    )


def _create_search_work_tables(bind: sa.Connection) -> None:
    table_names = set(sa.inspect(bind).get_table_names())
    if "search_materialization_jobs" not in table_names:
        op.create_table(
            "search_materialization_jobs",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "idea_block_revision_id",
                sa.Text(),
                sa.ForeignKey("idea_block_revisions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
            sa.Column("error_message", sa.Text()),
            sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "status in ('queued', 'processing', 'completed', 'failed')",
                name="ck_search_materialization_jobs_status",
            ),
        )
        op.create_index(
            "uq_search_materialization_jobs_pending_revision",
            "search_materialization_jobs",
            ["idea_block_revision_id"],
            unique=True,
            postgresql_where=sa.text("status in ('queued', 'processing')"),
            sqlite_where=sa.text("status in ('queued', 'processing')"),
        )
    if "search_embedding_assignments" not in table_names:
        op.create_table(
            "search_embedding_assignments",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("model_id", sa.Text(), nullable=False),
            sa.Column("index_version", sa.Text(), nullable=False),
            sa.Column("dimensions", sa.Integer(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'building'")),
            sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("activated_at", sa.DateTime(timezone=True)),
            sa.Column("retired_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("model_id", "index_version", name="uq_search_embedding_assignments_model_index"),
            sa.CheckConstraint(
                "status in ('building', 'active', 'retired', 'failed')", name="ck_search_embedding_assignments_status"
            ),
            sa.CheckConstraint("dimensions = 384", name="ck_search_embedding_assignments_dimensions"),
        )
        op.create_index(
            "uq_search_embedding_assignments_active",
            "search_embedding_assignments",
            ["status"],
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
            sqlite_where=sa.text("status = 'active'"),
        )


def _assert_fixed_embedding_dimension(bind: sa.Connection) -> None:
    invalid_records = bind.execute(
        sa.text("SELECT count(*) FROM embedding_records WHERE dimensions <> 384")
    ).scalar_one()
    invalid_assignments = bind.execute(
        sa.text("SELECT count(*) FROM search_embedding_assignments WHERE dimensions <> 384")
    ).scalar_one()
    if invalid_records or invalid_assignments:
        raise RuntimeError(
            "0009_search_indexes requires only fixed 384-dimension embeddings; rebuild the clean target instead"
        )


def _enqueue_initial_materialization(bind: sa.Connection) -> None:
    """Durably seed every existing searchable current revision for bounded work."""

    metadata_literal = "'{}'::jsonb" if bind.dialect.name == "postgresql" else "'{}'"
    op.execute(
        "INSERT INTO search_materialization_jobs "
        "(id, idea_block_revision_id, reason, status, metadata, created_at, updated_at) "
        "SELECT DISTINCT 'searchjob_initial_' || revision.id, revision.id, "
        f"'search_initial_population', 'queued', {metadata_literal}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "FROM idea_block_revisions revision "
        "JOIN idea_blocks block ON block.current_revision_id = revision.id "
        "WHERE revision.publication_status IN ('candidate', 'verified') "
        "AND revision.idea_block_id = block.id "
        "AND block.merged_into_id IS NULL "
        "AND block.tombstoned_at IS NULL "
        "AND block.archived_at IS NULL "
        "AND NOT EXISTS ("
        "SELECT 1 FROM search_materialization_jobs job "
        "WHERE job.idea_block_revision_id = revision.id"
        ")"
    )


def _assert_generated_search_vector(bind: sa.Connection) -> None:
    generated = bind.execute(
        sa.text(
            "SELECT attribute.attgenerated, pg_get_expr(default_value.adbin, default_value.adrelid) "
            "FROM pg_attribute attribute "
            "JOIN pg_class relation ON relation.oid = attribute.attrelid "
            "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
            "JOIN pg_attrdef default_value "
            "ON default_value.adrelid = relation.oid AND default_value.adnum = attribute.attnum "
            "WHERE namespace.nspname = current_schema() "
            "AND relation.relname = 'idea_block_revisions' "
            "AND attribute.attname = 'search_vector'"
        )
    ).one_or_none()
    if generated is None:
        raise RuntimeError("0009_search_indexes requires idea_block_revisions.search_vector")
    generated_flag, expression = generated
    normalized_expression = str(expression).lower()
    if generated_flag != "s" or not all(
        value in normalized_expression for value in ("to_tsvector", "simple", "search_text")
    ):
        raise RuntimeError(
            "0009_search_indexes requires a stored simple to_tsvector search_vector generated from search_text"
        )


def _create_postgresql_indexes() -> None:
    # 0001 created expression indexes for the retired entity/source retrieval
    # paths.  The current candidate query reads only the persisted Idea Block
    # vector; source discovery is an explicit, separate operation and does not
    # use those expression indexes.
    for index_name in (
        "ix_entities_retrieval_fts",
        "ix_entities_retrieval_trgm",
        "ix_source_elements_retrieval_fts",
        "ix_source_elements_retrieval_trgm",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    statements = (
        "CREATE INDEX IF NOT EXISTS ix_idea_block_revisions_search_vector_gin "
        "ON idea_block_revisions USING gin (search_vector)",
        "CREATE INDEX IF NOT EXISTS ix_idea_blocks_search_scope "
        "ON idea_blocks (domain_id, current_revision_id) "
        "WHERE merged_into_id IS NULL AND tombstoned_at IS NULL AND archived_at IS NULL",
        "CREATE INDEX IF NOT EXISTS ix_idea_block_revisions_search_status_validity "
        "ON idea_block_revisions (publication_status, valid_from, valid_to, id)",
        "CREATE INDEX IF NOT EXISTS ix_embedding_records_idea_block_revision_hnsw "
        "ON embedding_records USING hnsw (embedding vector_cosine_ops) "
        "WHERE target_type = 'idea_block_revision'",
        "CREATE INDEX IF NOT EXISTS ix_embedding_records_source_element_hnsw "
        "ON embedding_records USING hnsw (embedding vector_cosine_ops) "
        "WHERE target_type = 'source_element'",
        "CREATE INDEX IF NOT EXISTS ix_embedding_records_search_assignment "
        "ON embedding_records (model_id, index_version, target_type, target_id) "
        "WHERE dimensions = 384",
    )
    for statement in statements:
        op.execute(statement)
