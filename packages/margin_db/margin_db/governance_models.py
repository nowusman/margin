"""Canonical persistence for every Architecture Governance record."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from margin_db.base import Base, JsonDict, utc_now


class GovernanceRecord(Base):
    """One lifecycle-owned record; type-specific fields live in ``attributes``."""

    __tablename__ = "governance_records"
    __table_args__ = (
        UniqueConstraint("domain_id", "business_key", name="uq_governance_records_domain_business_key"),
        UniqueConstraint("external_issue_id", name="uq_governance_records_external_issue_id"),
        Index("ix_governance_records_type_status", "record_type", "lifecycle_status"),
        Index("ix_governance_records_owner", "owner_id"),
        Index("ix_governance_records_priority_severity", "priority", "severity"),
        Index("ix_governance_records_review_date", "review_date"),
        Index("ix_governance_records_expiry_date", "expiry_date"),
        Index("ix_governance_records_target_date", "target_date"),
        CheckConstraint(
            "record_type in ('adr', 'risk', 'waiver', 'technical_debt', 'architecture_backlog')",
            name="ck_governance_records_type",
        ),
        CheckConstraint(
            "(record_type = 'adr' and lifecycle_status in "
            "('proposed', 'accepted', 'superseded', 'deprecated', 'rejected')) "
            "or (record_type = 'risk' and lifecycle_status in "
            "('identified', 'mitigating', 'accepted', 'closed')) "
            "or (record_type = 'waiver' and lifecycle_status in "
            "('proposed', 'approved', 'expired', 'revoked', 'rejected')) "
            "or (record_type = 'technical_debt' and lifecycle_status in "
            "('identified', 'accepted', 'planned', 'in_progress', 'resolved')) "
            "or (record_type = 'architecture_backlog' and lifecycle_status in "
            "('open', 'planned', 'in_progress', 'blocked', 'completed', 'cancelled'))",
            name="ck_governance_records_lifecycle_status",
        ),
        CheckConstraint(
            "effective_date is null or review_date is null or review_date >= effective_date",
            name="ck_governance_records_review_date",
        ),
        CheckConstraint(
            "effective_date is null or expiry_date is null or expiry_date >= effective_date",
            name="ck_governance_records_expiry_date",
        ),
        CheckConstraint(
            "record_type <> 'waiver' or expiry_date is not null", name="ck_governance_records_waiver_expiry"
        ),
        CheckConstraint("jsonb_typeof(attributes) = 'object'", name="ck_governance_records_attributes_object").ddl_if(
            dialect="postgresql"
        ),
        CheckConstraint("json_type(attributes) = 'object'", name="ck_governance_records_attributes_object").ddl_if(
            dialect="sqlite"
        ),
        CheckConstraint(
            "record_type <> 'waiver' or (expiry_date is not null and case "
            "when jsonb_typeof(attributes -> 'expiry_date') = 'string' "
            "and (attributes ->> 'expiry_date') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' "
            "then (attributes ->> 'expiry_date')::date = expiry_date else false end)",
            name="ck_governance_records_waiver_expiry_attribute",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "record_type <> 'waiver' or (expiry_date is not null and "
            "json_type(attributes, '$.expiry_date') = 'text' and "
            "json_extract(attributes, '$.expiry_date') glob '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' and "
            "date(json_extract(attributes, '$.expiry_date')) = json_extract(attributes, '$.expiry_date') and "
            "date(json_extract(attributes, '$.expiry_date')) = expiry_date)",
            name="ck_governance_records_waiver_expiry_attribute",
        ).ddl_if(dialect="sqlite"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    business_key: Mapped[str] = mapped_column(Text, nullable=False)
    record_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id"), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    steward_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    effective_date: Mapped[date | None] = mapped_column(Date)
    review_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    target_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[int | None] = mapped_column(Integer)
    external_issue_id: Mapped[str | None] = mapped_column(Text)
    external_issue_url: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, default="governance-v1")
    current_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("governance_record_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    attributes: Mapped[dict] = mapped_column(JsonDict, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    current_revision: Mapped[GovernanceRecordRevision | None] = relationship(
        "GovernanceRecordRevision", foreign_keys=[current_revision_id], uselist=False, post_update=True
    )


class GovernanceRecordRevision(Base):
    """An immutable complete snapshot for every material governance change."""

    __tablename__ = "governance_record_revisions"
    __table_args__ = (
        UniqueConstraint("governance_record_id", "revision_number", name="uq_governance_record_revisions_number"),
        Index("ix_governance_record_revisions_record", "governance_record_id", "revision_number"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    governance_record_id: Mapped[str] = mapped_column(
        ForeignKey("governance_records.id", ondelete="CASCADE"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JsonDict, nullable=False)
    transition_reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    process: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GovernanceRecordIdeaBlock(Base):
    __tablename__ = "governance_record_idea_blocks"
    __table_args__ = (
        UniqueConstraint("governance_record_id", "idea_block_id", "role", name="uq_governance_record_idea_blocks_role"),
        Index("ix_governance_record_idea_blocks_idea_block", "idea_block_id"),
    )

    governance_record_id: Mapped[str] = mapped_column(
        ForeignKey("governance_records.id", ondelete="CASCADE"), primary_key=True
    )
    idea_block_id: Mapped[str] = mapped_column(ForeignKey("idea_blocks.id", ondelete="RESTRICT"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GovernanceRecordEntity(Base):
    __tablename__ = "governance_record_entities"
    __table_args__ = (
        UniqueConstraint("governance_record_id", "entity_id", "role", name="uq_governance_record_entities_role"),
        Index("ix_governance_record_entities_entity", "entity_id"),
    )

    governance_record_id: Mapped[str] = mapped_column(
        ForeignKey("governance_records.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="RESTRICT"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)


class GovernanceRecordRelation(Base):
    __tablename__ = "governance_record_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_record_id",
            "target_record_id",
            "relation_type",
            name="uq_governance_record_relations_endpoints_type",
        ),
        Index("ix_governance_record_relations_source_type_status", "source_record_id", "relation_type", "status"),
        Index("ix_governance_record_relations_target_type_status", "target_record_id", "relation_type", "status"),
        CheckConstraint("source_record_id <> target_record_id", name="ck_governance_record_relations_distinct"),
        CheckConstraint(
            "relation_type in ('supersedes', 'mitigates', 'waives', 'incurs', 'resolves', 'corrects')",
            name="ck_governance_record_relations_type",
        ),
        CheckConstraint(
            "status in ('candidate', 'verified', 'rejected', 'stale')", name="ck_governance_record_relations_status"
        ),
        CheckConstraint(
            "valid_to is null or valid_from is null or valid_to >= valid_from",
            name="ck_governance_record_relations_valid_range",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_record_id: Mapped[str] = mapped_column(
        ForeignKey("governance_records.id", ondelete="CASCADE"), nullable=False
    )
    target_record_id: Mapped[str] = mapped_column(
        ForeignKey("governance_records.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    provenance: Mapped[dict] = mapped_column(JsonDict, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
