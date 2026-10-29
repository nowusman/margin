from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, JsonDict, utc_now


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict | None] = mapped_column(JsonDict)
    after: Mapped[dict | None] = mapped_column(JsonDict)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    git_commit_sha: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
