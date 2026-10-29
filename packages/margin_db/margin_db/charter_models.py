"""Domain charters: what a domain covers, and how its knowledge is read.

A charter is authored prose owned by a domain steward. It states the purpose of
the domain, what is inside its scope, what is explicitly outside it, and the
terminology the domain prefers. It is injected into extraction so that the
records produced for a domain reflect how that domain actually works.

It is deliberately not a ranking input. Authored intent must never manufacture
relevance, so the charter is read by extraction and by the reader-facing
briefing, and never by the retrieval ranker.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, utc_now

CHARTER_STATUS_ACTIVE = "active"
CHARTER_STATUS_SUPERSEDED = "superseded"
CHARTER_STATUS_DRAFT = "draft"
CHARTER_STATUSES = (CHARTER_STATUS_ACTIVE, CHARTER_STATUS_SUPERSEDED, CHARTER_STATUS_DRAFT)


class DomainCharter(Base):
    """One authored version of a domain's charter."""

    __tablename__ = "domain_charters"
    __table_args__ = (
        Index("ix_domain_charters_domain_id", "domain_id"),
        Index("ix_domain_charters_domain_status", "domain_id", "status"),
        Index("uq_domain_charters_domain_version", "domain_id", "version", unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=CHARTER_STATUS_DRAFT)
    author_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scope_inclusions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scope_exclusions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    terminology: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
