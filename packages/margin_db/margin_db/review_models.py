from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, JsonDict, JsonList, utc_now


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        CheckConstraint(
            "status in ('open', 'assigned', 'needs_evidence', 'approved', 'rejected', 'closed')",
            name="ck_review_tasks_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    priority: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    recommendation: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JsonList, default=list)
    resolution: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewTaskComment(Base):
    __tablename__ = "review_task_comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_tasks.id"), nullable=False)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
