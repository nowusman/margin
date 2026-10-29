from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, JsonDict, utc_now
from margin_schema.ontology import ONTOLOGY_VERSION


class EntityRelationship(Base):
    __tablename__ = "entity_relationships"
    __table_args__ = (
        Index("ix_entity_relationships_created_at", "created_at"),
        Index("ix_entity_relationships_source_entity_id", "source_entity_id"),
        Index("ix_entity_relationships_target_entity_id", "target_entity_id"),
        Index("ix_entity_relationships_status", "status"),
        Index("ix_entity_relationships_source_type_status", "source_entity_id", "relation_type", "status", "valid_to"),
        Index("ix_entity_relationships_target_type_status", "target_entity_id", "relation_type", "status", "valid_to"),
        Index(
            "uq_entity_relationships_semantic_scope",
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            unique=True,
        ),
        CheckConstraint(
            "status in ('candidate', 'verified', 'rejected', 'stale')", name="ck_entity_relationships_status"
        ),
        CheckConstraint("source_entity_id <> target_entity_id", name="ck_entity_relationships_distinct_endpoints"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    target_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    ontology_version: Mapped[str] = mapped_column(Text, nullable=False, default=ONTOLOGY_VERSION)
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RelationshipProvenance(Base):
    __tablename__ = "relationship_provenance"
    __table_args__ = (
        Index("ix_relationship_provenance_relationship_id", "relationship_id"),
        Index("ix_relationship_provenance_idea_block_id", "idea_block_id"),
        Index("ix_relationship_provenance_source_element_id", "source_element_id"),
        CheckConstraint("polarity in ('supports', 'refutes')", name="ck_relationship_provenance_polarity"),
        CheckConstraint(
            "status in ('candidate', 'verified', 'rejected', 'stale')", name="ck_relationship_provenance_status"
        ),
        CheckConstraint(
            "valid_to is null or valid_from is null or valid_to >= valid_from",
            name="ck_relationship_provenance_valid_range",
        ),
        CheckConstraint(
            "status NOT IN ('candidate', 'verified') "
            "OR (source_quote IS NOT NULL AND length(trim(source_quote)) > 0)",
            name="ck_relationship_provenance_quote_present",
        ),
        CheckConstraint(
            "status NOT IN ('candidate', 'verified') "
            "OR (extraction_method IS NOT NULL AND length(trim(extraction_method)) > 0)",
            name="ck_relationship_provenance_extraction_method",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    relationship_id: Mapped[str] = mapped_column(ForeignKey("entity_relationships.id"), nullable=False)
    idea_block_id: Mapped[str] = mapped_column(ForeignKey("idea_blocks.id"), nullable=False)
    source_element_id: Mapped[str] = mapped_column(ForeignKey("source_elements.id", ondelete="CASCADE"), nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_method: Mapped[str | None] = mapped_column(Text)
    source_quote: Mapped[str | None] = mapped_column(Text)
    raw_predicate: Mapped[str | None] = mapped_column(Text)
    polarity: Mapped[str] = mapped_column(Text, nullable=False, default="supports")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    qualifiers: Mapped[dict] = mapped_column(JsonDict, default=dict)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
