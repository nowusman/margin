"""Remove derived community and similarity projections.

Explicit Entity, Idea Block, and Governance Record relationships remain the
only relationship persistence. Topic articles were generated from the removed
projection snapshots, so their rows and foreign-key columns leave with them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_remove_graph_projections"
down_revision: str | Sequence[str] | None = "0009_search_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROJECTION_TABLES = (
    "topic_community_edges",
    "topic_community_members",
    "topic_community_snapshots",
    "graph_similarity_edges",
    "graph_similarity_snapshots",
    "graph_community_members",
    "graph_community_snapshots",
)


def upgrade() -> None:
    """Remove projection-only storage and add retained relationship indexes."""

    bind = op.get_bind()
    _retire_topic_articles(bind)
    _drop_projection_tables(bind)
    _create_relationship_indexes(bind)
    _assert_projection_absence(bind)


def downgrade() -> None:
    raise RuntimeError("0010_remove_graph_projections is forward-only for the clean target schema")


def _retire_topic_articles(bind: sa.Connection) -> None:
    table_names = set(sa.inspect(bind).get_table_names())
    if "compiled_articles" not in table_names:
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("compiled_articles")}
    if "projection_snapshot_id" not in columns:
        return

    op.execute("DELETE FROM compiled_articles WHERE article_type = 'topic'")
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE compiled_articles DROP CONSTRAINT IF EXISTS ck_compiled_articles_scope")
    op.execute("ALTER TABLE compiled_articles DROP CONSTRAINT IF EXISTS ck_compiled_articles_article_type")
    op.execute("ALTER TABLE compiled_articles DROP CONSTRAINT IF EXISTS compiled_articles_projection_snapshot_id_fkey")
    op.execute("ALTER TABLE compiled_articles DROP COLUMN IF EXISTS projection_snapshot_id")
    op.execute("ALTER TABLE compiled_articles DROP COLUMN IF EXISTS topic_key")
    op.execute(
        "ALTER TABLE compiled_articles ADD CONSTRAINT ck_compiled_articles_article_type "
        "CHECK (article_type IN ('entity', 'source'))"
    )
    op.execute(
        "ALTER TABLE compiled_articles ADD CONSTRAINT ck_compiled_articles_scope CHECK ("
        "(article_type = 'entity' AND entity_id IS NOT NULL AND source_document_id IS NULL) OR "
        "(article_type = 'source' AND entity_id IS NULL AND source_document_id IS NOT NULL)"
        ")"
    )


def _drop_projection_tables(bind: sa.Connection) -> None:
    table_names = set(sa.inspect(bind).get_table_names())
    for table_name in _PROJECTION_TABLES:
        if table_name in table_names:
            op.drop_table(table_name)


def _create_relationship_indexes(bind: sa.Connection) -> None:
    statements = (
        "CREATE INDEX IF NOT EXISTS ix_entity_relationships_source_type_status "
        "ON entity_relationships (source_entity_id, relation_type, status, valid_to)",
        "CREATE INDEX IF NOT EXISTS ix_entity_relationships_target_type_status "
        "ON entity_relationships (target_entity_id, relation_type, status, valid_to)",
        "CREATE INDEX IF NOT EXISTS ix_idea_block_relations_source_type_status "
        "ON idea_block_relations (source_idea_block_id, relation_type, status, valid_to)",
        "CREATE INDEX IF NOT EXISTS ix_idea_block_relations_target_type_status "
        "ON idea_block_relations (target_idea_block_id, relation_type, status, valid_to)",
        "CREATE INDEX IF NOT EXISTS ix_governance_record_relations_source_type_status "
        "ON governance_record_relations (source_record_id, relation_type, status)",
        "CREATE INDEX IF NOT EXISTS ix_governance_record_relations_target_type_status "
        "ON governance_record_relations (target_record_id, relation_type, status)",
    )
    for statement in statements:
        bind.execute(sa.text(statement))


def _assert_projection_absence(bind: sa.Connection) -> None:
    remaining = sorted(set(sa.inspect(bind).get_table_names()).intersection(_PROJECTION_TABLES))
    if remaining:
        raise RuntimeError("0010_remove_graph_projections left tables: " + ", ".join(remaining))
