from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import workers.main as worker_main
import workers.tasks as tasks
from margin_db.base import Base
from margin_db.source_models import (
    INGESTION_JOB_COMPLETED,
    INGESTION_JOB_CONVERTING,
    INGESTION_JOB_RECEIVED,
    IngestionJob,
)
from workers.extract.inference import InferenceUnavailableError


class FakeSession:
    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_worker_consumes_only_the_unified_lifecycle_queues() -> None:
    assert worker_main.WORKER_QUEUES == ("ingestion", "extraction", "embedding", "review", "maintenance", "export")
    registered = set(tasks.celery_app.tasks)
    assert {
        "margin.ingest.process_job",
        "margin.extract.run_pipeline",
        "margin.embedding.generate_records",
        "margin.review.route_ingestion_job",
        "margin.maintenance.health_check",
        "margin.export.domain",
    } <= registered
    assert not {"margin.compile.entity", "margin.health.daily_check"} & registered


def test_worker_stage_tasks_use_the_parent_ingestion_job_id() -> None:
    assert tuple(inspect.signature(tasks.process_ingestion_job.run).parameters) == ("ingestion_job_id",)
    assert tuple(inspect.signature(tasks.run_extract_pipeline.run).parameters) == ("ingestion_job_id",)
    assert tuple(inspect.signature(tasks.route_ingestion_review.run).parameters) == ("ingestion_job_id",)
    assert "ingestion_job_id" in inspect.signature(tasks.generate_embedding_records.run).parameters


def test_llm_extraction_pipeline_fails_fast_when_model_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "configured_active_model", lambda session, purpose: None)

    with pytest.raises(InferenceUnavailableError):
        tasks._extraction_pipeline(lambda: FakeSession(), extraction_mode="llm")


def test_maintenance_health_report_contains_only_retained_health_signals() -> None:
    report = tasks.MaintenanceHealthReport(
        hygiene_findings=1,
        relationship_consistency_findings=2,
        graph_hygiene_findings=3,
        known_unknowns=4,
        contradiction_tasks=5,
        blind_spot_tasks=6,
        review_tasks_created=7,
    )

    assert report.to_dict() == {
        "hygiene_findings": 1,
        "relationship_consistency_findings": 2,
        "graph_hygiene_findings": 3,
        "known_unknowns": 4,
        "contradiction_tasks": 5,
        "blind_spot_tasks": 6,
        "review_tasks_created": 7,
        "ontology_records_reevaluated": 0,
        "ontology_records_withdrawn": 0,
        "stalled_jobs_resumed": 0,
    }
    assert "adapter_contracts" not in report.to_dict()


def test_stalled_ingestion_recovery_is_bounded_and_idempotent() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    stale_at = now - tasks._STALLED_INGESTION_AFTER - timedelta(seconds=1)

    with Session(engine) as session:
        session.add_all(
            (
                IngestionJob(id="job-received", status=INGESTION_JOB_RECEIVED, updated_at=stale_at),
                IngestionJob(id="job-converting", status=INGESTION_JOB_CONVERTING, updated_at=stale_at),
                IngestionJob(id="job-fresh", status=INGESTION_JOB_RECEIVED, updated_at=now),
                IngestionJob(id="job-completed", status=INGESTION_JOB_COMPLETED, updated_at=stale_at),
            )
        )
        session.flush()

        requests = tasks._stalled_ingestion_recovery_requests(session, now=now)

        assert requests == ((INGESTION_JOB_CONVERTING, "job-converting"), (INGESTION_JOB_RECEIVED, "job-received"))
        recovered_job = session.get(IngestionJob, "job-received")
        assert recovered_job is not None
        assert recovered_job.metadata_["recovery"]["stage"] == INGESTION_JOB_RECEIVED
        session.flush()
        assert tasks._stalled_ingestion_recovery_requests(session, now=now) == ()
