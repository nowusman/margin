from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, JsonDict, JsonList, utc_now
from margin_schema.ontology import ONTOLOGY_VERSION

ENTITY_LIFECYCLE_STATES = ("draft", "active", "superseded", "retired")
ENTITY_ALIAS_LIFECYCLE_STATES = ("active", "deprecated", "retired")
RESOLVED_MENTION_LINK_ROLE = "resolved_mention"


class Entity(Base):
    """The single canonical catalog for architecture entities and vocabulary terms."""

    __tablename__ = "entities"
    __table_args__ = (
        Index("ix_entities_ontology_type", "ontology_type"),
        Index("ix_entities_normalized_label", "normalized_label"),
        Index("ix_entities_domain_type_label", "domain_id", "ontology_type", "normalized_label"),
        Index(
            "uq_entities_active_local_scope",
            "ontology_type",
            "locale",
            "normalized_label",
            text("coalesce(domain_id, '')"),
            unique=True,
            postgresql_where=text("lifecycle_status <> 'retired'"),
            sqlite_where=text("lifecycle_status <> 'retired'"),
        ),
        Index(
            "uq_entities_external_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
        CheckConstraint(
            "lifecycle_status in ('draft', 'active', 'superseded', 'retired')", name="ck_entities_lifecycle_status"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    preferred_label: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_label: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[str | None] = mapped_column(Text)
    definition_idea_block_id: Mapped[str | None] = mapped_column(ForeignKey("idea_blocks.id"))
    ontology_type: Mapped[str] = mapped_column(Text, nullable=False)
    domain_id: Mapped[str | None] = mapped_column(ForeignKey("domains.id"))
    locale: Mapped[str] = mapped_column(Text, nullable=False, default="en")
    external_id: Mapped[str | None] = mapped_column(Text)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    ontology_version: Mapped[str] = mapped_column(Text, nullable=False, default=ONTOLOGY_VERSION)
    catalog_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    superseded_by_entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id"))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    steward_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class EntityAlias(Base):
    """A source-grounded alternate label in one domain/type/locale scope."""

    __tablename__ = "entity_aliases"
    __table_args__ = (
        Index("ix_entity_aliases_entity_id", "entity_id"),
        Index("ix_entity_aliases_lookup", "domain_id", "ontology_type", "locale", "normalized_alias"),
        Index(
            "uq_entity_aliases_active_scope",
            "ontology_type",
            "locale",
            "normalized_alias",
            text("coalesce(domain_id, '')"),
            unique=True,
            postgresql_where=text("lifecycle_status = 'active'"),
            sqlite_where=text("lifecycle_status = 'active'"),
        ),
        CheckConstraint(
            "lifecycle_status in ('active', 'deprecated', 'retired')", name="ck_entity_aliases_lifecycle_status"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    locale: Mapped[str] = mapped_column(Text, nullable=False, default="en")
    domain_id: Mapped[str | None] = mapped_column(ForeignKey("domains.id"))
    ontology_type: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    source_provenance: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class EntityMention(Base):
    """An unresolved or resolved source mention anchored to a revision."""

    __tablename__ = "entity_mentions"
    __table_args__ = (
        Index("ix_entity_mentions_revision_id", "idea_block_revision_id"),
        Index("ix_entity_mentions_source_element_id", "source_element_id"),
        Index("ix_entity_mentions_scope", "domain_id", "proposed_type", "normalized_text"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    idea_block_revision_id: Mapped[str] = mapped_column(
        ForeignKey("idea_block_revisions.id", ondelete="CASCADE"), nullable=False
    )
    source_element_id: Mapped[str] = mapped_column(ForeignKey("source_elements.id", ondelete="CASCADE"), nullable=False)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    candidate_entity_ids: Mapped[list[str]] = mapped_column(JsonList, default=list)
    ontology_version: Mapped[str] = mapped_column(Text, nullable=False, default=ONTOLOGY_VERSION)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EntityResolutionDecision(Base):
    __tablename__ = "entity_resolution_decisions"
    __table_args__ = (
        Index("ix_entity_resolution_decisions_entity_id", "entity_id"),
        Index("ix_entity_resolution_decisions_mention_sequence", "mention_id", "sequence"),
        Index(
            "uq_entity_resolution_decisions_current_mention",
            "mention_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    mention_id: Mapped[str] = mapped_column(ForeignKey("entity_mentions.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id"))
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)
    candidate_entity_ids: Mapped[list[str]] = mapped_column(JsonList, default=list)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="review_required")
    ontology_version: Mapped[str] = mapped_column(Text, nullable=False, default=ONTOLOGY_VERSION)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EntityResolutionAction(Base):
    __tablename__ = "entity_resolution_actions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    source_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), nullable=False)
    target_entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id"))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EntityReresolutionJob(Base):
    """Deduplicated affected-only re-resolution work."""

    __tablename__ = "entity_reresolution_jobs"
    __table_args__ = (
        UniqueConstraint("deduplication_key", name="uq_entity_reresolution_jobs_deduplication_key"),
        Index("ix_entity_reresolution_jobs_state", "state"),
        CheckConstraint(
            "state in ('queued', 'running', 'completed', 'failed')", name="ck_entity_reresolution_jobs_state"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    deduplication_key: Mapped[str] = mapped_column(Text, nullable=False)
    domain_id: Mapped[str | None] = mapped_column(ForeignKey("domains.id"))
    entity_ids: Mapped[list[str]] = mapped_column(JsonList, default=list)
    entity_versions: Mapped[dict[str, int]] = mapped_column(JsonDict, default=dict)
    normalized_aliases: Mapped[list[str]] = mapped_column(JsonList, default=list)
    ontology_version: Mapped[str] = mapped_column(Text, nullable=False, default=ONTOLOGY_VERSION)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    affected_mention_count: Mapped[int] = mapped_column(nullable=False, default=0)
    affected_revision_count: Mapped[int] = mapped_column(nullable=False, default=0)
    failure: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
