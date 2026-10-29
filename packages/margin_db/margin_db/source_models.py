from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, JsonDict, JsonList, utc_now

INGESTION_JOB_RECEIVED = "received"
INGESTION_JOB_CONVERTING = "converting"
INGESTION_JOB_EXTRACTING = "extracting"
INGESTION_JOB_RESOLVING = "resolving"
INGESTION_JOB_EMBEDDING = "embedding"
INGESTION_JOB_REVIEW_REQUIRED = "review_required"
INGESTION_JOB_COMPLETED = "completed"
INGESTION_JOB_FAILED = "failed"
INGESTION_JOB_QUARANTINED = "quarantined"

INGESTION_JOB_STATUSES = (
    INGESTION_JOB_RECEIVED,
    INGESTION_JOB_CONVERTING,
    INGESTION_JOB_EXTRACTING,
    INGESTION_JOB_RESOLVING,
    INGESTION_JOB_EMBEDDING,
    INGESTION_JOB_REVIEW_REQUIRED,
    INGESTION_JOB_COMPLETED,
    INGESTION_JOB_FAILED,
    INGESTION_JOB_QUARANTINED,
)

INGESTION_JOB_TRANSITIONS: dict[str, tuple[str, ...]] = {
    INGESTION_JOB_RECEIVED: (INGESTION_JOB_CONVERTING, INGESTION_JOB_FAILED, INGESTION_JOB_QUARANTINED),
    INGESTION_JOB_CONVERTING: (INGESTION_JOB_EXTRACTING, INGESTION_JOB_FAILED, INGESTION_JOB_QUARANTINED),
    INGESTION_JOB_EXTRACTING: (INGESTION_JOB_RESOLVING, INGESTION_JOB_FAILED, INGESTION_JOB_QUARANTINED),
    INGESTION_JOB_RESOLVING: (
        INGESTION_JOB_EMBEDDING,
        INGESTION_JOB_REVIEW_REQUIRED,
        INGESTION_JOB_FAILED,
        INGESTION_JOB_QUARANTINED,
    ),
    INGESTION_JOB_EMBEDDING: (
        INGESTION_JOB_COMPLETED,
        INGESTION_JOB_REVIEW_REQUIRED,
        INGESTION_JOB_FAILED,
        INGESTION_JOB_QUARANTINED,
    ),
    INGESTION_JOB_FAILED: (INGESTION_JOB_RECEIVED,),
    INGESTION_JOB_QUARANTINED: (INGESTION_JOB_RECEIVED,),
}

INGESTION_JOB_TERMINAL_STATUSES = (
    INGESTION_JOB_COMPLETED,
    INGESTION_JOB_REVIEW_REQUIRED,
    INGESTION_JOB_FAILED,
    INGESTION_JOB_QUARANTINED,
)

SOURCE_DOCUMENT_UPLOADED = "uploaded"
SOURCE_DOCUMENT_INGESTED = "ingested"
SOURCE_DOCUMENT_CONVERTING = "converting"
SOURCE_DOCUMENT_INDEXED = "indexed"
SOURCE_DOCUMENT_EXTRACTION_READY = "extraction_ready"
SOURCE_DOCUMENT_KNOWLEDGE_READY = "knowledge_ready"
SOURCE_DOCUMENT_REVIEW_REQUIRED = "review_required"
SOURCE_DOCUMENT_FAILED = "failed"

SOURCE_DOCUMENT_STATUSES = (
    SOURCE_DOCUMENT_UPLOADED,
    SOURCE_DOCUMENT_INGESTED,
    SOURCE_DOCUMENT_CONVERTING,
    SOURCE_DOCUMENT_INDEXED,
    SOURCE_DOCUMENT_EXTRACTION_READY,
    SOURCE_DOCUMENT_KNOWLEDGE_READY,
    SOURCE_DOCUMENT_REVIEW_REQUIRED,
    SOURCE_DOCUMENT_FAILED,
)

SOURCE_METADATA_KEYS = ("title", "creator", "date", "format", "page_count", "language", "document_type_hints")

