from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    FetchedValue,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from margin_db.base import Base, JsonDict, JsonList, PgVector, utc_now
from margin_schema.ontology import ONTOLOGY_VERSION


class IdeaBlock(Base):
    __tablename__ = "idea_blocks"
    __table_args__ = (
        Index("ix_idea_blocks_created_at", "created_at"),
        Index("ix_idea_blocks_domain_id", "domain_id"),
        Index("ix_idea_blocks_search_scope", "domain_id", "current_revision_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id"), nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("idea_block_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    current_revision: Mapped[IdeaBlockRevision | None] = relationship(
        "IdeaBlockRevision", foreign_keys=[current_revision_id], uselist=False, post_update=True
    )
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey("idea_blocks.id", ondelete="RESTRICT"))
    tombstoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tombstone_reason: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archive_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class IdeaBlockRevision(Base):
    __tablename__ = "idea_block_revisions"
    __table_args__ = (
        UniqueConstraint("idea_block_id", "revision_number", name="uq_idea_block_revisions_number"),
        Index(
            "uq_idea_block_revisions_verified_semantic_key",
            "semantic_key_version",
            "semantic_key",
            unique=True,
            postgresql_where=text("publication_status = 'verified' AND semantic_key IS NOT NULL"),
            sqlite_where=text("publication_status = 'verified' AND semantic_key IS NOT NULL"),
        ),
        Index("ix_idea_block_revisions_block_status", "idea_block_id", "publication_status"),
        Index("ix_idea_block_revisions_search_status_validity", "publication_status", "valid_from", "valid_to", "id"),
        Index("ix_idea_block_revisions_search_vector_gin", "search_vector", postgresql_using="gin"),
        Index("ix_idea_block_revisions_statement", "normalized_statement"),
        CheckConstraint(
            "publication_status in ('candidate', 'verified', 'duplicate', 'stale', 'rejected', 'superseded')",
            name="ck_idea_block_revisions_publication_status",
        ),
        CheckConstraint("revision_number > 0", name="ck_idea_block_revisions_number_positive"),
        CheckConstraint(
            "valid_to is null or valid_from is null or valid_to >= valid_from",
            name="ck_idea_block_revisions_valid_range",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    idea_block_id: Mapped[str] = mapped_column(ForeignKey("idea_blocks.id", ondelete="CASCADE"), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_statement: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR().with_variant(Text(), "sqlite"),
        nullable=True,
        # Migration 0009 replaces this with a PostgreSQL stored generated
        # column. FetchedValue keeps the ORM from inserting an explicit NULL
        # while preserving SQLite's lightweight test schema.
        server_default=FetchedValue(),
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    polarity: Mapped[str] = mapped_column(Text, nullable=False, default="supports")
    qualifiers: Mapped[dict] = mapped_column(JsonDict, default=dict)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    publication_status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    semantic_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    semantic_key_version: Mapped[str] = mapped_column(Text, nullable=False, default="semantic-key-v1")
    ontology_version: Mapped[str] = mapped_column(Text, nullable=False, default=ONTOLOGY_VERSION)
    actor: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    process: Mapped[str] = mapped_column(Text, nullable=False, default="extraction")
    change_reason: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IdeaBlockRelation(Base):
    __tablename__ = "idea_block_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_idea_block_id",
            "target_idea_block_id",
            "relation_type",
            name="uq_idea_block_relations_endpoints_type",
        ),
        Index("ix_idea_block_relations_source", "source_idea_block_id"),
        Index("ix_idea_block_relations_target", "target_idea_block_id"),
        Index("ix_idea_block_relations_status", "status"),
        Index(
            "ix_idea_block_relations_source_type_status", "source_idea_block_id", "relation_type", "status", "valid_to"
        ),
        Index(
            "ix_idea_block_relations_target_type_status", "target_idea_block_id", "relation_type", "status", "valid_to"
        ),
        CheckConstraint(
            "relation_type in ('contradicts', 'equivalent', 'refines', 'supersedes', "
            "'superseded_by', 'semantic_related')",
            name="ck_idea_block_relations_type",
        ),
        CheckConstraint("direction in ('directed', 'symmetric')", name="ck_idea_block_relations_direction"),
        CheckConstraint(
            "status in ('candidate', 'verified', 'rejected', 'stale')", name="ck_idea_block_relations_status"
        ),
        CheckConstraint("confidence >= 0 and confidence <= 1", name="ck_idea_block_relations_confidence"),
        CheckConstraint(
            "valid_to is null or valid_from is null or valid_to >= valid_from",
            name="ck_idea_block_relations_valid_range",
        ),
        CheckConstraint(
            "relation_type not in ('contradicts', 'equivalent') or "
            "(direction = 'symmetric' and source_idea_block_id < target_idea_block_id)",
            name="ck_idea_block_relations_contradiction_canonical",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_idea_block_id: Mapped[str] = mapped_column(ForeignKey("idea_blocks.id", ondelete="CASCADE"), nullable=False)
    target_idea_block_id: Mapped[str] = mapped_column(ForeignKey("idea_blocks.id", ondelete="CASCADE"), nullable=False)
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False, default="directed")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0"))
    decision_provenance: Mapped[dict] = mapped_column(JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class IdeaBlockEntity(Base):
    __tablename__ = "idea_block_entities"
    __table_args__ = (Index("ix_idea_block_entities_entity_id", "entity_id"),)

    idea_block_id: Mapped[str] = mapped_column(ForeignKey("idea_blocks.id"), primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True, default="mentions")


class EmbeddingRecord(Base):
    __tablename__ = "embedding_records"
    __table_args__ = (
        UniqueConstraint(
            "target_type", "target_id", "model_id", "index_version", name="uq_embedding_records_target_model_index"
        ),
        CheckConstraint(
            "target_type in ('source_element', 'idea_block_revision')", name="ck_embedding_records_target_type"
        ),
        CheckConstraint("dimensions = 384", name="ck_embedding_records_dimensions"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    idea_block_revision_id: Mapped[str | None] = mapped_column(ForeignKey("idea_block_revisions.id"))
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    index_version: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(PgVector, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    provider_id: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class EmbeddingJob(Base):
    __tablename__ = "embedding_jobs"
    __table_args__ = (
        Index(
            "uq_embedding_jobs_pending_target",
            "target_type",
            "target_id",
            "model_id",
            "index_version",
            unique=True,
            postgresql_where=text("status in ('queued', 'processing')"),
            sqlite_where=text("status in ('queued', 'processing')"),
        ),
        CheckConstraint(
            "target_type in ('source_element', 'idea_block_revision')", name="ck_embedding_jobs_target_type"
        ),
        CheckConstraint("status in ('queued', 'processing', 'completed', 'failed')", name="ck_embedding_jobs_status"),
        Index("ix_embedding_jobs_ingestion_job_id", "ingestion_job_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    ingestion_job_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="SET NULL"))
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    idea_block_revision_id: Mapped[str | None] = mapped_column(ForeignKey("idea_block_revisions.id"))
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    index_version: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SearchMaterializationJob(Base):
    """Durable, affected-revision work for generated full-text search inputs."""

    __tablename__ = "search_materialization_jobs"
    __table_args__ = (
        Index(
            "uq_search_materialization_jobs_pending_revision",
            "idea_block_revision_id",
            unique=True,
            postgresql_where=text("status in ('queued', 'processing')"),
            sqlite_where=text("status in ('queued', 'processing')"),
        ),
        CheckConstraint(
            "status in ('queued', 'processing', 'completed', 'failed')", name="ck_search_materialization_jobs_status"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    idea_block_revision_id: Mapped[str] = mapped_column(
        ForeignKey("idea_block_revisions.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SearchEmbeddingAssignment(Base):
    """One active, fixed-dimension embedding assignment for ordinary search."""

    __tablename__ = "search_embedding_assignments"
    __table_args__ = (
        UniqueConstraint("model_id", "index_version", name="uq_search_embedding_assignments_model_index"),
        Index(
            "uq_search_embedding_assignments_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        CheckConstraint(
            "status in ('building', 'active', 'retired', 'failed')", name="ck_search_embedding_assignments_status"
        ),
        CheckConstraint("dimensions = 384", name="ck_search_embedding_assignments_dimensions"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    index_version: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="building")
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        Index(
            "uq_claim_evidence_document_location_polarity_run",
            "idea_block_revision_id",
            "source_element_id",
            text("COALESCE(quote_char_start, -1)"),
            text("COALESCE(quote_char_end, -1)"),
            "polarity",
            text("COALESCE(extraction_run_id, '')"),
            unique=True,
            postgresql_where=text("source_element_id IS NOT NULL"),
            sqlite_where=text("source_element_id IS NOT NULL"),
        ),
        Index(
            "uq_claim_evidence_governance_revision_polarity",
            "idea_block_revision_id",
            "governance_record_revision_id",
            "polarity",
            unique=True,
            postgresql_where=text("governance_record_revision_id IS NOT NULL"),
            sqlite_where=text("governance_record_revision_id IS NOT NULL"),
        ),
        CheckConstraint("polarity in ('supports', 'refutes')", name="ck_claim_evidence_polarity"),
        CheckConstraint("status in ('active', 'stale', 'rejected')", name="ck_claim_evidence_status"),
        CheckConstraint("evidence_kind in ('verbatim', 'derived_visual')", name="ck_claim_evidence_evidence_kind"),
        CheckConstraint(
            "valid_to is null or valid_from is null or valid_to >= valid_from", name="ck_claim_evidence_valid_range"
        ),
        CheckConstraint(
            "(source_element_id is not null and governance_record_revision_id is null) "
            "or (source_element_id is null and governance_record_revision_id is not null)",
            name="ck_claim_evidence_exactly_one_origin",
        ),
        Index("ix_claim_evidence_idea_block_id", "idea_block_id"),
        Index("ix_claim_evidence_source_element_id", "source_element_id"),
        Index("ix_claim_evidence_governance_revision_id", "governance_record_revision_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    idea_block_id: Mapped[str] = mapped_column(ForeignKey("idea_blocks.id"), nullable=False)
    idea_block_revision_id: Mapped[str] = mapped_column(
        ForeignKey("idea_block_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    source_element_id: Mapped[str | None] = mapped_column(ForeignKey("source_elements.id", ondelete="CASCADE"))
    governance_record_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("governance_record_revisions.id", ondelete="RESTRICT")
    )
    extraction_run_id: Mapped[str | None] = mapped_column(ForeignKey("extraction_runs.id"))
    source_quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_char_start: Mapped[int | None] = mapped_column(Integer)
    quote_char_end: Mapped[int | None] = mapped_column(Integer)
    evidence_kind: Mapped[str] = mapped_column(Text, nullable=False, default="verbatim")
    polarity: Mapped[str] = mapped_column(Text, nullable=False, default="supports")
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_method: Mapped[str] = mapped_column(Text, nullable=False)
    qualifiers: Mapped[dict] = mapped_column(JsonDict, default=dict)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReprocessingDiff(Base):
    __tablename__ = "reprocessing_diffs"
    __table_args__ = (Index("ix_reprocessing_diffs_source_document_id", "source_document_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    previous_version_id: Mapped[str | None] = mapped_column(ForeignKey("source_document_versions.id"))
    current_version_id: Mapped[str] = mapped_column(ForeignKey("source_document_versions.id"), nullable=False)
    extraction_run_id: Mapped[str | None] = mapped_column(ForeignKey("extraction_runs.id"))
    added_claim_ids: Mapped[list] = mapped_column(JsonList, default=list)
    removed_claim_ids: Mapped[list] = mapped_column(JsonList, default=list)
    modified_claims: Mapped[list] = mapped_column(JsonList, default=list)
    changed_entity_resolutions: Mapped[list] = mapped_column(JsonList, default=list)
    added_relationship_ids: Mapped[list] = mapped_column(JsonList, default=list)
    removed_relationship_ids: Mapped[list] = mapped_column(JsonList, default=list)
    newly_unresolved: Mapped[list] = mapped_column(JsonList, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
