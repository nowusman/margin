"""The personal layer: what one reader has built up for themselves.

A governed corpus tells an organisation what it knows. A personal layer tells
one person what they have read, saved, written down and been thinking about.
It is the difference between a portal and a second brain.

Everything here is owned by a principal and private to them by default. A
personal record references a canonical record rather than copying it, so the
corpus remains the single source of truth and a personal note can never become
a published claim by accident.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, JsonDict, utc_now

NOTE_VISIBILITY_PRIVATE = "private"
NOTE_VISIBILITIES = (NOTE_VISIBILITY_PRIVATE,)

TARGET_IDEA_BLOCK = "idea_block"
TARGET_ENTITY = "entity"
TARGET_GOVERNANCE_RECORD = "governance_record"
TARGET_SOURCE_DOCUMENT = "source_document"
PERSONAL_TARGET_TYPES = (TARGET_IDEA_BLOCK, TARGET_ENTITY, TARGET_GOVERNANCE_RECORD, TARGET_SOURCE_DOCUMENT)

MESSAGE_ROLE_USER = "user"
MESSAGE_ROLE_ASSISTANT = "assistant"
MESSAGE_ROLES = (MESSAGE_ROLE_USER, MESSAGE_ROLE_ASSISTANT)


class PersonalNote(Base):
    """A reader's own annotation against a canonical record."""

    __tablename__ = "personal_notes"
    __table_args__ = (
        Index("ix_personal_notes_owner", "owner_principal_id"),
        Index("ix_personal_notes_target", "target_type", "target_id"),
        Index("ix_personal_notes_owner_target", "owner_principal_id", "target_type", "target_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    domain_id: Mapped[str | None] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default=NOTE_VISIBILITY_PRIVATE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SavedItem(Base):
    """A record a reader has kept for themselves."""

    __tablename__ = "saved_items"
    __table_args__ = (
        Index("ix_saved_items_owner", "owner_principal_id"),
        Index("ix_saved_items_target", "target_type", "target_id"),
        Index("uq_saved_items_owner_target", "owner_principal_id", "target_type", "target_id", unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    domain_id: Mapped[str | None] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[JsonDict] = mapped_column(JsonDict, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PersonalView(Base):
    """A record a reader has looked at, used to measure recency."""

    __tablename__ = "personal_views"
    __table_args__ = (
        Index("ix_personal_views_owner_target", "owner_principal_id", "target_type", "target_id"),
        Index("ix_personal_views_owner_viewed", "owner_principal_id", "viewed_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    domain_id: Mapped[str | None] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssistantThread(Base):
    """A reader's ongoing conversation with Margin."""

    __tablename__ = "assistant_threads"
    __table_args__ = (
        Index("ix_assistant_threads_owner", "owner_principal_id"),
        Index("ix_assistant_threads_owner_updated", "owner_principal_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    domain_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssistantMessage(Base):
    """One turn in a conversation, with the citations it relied on."""

    __tablename__ = "assistant_messages"
    __table_args__ = (Index("ix_assistant_messages_thread", "thread_id", "position"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    owner_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    citation_ids: Mapped[JsonDict] = mapped_column(JsonDict, nullable=False, default=list)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
