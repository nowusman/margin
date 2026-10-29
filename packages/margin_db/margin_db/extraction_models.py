from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from margin_schema.ontology import ONTOLOGY_VERSION

from .base import Base, JsonDict, JsonList, utc_now


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'completed', 'completed_with_issues', 'failed')", name="ck_extraction_runs_status"
        ),
        CheckConstraint("expected_window_count >= 0", name="ck_extraction_runs_expected_windows"),
        CheckConstraint("processed_window_count >= 0", name="ck_extraction_runs_processed_windows"),
        CheckConstraint("input_tokens >= 0", name="ck_extraction_runs_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="ck_extraction_runs_output_tokens"),
        Index("ix_extraction_runs_source_document_id", "source_document_id"),
        Index("ix_extraction_runs_source_version_id", "source_version_id"),
        Index("ix_extraction_runs_ingestion_job_id", "ingestion_job_id"),
        Index("ix_extraction_runs_status", "status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    ingestion_job_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="SET NULL"))
    source_document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_document_versions.id"), nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    extractor: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    prompt_hash: Mapped[str | None] = mapped_column(Text)
    ontology_version: Mapped[str] = mapped_column(Text, nullable=False, default=ONTOLOGY_VERSION)
    expected_window_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_window_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list] = mapped_column(JsonList, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SpanExtractionAttempt(Base):
    __tablename__ = "span_extraction_attempts"
    __table_args__ = (
        CheckConstraint(
            "outcome in ('extracted', 'no_claim', 'failed', 'deferred', 'unsupported', 'duplicate')",
            name="ck_span_extraction_attempts_outcome",
        ),
        CheckConstraint("window_index >= 0", name="ck_span_extraction_attempts_window_index"),
        CheckConstraint("attempt_count >= 1", name="ck_span_extraction_attempts_attempt_count"),
        CheckConstraint("input_tokens >= 0", name="ck_span_extraction_attempts_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="ck_span_extraction_attempts_output_tokens"),
        UniqueConstraint(
            "extraction_run_id", "window_id", "source_element_id", name="uq_span_extraction_attempts_run_window_element"
        ),
        Index("ix_span_extraction_attempts_run_id", "extraction_run_id"),
        Index("ix_span_extraction_attempts_element_id", "source_element_id"),
        Index("ix_span_extraction_attempts_outcome", "outcome"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    extraction_run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"), nullable=False)
    source_element_id: Mapped[str] = mapped_column(ForeignKey("source_elements.id", ondelete="CASCADE"), nullable=False)
    window_id: Mapped[str] = mapped_column(Text, nullable=False)
    window_index: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    extractor: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    prompt_hash: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list] = mapped_column(JsonList, default=list)
    output_idea_block_ids: Mapped[list] = mapped_column(JsonList, default=list)
    output_entity_ids: Mapped[list] = mapped_column(JsonList, default=list)
    output_relationship_ids: Mapped[list] = mapped_column(JsonList, default=list)
    context_element_ids: Mapped[list] = mapped_column(JsonList, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
