from __future__ import annotations

from margin_db.base import Base
from margin_db.source_models import (
    DUBLIN_CORE_FIELDS,
    INGESTION_JOB_STATUSES,
    SOURCE_DOCUMENT_STATUSES,
    SOURCE_METADATA_KEYS,
)


def columns(table_name: str) -> set[str]:
    return {column.name for column in Base.metadata.tables[table_name].columns}


def test_source_ingestion_tables_are_registered() -> None:
    assert {"ingestion_jobs", "conversion_quality_reports"} <= set(Base.metadata.tables)


def test_source_metadata_columns_exist() -> None:
    for table_name in ("source_documents", "source_document_versions"):
        assert {"metadata", "dublin_core"} <= columns(table_name)


def test_ingestion_job_columns_exist() -> None:
    assert {
        "id",
        "source_document_id",
        "source_version_id",
        "status",
        "retry_count",
        "max_retries",
        "next_retry_at",
        "error_message",
        "metadata",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    } <= columns("ingestion_jobs")


def test_ingestion_execution_children_link_to_the_parent_job() -> None:
    assert {"ingestion_job_id"} <= columns("extraction_runs")
    assert {"ingestion_job_id"} <= columns("embedding_jobs")


def test_conversion_quality_report_columns_exist() -> None:
    assert {
        "id",
        "source_document_id",
        "source_version_id",
        "text_coverage",
        "table_coverage",
        "image_diagram_detected",
        "ocr_needed",
        "section_tree_confidence",
        "warnings",
        "metadata",
        "created_at",
    } <= columns("conversion_quality_reports")


def test_source_ingestion_status_values_are_named_in_models() -> None:
    assert {
        "received",
        "converting",
        "extracting",
        "resolving",
        "embedding",
        "review_required",
        "completed",
        "failed",
        "quarantined",
    } == set(INGESTION_JOB_STATUSES)
    assert {"uploaded", "ingested", "converting", "indexed", "extraction_ready", "failed"} <= set(
        SOURCE_DOCUMENT_STATUSES
    )


def test_source_metadata_contract_names_are_exposed() -> None:
    assert {"title", "creator", "date", "format", "page_count", "language", "document_type_hints"} <= set(
        SOURCE_METADATA_KEYS
    )
    assert {
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
    } == set(DUBLIN_CORE_FIELDS)
