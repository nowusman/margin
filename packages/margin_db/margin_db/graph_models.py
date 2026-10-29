"""Persisted reader decisions about the knowledge graph.

The graph itself is never stored. It is recomputed from canonical records on
every request, because a stored graph would be a second source of truth that
could disagree with the records it claims to describe.

What is stored is a reader's decision about a surfaced item: that a connection
is not worth seeing again, or that a knowledge gap is not worth acting on yet.
Both are judgements about the reader's own attention, not properties of the
corpus, so they belong to the reader. One decision exists per reader and per
target, so a deferred gap can later be dismissed without leaving a
contradictory pair of rows behind.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, utc_now

GRAPH_DECISION_DISMISS = "dismiss"
GRAPH_DECISION_DEFER = "defer"
GRAPH_DECISION_KINDS = (GRAPH_DECISION_DISMISS, GRAPH_DECISION_DEFER)

GRAPH_TARGET_CONNECTION = "connection"
GRAPH_TARGET_GAP = "gap"
GRAPH_TARGET_TYPES = (GRAPH_TARGET_CONNECTION, GRAPH_TARGET_GAP)


class GraphInsightDecision(Base):
    """A reader's record of how they want a surfaced item treated."""

    __tablename__ = "graph_insight_decisions"
    __table_args__ = (
        Index("ix_graph_insight_decisions_principal_id", "principal_id"),
        Index("ix_graph_insight_decisions_target_id", "target_id"),
        Index("uq_graph_insight_decisions_principal_target", "principal_id", "target_type", "target_id", unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False, default=GRAPH_TARGET_CONNECTION)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False, default=GRAPH_DECISION_DISMISS)
    deferred_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
