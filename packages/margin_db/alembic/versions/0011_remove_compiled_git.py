"""Remove compiled publication and legacy assistant persistence.

The canonical source, evidence, IdeaBlock, entity, and explicit relationship
records remain. This cutover removes only generated publication material and
the unused persisted assistant workflow state.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_remove_compiled_git"
down_revision: str | Sequence[str] | None = "0010_remove_graph_projections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REMOVED_TABLES = (
    "compiled_articles",
    "synthesis_candidates",
    "assistant_answer_feedback",
    "retrieval_traces",
    "assistant_conversations",
)


def upgrade() -> None:
    bind = op.get_bind()
    _remove_compilation_models(bind)
    _drop_unused_git_version_column(bind)
    _drop_removed_tables(bind)
    _assert_removed_tables_absent(bind)


def downgrade() -> None:
    raise RuntimeError("0011_remove_compiled_git is forward-only for the clean target schema")


def _remove_compilation_models(bind: sa.Connection) -> None:
    tables = set(sa.inspect(bind).get_table_names())
    if "model_configurations" not in tables:
        return
    bind.execute(sa.text("DELETE FROM model_configurations WHERE purpose = 'compilation'"))
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text("ALTER TABLE model_configurations DROP CONSTRAINT IF EXISTS ck_model_configurations_purpose"))
    bind.execute(
        sa.text(
            "ALTER TABLE model_configurations ADD CONSTRAINT ck_model_configurations_purpose "
            "CHECK (purpose in ('extraction', 'vision_ocr', 'answer_synthesis', 'semantic_resolution'))"
        )
    )


def _drop_removed_tables(bind: sa.Connection) -> None:
    table_names = set(sa.inspect(bind).get_table_names())
    for table_name in _REMOVED_TABLES:
        if table_name in table_names:
            op.drop_table(table_name)


def _drop_unused_git_version_column(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    if "source_document_versions" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("source_document_versions")}
    if "git_commit_sha" in columns:
        op.drop_column("source_document_versions", "git_commit_sha")


def _assert_removed_tables_absent(bind: sa.Connection) -> None:
    remaining = sorted(set(sa.inspect(bind).get_table_names()).intersection(_REMOVED_TABLES))
    if remaining:
        raise RuntimeError("0011_remove_compiled_git left tables: " + ", ".join(remaining))
