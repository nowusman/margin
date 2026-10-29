"""Persisted export requests.

An export bundle is stored rather than written to an object store, so that the
archive a reader downloads is rendered from the same records the manifest
describes. The rendering is deterministic, so the archive is identical every
time it is produced.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, JsonDict, utc_now

EXPORT_STATE_PENDING = "pending"
EXPORT_STATE_READY = "ready"
EXPORT_STATE_FAILED = "failed"
EXPORT_STATES = (EXPORT_STATE_PENDING, EXPORT_STATE_READY, EXPORT_STATE_FAILED)


class ExportRequest(Base):
    """One requested export of a domain's canonical records."""

    __tablename__ = "export_bundles"
    __table_args__ = (
        Index("ix_export_bundles_domain_id", "domain_id"),
        Index("ix_export_bundles_requested_by", "requested_by"),
        Index("ix_export_bundles_state", "state"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    domain_id: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default=EXPORT_STATE_PENDING)
    content_hash: Mapped[str | None] = mapped_column(Text)
    manifest: Mapped[JsonDict] = mapped_column(JsonDict, nullable=False, default=dict)
    records: Mapped[JsonDict] = mapped_column(JsonDict, nullable=False, default=dict)
    scope: Mapped[JsonDict] = mapped_column(JsonDict, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
