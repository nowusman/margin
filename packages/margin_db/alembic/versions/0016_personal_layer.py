"""Add the personal layer: notes, saved items, views and conversations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_personal_layer"
down_revision: str | Sequence[str] | None = "0015_domain_charters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

Text = sa.Text()
Integer = sa.Integer()
Json = sa.JSON()
Timestamp = sa.DateTime(timezone=True)

_TABLES: tuple[
    tuple[str, tuple[tuple[str, sa.types.TypeEngine, bool], ...], tuple[tuple[str, tuple[str, ...], bool], ...]], ...
] = (
    (
        "personal_notes",
        (
            ("id", Text, False),
            ("owner_principal_id", Text, False),
            ("domain_id", Text, True),
            ("target_type", Text, False),
            ("target_id", Text, False),
            ("body", Text, False),
            ("visibility", Text, False),
            ("created_at", Timestamp, False),
            ("updated_at", Timestamp, False),
        ),
        (
            ("ix_personal_notes_owner", ("owner_principal_id",), False),
            ("ix_personal_notes_target", ("target_type", "target_id"), False),
            ("ix_personal_notes_owner_target", ("owner_principal_id", "target_type", "target_id"), False),
        ),
    ),
    (
        "saved_items",
        (
            ("id", Text, False),
            ("owner_principal_id", Text, False),
            ("domain_id", Text, True),
            ("target_type", Text, False),
            ("target_id", Text, False),
            ("label", Text, True),
            ("tags", Json, False),
            ("created_at", Timestamp, False),
        ),
        (
            ("ix_saved_items_owner", ("owner_principal_id",), False),
            ("ix_saved_items_target", ("target_type", "target_id"), False),
            ("uq_saved_items_owner_target", ("owner_principal_id", "target_type", "target_id"), True),
        ),
    ),
    (
        "personal_views",
        (
            ("id", Text, False),
            ("owner_principal_id", Text, False),
            ("domain_id", Text, True),
            ("target_type", Text, False),
            ("target_id", Text, False),
            ("viewed_at", Timestamp, False),
        ),
        (
            ("ix_personal_views_owner_target", ("owner_principal_id", "target_type", "target_id"), False),
            ("ix_personal_views_owner_viewed", ("owner_principal_id", "viewed_at"), False),
        ),
    ),
    (
        "assistant_threads",
        (
            ("id", Text, False),
            ("owner_principal_id", Text, False),
            ("domain_id", Text, True),
            ("title", Text, False),
            ("created_at", Timestamp, False),
            ("updated_at", Timestamp, False),
        ),
        (
            ("ix_assistant_threads_owner", ("owner_principal_id",), False),
            ("ix_assistant_threads_owner_updated", ("owner_principal_id", "updated_at"), False),
        ),
    ),
    (
        "assistant_messages",
        (
            ("id", Text, False),
            ("thread_id", Text, False),
            ("owner_principal_id", Text, False),
            ("role", Text, False),
            ("content", Text, False),
            ("citation_ids", Json, False),
            ("position", Integer, False),
            ("created_at", Timestamp, False),
        ),
        (("ix_assistant_messages_thread", ("thread_id", "position"), False),),
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name, columns, indexes in _TABLES:
        inspector = sa.inspect(bind)
        if table_name not in inspector.get_table_names():
            op.create_table(
                table_name,
                *(sa.Column(name, column_type, nullable=nullable) for name, column_type, nullable in columns),
                sa.PrimaryKeyConstraint("id"),
            )
        existing_indexes = {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}
        for name, index_columns, unique in indexes:
            if name in existing_indexes:
                continue
            op.create_index(name, table_name, list(index_columns), unique=unique)


def downgrade() -> None:
    raise RuntimeError("0016_personal_layer is forward-only for the clean target schema")