DUBLIN_CORE_FIELDS = (
    "title",
    "creator",
    "subject",
    "description",
    "publisher",
    "contributor",
    "date",
    "type",
    "format",
    "identifier",
    "source",
    "language",
    "relation",
    "coverage",
    "rights",
)


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        Index("ix_source_documents_category_id", "category_id"),
        Index("ix_source_documents_sha256", "sha256"),
        Index(
            "uq_source_documents_domain_logical_identity",
            "domain_id",
            "logical_identity",
            unique=True,
            postgresql_where=text("logical_identity IS NOT NULL"),
            sqlite_where=text("logical_identity IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)
    logical_identity: Mapped[str | None] = mapped_column(Text)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None]
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id"), nullable=False)
    category_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), nullable=False)
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False, default="internal")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ingested")
    owner: Mapped[str | None] = mapped_column(Text)
    steward: Mapped[str | None] = mapped_column(Text)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    steward_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    dublin_core: Mapped[dict] = mapped_column(JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ingested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class SourceDocumentVersion(Base):
    __tablename__ = "source_document_versions"
    __table_args__ = (
        CheckConstraint("status in ('active', 'superseded')", name="ck_source_document_versions_status"),
        Index("ix_source_document_versions_source_document_id", "source_document_id"),
        Index(
            "uq_source_document_versions_one_active",
            "source_document_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    version_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    converter_name: Mapped[str | None] = mapped_column(Text)
    converter_version: Mapped[str | None] = mapped_column(Text)
    converted_object_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    superseded_by_version_id: Mapped[str | None] = mapped_column(ForeignKey("source_document_versions.id"))
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    dublin_core: Mapped[dict] = mapped_column(JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "status in ('received', 'converting', 'extracting', 'resolving', 'embedding', "
            "'review_required', 'completed', 'failed', 'quarantined')",
            name="ck_ingestion_jobs_status",
        ),
        CheckConstraint("retry_count >= 0", name="ck_ingestion_jobs_retry_count"),
        CheckConstraint("max_retries >= 0", name="ck_ingestion_jobs_max_retries"),
        Index("ix_ingestion_jobs_source_sequence", "source_document_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: f"job_{uuid.uuid4().hex}")
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("source_documents.id"))
    source_version_id: Mapped[str | None] = mapped_column(ForeignKey("source_document_versions.id"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=INGESTION_JOB_RECEIVED, server_default=text("'received'")
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def can_retry(self) -> bool:
        retry_count = self.retry_count or 0
        max_retries = 3 if self.max_retries is None else self.max_retries
        return retry_count < max_retries

    def can_transition_to(self, next_status: str) -> bool:
        if next_status == self.status:
            return True
        if self.status in (INGESTION_JOB_FAILED, INGESTION_JOB_QUARANTINED) and next_status == INGESTION_JOB_RECEIVED:
            return self.can_retry()
        return next_status in INGESTION_JOB_TRANSITIONS.get(self.status, ())

    def transition_to(
        self,
        next_status: str,
        *,
        error_message: str | None = None,
        next_retry_at: datetime | None = None,
        at: datetime | None = None,
    ) -> None:
        if next_status not in INGESTION_JOB_STATUSES:
            raise ValueError(f"Unknown ingestion job status: {next_status}")
        if not self.can_transition_to(next_status):
            raise ValueError(f"Cannot transition ingestion job from {self.status!r} to {next_status!r}")

        changed_at = at or utc_now()
        previous_status = self.status
        self.status = next_status
        self.updated_at = changed_at

        if next_status == INGESTION_JOB_CONVERTING and self.started_at is None:
            self.started_at = changed_at
        if next_status in INGESTION_JOB_TERMINAL_STATUSES:
            self.completed_at = changed_at
        if next_status in (INGESTION_JOB_FAILED, INGESTION_JOB_QUARANTINED):
            self.error_message = error_message
            self.next_retry_at = next_retry_at
        if (
            previous_status in (INGESTION_JOB_FAILED, INGESTION_JOB_QUARANTINED)
            and next_status == INGESTION_JOB_RECEIVED
        ):
            self.retry_count = (self.retry_count or 0) + 1
            self.next_retry_at = next_retry_at
            self.completed_at = None


class ConversionQualityReport(Base):
    __tablename__ = "conversion_quality_reports"
    __table_args__ = (
        CheckConstraint(
            "text_coverage is null or (text_coverage >= 0 and text_coverage <= 1)",
            name="ck_conversion_quality_text_coverage",
        ),
        CheckConstraint(
            "table_coverage is null or (table_coverage >= 0 and table_coverage <= 1)",
            name="ck_conversion_quality_table_coverage",
        ),
        CheckConstraint(
            "section_tree_confidence is null or " "(section_tree_confidence >= 0 and section_tree_confidence <= 1)",
            name="ck_conversion_quality_section_tree_confidence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_document_versions.id"), nullable=False, unique=True
    )
    text_coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    table_coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    image_diagram_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ocr_needed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    section_tree_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    warnings: Mapped[list] = mapped_column(JsonList, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourcePage(Base):
    __tablename__ = "source_pages"
    __table_args__ = (
        UniqueConstraint("source_version_id", "page_number", name="uq_source_pages_version_number"),
        CheckConstraint(
            "classification in ('text_extracted', 'ocr_required', 'non_content', 'unsupported')",
            name="ck_source_pages_classification",
        ),
        Index("ix_source_pages_source_version_id", "source_version_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_document_versions.id"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceElement(Base):
    __tablename__ = "source_elements"
    __table_args__ = (
        UniqueConstraint("source_version_id", "ordinal", name="uq_source_elements_version_ordinal"),
        Index(
            "uq_source_elements_version_native",
            "source_version_id",
            "native_element_id",
            unique=True,
            postgresql_where=text("native_element_id IS NOT NULL"),
            sqlite_where=text("native_element_id IS NOT NULL"),
        ),
        Index("ix_source_elements_source_version_id", "source_version_id"),
        Index("ix_source_elements_source_page_id", "source_page_id"),
        Index("ix_source_elements_parent_element_id", "parent_element_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_document_versions.id", ondelete="CASCADE"), nullable=False
    )
    source_page_id: Mapped[str | None] = mapped_column(ForeignKey("source_pages.id", ondelete="CASCADE"))
    parent_element_id: Mapped[str | None] = mapped_column(ForeignKey("source_elements.id", ondelete="CASCADE"))
    native_element_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    bounding_box: Mapped[dict | None] = mapped_column(JsonDict)
    asset_key: Mapped[str | None] = mapped_column(Text)
    converter_method: Mapped[str | None] = mapped_column(Text)
    converter_version: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourcePageOutcome(Base):
    __tablename__ = "source_page_outcomes"
    __table_args__ = (
        UniqueConstraint("extraction_run_id", "source_page_id", name="uq_source_page_outcomes_run_page"),
        CheckConstraint(
            "outcome in (" "'extracted', 'verified_no_claim', 'intentionally_ignored', " "'quarantined', 'failed'" ")",
            name="ck_source_page_outcomes_outcome",
        ),
        CheckConstraint("outcome = 'extracted' or reason is not null", name="ck_source_page_outcomes_reason"),
        Index("ix_source_page_outcomes_run_id", "extraction_run_id"),
        Index("ix_source_page_outcomes_page_id", "source_page_id"),
        Index("ix_source_page_outcomes_idempotency_key", "idempotency_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    extraction_run_id: Mapped[str | None] = mapped_column(ForeignKey("extraction_runs.id"), nullable=True)
    source_page_id: Mapped[str] = mapped_column(ForeignKey("source_pages.id"), nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    processor: Mapped[str | None] = mapped_column(Text)
    processor_version: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceElementOutcome(Base):
    __tablename__ = "source_element_outcomes"
    __table_args__ = (
        UniqueConstraint("extraction_run_id", "source_element_id", name="uq_source_element_outcomes_run_element"),
        CheckConstraint(
            "outcome in (" "'extracted', 'verified_no_claim', 'intentionally_ignored', " "'quarantined', 'failed'" ")",
            name="ck_source_element_outcomes_outcome",
        ),
        CheckConstraint("outcome = 'extracted' or reason is not null", name="ck_source_element_outcomes_reason"),
        CheckConstraint(
            "outcome != 'verified_no_claim' or " "(verification_method is not null and verifier_version is not null)",
            name="ck_source_element_outcomes_verification",
        ),
        CheckConstraint(
            "audit_status in ('not_selected', 'selected', 'passed')", name="ck_source_element_outcomes_audit_status"
        ),
        Index("ix_source_element_outcomes_run_id", "extraction_run_id"),
        Index("ix_source_element_outcomes_element_id", "source_element_id"),
        Index("ix_source_element_outcomes_outcome", "outcome"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    extraction_run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"), nullable=False)
    source_element_id: Mapped[str] = mapped_column(ForeignKey("source_elements.id", ondelete="CASCADE"), nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    extractor: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    prompt_hash: Mapped[str | None] = mapped_column(Text)
    verification_method: Mapped[str | None] = mapped_column(Text)
    verifier_version: Mapped[str | None] = mapped_column(Text)
    attempt_metadata: Mapped[dict] = mapped_column(JsonDict, default=dict)
    audit_status: Mapped[str] = mapped_column(Text, nullable=False, default="not_selected")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class UnmappedExtraction(Base):
    __tablename__ = "unmapped_extractions"
    __table_args__ = (
        CheckConstraint("kind in ('claim', 'entity', 'relationship', 'field')", name="ck_unmapped_extractions_kind"),
        CheckConstraint("status in ('open', 'resolved', 'rejected')", name="ck_unmapped_extractions_status"),
        Index("ix_unmapped_extractions_source_document_id", "source_document_id"),
        Index("ix_unmapped_extractions_source_element_id", "source_element_id"),
        Index("ix_unmapped_extractions_status", "status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_document_versions.id"), nullable=False)
    source_element_id: Mapped[str] = mapped_column(ForeignKey("source_elements.id", ondelete="CASCADE"), nullable=False)
    extraction_run_id: Mapped[str | None] = mapped_column(ForeignKey("extraction_runs.id"))
    review_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("review_tasks.id"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JsonDict, default=dict)
    model_id: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    resolution: Mapped[dict] = mapped_column(JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
