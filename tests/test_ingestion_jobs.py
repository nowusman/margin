from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from margin_db.source_models import (
    INGESTION_JOB_COMPLETED,
    INGESTION_JOB_CONVERTING,
    INGESTION_JOB_EMBEDDING,
    INGESTION_JOB_EXTRACTING,
    INGESTION_JOB_FAILED,
    INGESTION_JOB_QUARANTINED,
    INGESTION_JOB_RECEIVED,
    INGESTION_JOB_RESOLVING,
    INGESTION_JOB_REVIEW_REQUIRED,
    INGESTION_JOB_STATUSES,
    IngestionJob,
)


def test_ingestion_job_accepts_the_complete_parent_lifecycle() -> None:
    received_at = datetime(2026, 5, 15, 9, 0, tzinfo=UTC)
    completed_at = datetime(2026, 5, 15, 9, 4, tzinfo=UTC)
    job = IngestionJob(status=INGESTION_JOB_RECEIVED)

    job.transition_to(INGESTION_JOB_CONVERTING, at=received_at)
    job.transition_to(INGESTION_JOB_EXTRACTING)
    job.transition_to(INGESTION_JOB_RESOLVING)
    job.transition_to(INGESTION_JOB_EMBEDDING)
    job.transition_to(INGESTION_JOB_COMPLETED, at=completed_at)

    assert job.status == INGESTION_JOB_COMPLETED
    assert job.started_at == received_at
    assert job.completed_at == completed_at
    assert job.updated_at == completed_at


def test_ingestion_job_review_and_quarantine_are_terminal_outcomes() -> None:
    review_job = IngestionJob(status=INGESTION_JOB_RESOLVING)
    review_job.transition_to(INGESTION_JOB_REVIEW_REQUIRED)
    quarantined_job = IngestionJob(status=INGESTION_JOB_EXTRACTING)
    quarantined_job.transition_to(INGESTION_JOB_QUARANTINED, error_message="invalid visual payload")

    assert review_job.completed_at is not None
    assert quarantined_job.completed_at is not None
    assert quarantined_job.error_message == "invalid visual payload"


def test_ingestion_job_rejects_a_skipped_lifecycle_stage() -> None:
    job = IngestionJob(status=INGESTION_JOB_RECEIVED)

    with pytest.raises(ValueError, match="Cannot transition"):
        job.transition_to(INGESTION_JOB_EMBEDDING)


def test_terminal_failure_retries_from_the_parent_without_resetting_audit_data() -> None:
    failed_at = datetime(2026, 5, 15, 9, 0, tzinfo=UTC)
    retry_at = failed_at + timedelta(minutes=5)
    job = IngestionJob(status=INGESTION_JOB_CONVERTING, max_retries=1, metadata_={"input_key": "source:v1"})

    job.transition_to(INGESTION_JOB_FAILED, error_message="converter failed", at=failed_at)
    job.transition_to(INGESTION_JOB_RECEIVED, at=retry_at)

    assert job.status == INGESTION_JOB_RECEIVED
    assert job.retry_count == 1
    assert job.completed_at is None
    assert job.error_message == "converter failed"
    assert job.metadata_ == {"input_key": "source:v1"}

    job.transition_to(INGESTION_JOB_QUARANTINED, error_message="manual repair required")
    with pytest.raises(ValueError, match="Cannot transition"):
        job.transition_to(INGESTION_JOB_RECEIVED)


def test_ingestion_job_statuses_are_the_unified_contract() -> None:
    assert set(INGESTION_JOB_STATUSES) == {
        "received",
        "converting",
        "extracting",
        "resolving",
        "embedding",
        "review_required",
        "completed",
        "failed",
        "quarantined",
    }
