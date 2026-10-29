from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import workers.extract.inference as extractor_inference
from margin_db.audit import write_audit_event
from margin_db.embeddings import (
    EmbeddingTargetType,
    enqueue_embedding_job,
    process_embedding_jobs,
    required_embedding_index_config,
)
from margin_db.entity_catalog import reresolve_affected_entities
from margin_db.entity_models import EntityReresolutionJob
from margin_db.export import complete_export, fail_export
from margin_db.extraction_models import ExtractionRun
from margin_db.governance import (
    create_review_tasks_for_findings,
    detect_blind_spots,
    detect_contradiction_candidates,
    run_hygiene_lint,
)
from margin_db.graph_hygiene import run_graph_hygiene_lint
from margin_db.knowledge_models import ClaimEvidence, EmbeddingJob, IdeaBlock, IdeaBlockRevision
from margin_db.known_unknowns import create_known_unknown_tasks, detect_known_unknowns
from margin_db.model_registry import configured_active_model
from margin_db.object_store_factory import create_object_store
from margin_db.ontology_review import reevaluate_ontology_records
from margin_db.relationship_consistency import run_relationship_consistency_lint
from margin_db.search_materialization import activate_search_embedding_assignment, process_search_materialization_jobs
from margin_db.semantic_lint import run_semantic_lint
from margin_db.session import make_engine, make_session_factory, session_scope
from margin_db.source_ingestion import SourceIngestionService, set_job_status
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
    INGESTION_JOB_TERMINAL_STATUSES,
    IngestionJob,
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
)
from margin_embedding.factory import create_embedding_provider
from margin_embedding.interfaces import EmbeddingIndexConfig, EmbeddingProvider
from workers.celery_app import celery_app
from workers.extract.accounting import record_failed_extraction_run
from workers.extract.pipeline import MarginIdeaBlockPipeline

logger = logging.getLogger(__name__)

_ENGINE = None
_SESSION_FACTORY = None
_EMBEDDING_PROVIDER = None
_STALLED_INGESTION_AFTER = timedelta(minutes=10)
_STALLED_INGESTION_LIMIT = 100


def _session_factory() -> sessionmaker[Session]:
    global _ENGINE, _SESSION_FACTORY
    if _SESSION_FACTORY is not None:
        return _SESSION_FACTORY
    database_url = os.getenv("MARGIN_DATABASE_URL", "postgresql+psycopg://margin:margin@localhost:5432/margin")
    _ENGINE = make_engine(database_url)
    _SESSION_FACTORY = make_session_factory(_ENGINE)
    return _SESSION_FACTORY


def _ingestion_service() -> SourceIngestionService:
    return SourceIngestionService(create_object_store())


def _extraction_pipeline(
    session_factory: sessionmaker[Session], *, extraction_mode: str = "deterministic"
) -> MarginIdeaBlockPipeline:
    if extraction_mode == "deterministic":
        return MarginIdeaBlockPipeline(extraction_mode=extraction_mode)
    with session_scope(session_factory) as session:
        model = configured_active_model(session, "extraction")
    if model is None:
        raise extractor_inference.InferenceUnavailableError(
            "LLM extraction requested but no active extraction model is configured"
        )
    if model.last_test_status != "healthy":
        raise extractor_inference.InferenceUnavailableError(
            "LLM extraction requested but the active extraction model has no successful health test"
        )
    return MarginIdeaBlockPipeline(
        extraction_mode=extraction_mode,
        inference_extractor=extractor_inference.OpenAICompatibleExtractor(
            base_url=model.endpoint_url,
            model=model.model_name,
            secret_reference=model.secret_reference,
            temperature=model.temperature_default,
            max_tokens=model.max_tokens,
            system_prompt=model.system_prompt,
        ),
    )


def _embedding_provider():
    global _EMBEDDING_PROVIDER
    if _EMBEDDING_PROVIDER is None:
        _EMBEDDING_PROVIDER = create_embedding_provider()
    return _EMBEDDING_PROVIDER


@celery_app.task(name="margin.ingest.process_job", queue="ingestion")
def process_ingestion_job(ingestion_job_id: str) -> dict[str, object]:
    start = datetime.now(UTC)
    request_payload: dict[str, object] = {"ingestion_job_id": ingestion_job_id}
    extraction_request: str | None = None
    session_factory = _session_factory()
    service = _ingestion_service()
    try:
        with session_scope(session_factory) as session:
            job = service.process_job(session, ingestion_job_id)
            payload: dict[str, object] = {
                "ingestion_job_id": str(job.id),
                "source_document_id": job.source_document_id,
                "status": job.status,
            }
            if job.status == INGESTION_JOB_EXTRACTING:
                extraction_request = str(job.id)
    except Exception as exc:
        _log_task_interaction(
            event="process_ingestion_job",
            request_payload=request_payload,
            response_payload=None,
            start=start,
            error=str(exc),
        )
        raise

    if extraction_request is not None:
        task = run_extract_pipeline.delay(extraction_request)
        payload["extraction_task_id"] = str(task.id)
    _log_task_interaction(
        event="process_ingestion_job", request_payload=request_payload, response_payload=payload, start=start
    )
    return payload


@celery_app.task(name="margin.extract.run_pipeline", queue="extraction")
def run_extract_pipeline(ingestion_job_id: str) -> dict[str, object]:
    start = datetime.now(UTC)
    session_factory = _session_factory()
    review_request: str | None = None
    try:
        with session_scope(session_factory) as session:
            job = _get_ingestion_job(session, ingestion_job_id)
            if job.status != INGESTION_JOB_EXTRACTING:
                payload: dict[str, object] = {"ingestion_job_id": str(job.id), "status": job.status, "skipped": True}
            else:
                source_document_id = _source_document_id(job)
                extraction_mode = _source_extraction_mode(session, source_document_id)
                pipeline = _extraction_pipeline(session_factory, extraction_mode=extraction_mode)
                _clear_source_pipeline_failure(session, source_document_id)
                result = pipeline.run_once(session, source_document_id=source_document_id, ingestion_job_id=str(job.id))
                set_job_status(job, INGESTION_JOB_RESOLVING)
                requires_review = bool(
                    result.report.review_task_count
                    or result.semantic_review_candidate_count
                    or result.governance_review_candidate_count
                )
                job.metadata_ = {
                    **(job.metadata_ or {}),
                    "lifecycle": {
                        "extraction_run_id": result.extraction_run_id,
                        "quarantined_element_count": result.report.quarantined_element_count,
                        "requires_review": requires_review,
                    },
                }
                payload = {
                    "ingestion_job_id": str(job.id),
                    "source_document_id": source_document_id,
                    "status": job.status,
                    "report": asdict(result.report),
                    "extraction_run_id": result.extraction_run_id,
                    "stages": list(result.stages),
                    "semantic_review_candidate_count": result.semantic_review_candidate_count,
                    "governance_review_candidate_count": result.governance_review_candidate_count,
                }
                review_request = str(job.id)
    except Exception as exc:
        _mark_ingestion_job_failed(session_factory, ingestion_job_id, exc)
        _log_task_interaction(
            event="run_extract_pipeline",
            request_payload={"ingestion_job_id": ingestion_job_id},
            response_payload=None,
            start=start,
            error=str(exc),
        )
        raise

    if review_request is not None:
        task = route_ingestion_review.delay(review_request)
        payload["review_task_id"] = str(task.id)
    _log_task_interaction(
        event="run_extract_pipeline",
        request_payload={"ingestion_job_id": ingestion_job_id},
        response_payload=payload,
        start=start,
    )
    return payload


@celery_app.task(name="margin.review.route_ingestion_job", queue="review")
def route_ingestion_review(ingestion_job_id: str) -> dict[str, object]:
    start = datetime.now(UTC)
    embedding_request: str | None = None
    session_factory = _session_factory()
    try:
        with session_scope(session_factory) as session:
            job = _get_ingestion_job(session, ingestion_job_id)
            if job.status != INGESTION_JOB_RESOLVING:
                payload: dict[str, object] = {"ingestion_job_id": str(job.id), "status": job.status, "skipped": True}
            else:
                lifecycle = (job.metadata_ or {}).get("lifecycle", {})
                quarantined = int(lifecycle.get("quarantined_element_count", 0) or 0)
                if quarantined:
                    set_job_status(
                        job,
                        INGESTION_JOB_QUARANTINED,
                        error_message=f"{quarantined} source elements require manual intervention",
                    )
                else:
                    _prepare_idea_block_embeddings(
                        session, source_document_id=_source_document_id(job), ingestion_job_id=str(job.id)
                    )
                    set_job_status(job, INGESTION_JOB_EMBEDDING)
                    embedding_request = str(job.id)
                payload = {
                    "ingestion_job_id": str(job.id),
                    "status": job.status,
                    "quarantined_element_count": quarantined,
                }
    except Exception as exc:
        _mark_ingestion_job_failed(session_factory, ingestion_job_id, exc)
        _log_task_interaction(
            event="route_ingestion_review",
            request_payload={"ingestion_job_id": ingestion_job_id},
            response_payload=None,
            start=start,
            error=str(exc),
        )
        raise

    if embedding_request is not None:
        task = generate_embedding_records.delay(ingestion_job_id=embedding_request)
        payload["embedding_task_id"] = str(task.id)
    _log_task_interaction(
        event="route_ingestion_review",
        request_payload={"ingestion_job_id": ingestion_job_id},
        response_payload=payload,
        start=start,
    )
    return payload


def _prepare_idea_block_embeddings(session: Session, *, source_document_id: str, ingestion_job_id: str) -> None:
    targets = _source_idea_block_targets(session, source_document_id)
    if not targets:
        return
    provider = _embedding_provider()
    revision_ids = tuple(target_id for _target_type, target_id in targets)
    process_search_materialization_jobs(
        session,
        limit=len(revision_ids),
        embedding_index=EmbeddingIndexConfig(
            model_id=provider.model_id, index_version=provider.index_version, dimensions=provider.dimensions
        ),
        idea_block_revision_ids=revision_ids,
    )
    for target_type, target_id in targets:
        enqueue_embedding_job(
            session,
            target_type=target_type,
            target_id=target_id,
            model_id=provider.model_id,
            index_version=provider.index_version,
            reason="ingestion_embedding",
            ingestion_job_id=ingestion_job_id,
        )


def _source_idea_block_targets(
    session: Session, source_document_id: str
) -> tuple[tuple[EmbeddingTargetType, str], ...]:
    revision_ids = session.scalars(
        select(IdeaBlock.current_revision_id)
        .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
        .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(
            SourceDocumentVersion.source_document_id == source_document_id,
            SourceDocumentVersion.status == "active",
            ClaimEvidence.status == "active",
            IdeaBlockRevision.publication_status.in_("candidate", "verified"),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
        )
        .distinct()
        .order_by(IdeaBlock.current_revision_id)
    ).all()
    return tuple(("idea_block_revision", revision_id) for revision_id in revision_ids if revision_id is not None)


@celery_app.task(name="margin.embedding.generate_records", queue="embedding")
def generate_embedding_records(limit: int = 100, ingestion_job_id: str | None = None) -> dict[str, object]:
    start = datetime.now(UTC)
    request_payload: dict[str, object] = {"limit": limit, "ingestion_job_id": ingestion_job_id}
    session_factory = _session_factory()
    provider = _embedding_provider()
    next_embedding_request: str | None = None
    try:
        with session_scope(session_factory) as session:
            if ingestion_job_id is None:
                materialized = process_search_materialization_jobs(
                    session, limit=limit, embedding_index=required_embedding_index_config()
                )
            else:
                job = _get_ingestion_job(session, ingestion_job_id)
                materialized = 0
                if job.status != INGESTION_JOB_EMBEDDING:
                    payload: dict[str, object] = {
                        "ingestion_job_id": str(job.id),
                        "status": job.status,
                        "skipped": True,
                    }
                    _log_task_interaction(
                        event="generate_embedding_records",
                        request_payload=request_payload,
                        response_payload=payload,
                        start=start,
                    )
                    return payload
            completed = process_embedding_jobs(
                session, provider=provider, limit=limit, ingestion_job_id=ingestion_job_id
            )
            payload = {
                "completed": completed,
                "materialized": materialized,
                "provider_id": provider.provider_id,
                "model_id": provider.model_id,
                "index_version": provider.index_version,
            }
            if ingestion_job_id is None:
                assignment_status, assignment_warning = _try_activate_search_assignment(session, provider)
                payload["search_assignment_status"] = assignment_status
                if assignment_warning is not None:
                    payload["search_assignment_warning"] = assignment_warning
            if ingestion_job_id is not None:
                job = _get_ingestion_job(session, ingestion_job_id)
                payload["ingestion_job_id"] = str(job.id)
                pending = session.scalar(
                    select(EmbeddingJob.id)
                    .where(EmbeddingJob.ingestion_job_id == job.id, EmbeddingJob.status.in_(("queued", "processing")))
                    .limit(1)
                )
                failed = session.scalar(
                    select(EmbeddingJob.error_message)
                    .where(EmbeddingJob.ingestion_job_id == job.id, EmbeddingJob.status == "failed")
                    .order_by(EmbeddingJob.updated_at.desc())
                    .limit(1)
                )
                if failed is not None:
                    set_job_status(job, INGESTION_JOB_FAILED, error_message=f"embedding failed: {failed}")
                elif pending is not None:
                    next_embedding_request = str(job.id)
                else:
                    lifecycle = (job.metadata_ or {}).get("lifecycle", {})
                    assignment_status, assignment_warning = _try_activate_search_assignment(session, provider)
                    payload["search_assignment_status"] = assignment_status
                    if assignment_warning is not None:
                        payload["search_assignment_warning"] = assignment_warning
                    final_status = (
                        INGESTION_JOB_REVIEW_REQUIRED if lifecycle.get("requires_review") else INGESTION_JOB_COMPLETED
                    )
                    set_job_status(job, final_status)
                payload["status"] = job.status
    except Exception as exc:
        if ingestion_job_id is not None:
            _mark_ingestion_job_failed(session_factory, ingestion_job_id, exc)
        _log_task_interaction(
            event="generate_embedding_records",
            request_payload=request_payload,
            response_payload=None,
            start=start,
            error=str(exc),
        )
        raise

    if next_embedding_request is not None:
        task = generate_embedding_records.delay(ingestion_job_id=next_embedding_request, limit=limit)
        payload["next_embedding_task_id"] = str(task.id)
    _log_task_interaction(
        event="generate_embedding_records", request_payload=request_payload, response_payload=payload, start=start
    )
    return payload


def _try_activate_search_assignment(session: Session, provider: EmbeddingProvider) -> tuple[str, str | None]:
    """Activate dense search only after the complete fixed-dimension corpus gate passes."""

    embedding_index = EmbeddingIndexConfig(
        model_id=provider.model_id, index_version=provider.index_version, dimensions=provider.dimensions
    )
    try:
        assignment = activate_search_embedding_assignment(session, embedding_index=embedding_index)
    except ValueError as exc:
        return "building", str(exc)
    return assignment.status, None


_RECOVERY_DISPATCHERS = {
    INGESTION_JOB_RECEIVED: process_ingestion_job.delay,
    INGESTION_JOB_CONVERTING: process_ingestion_job.delay,
    INGESTION_JOB_EXTRACTING: run_extract_pipeline.delay,
    INGESTION_JOB_RESOLVING: route_ingestion_review.delay,
    INGESTION_JOB_EMBEDDING: lambda ingestion_job_id: generate_embedding_records.delay(
        ingestion_job_id=ingestion_job_id
    ),
}


@celery_app.task(name="margin.entity.reresolve_affected", queue="review")
def reresolve_affected_entity_catalog(job_id: str) -> dict[str, object]:
    """Re-resolve only mentions and current revisions named by one catalog job."""

    start = datetime.now(UTC)
    request_payload: dict[str, object] = {"job_id": job_id}
    session_factory = _session_factory()
    try:
        with session_scope(session_factory) as session:
            job = session.get(EntityReresolutionJob, job_id)
            if job is None:
                raise ValueError(f"entity re-resolution job not found: {job_id}")
            result = reresolve_affected_entities(session, job=job)
            payload = {"job_id": job.id, "state": job.state, **result}
    except Exception as exc:
        with session_scope(session_factory) as failure_session:
            failed_job = failure_session.get(EntityReresolutionJob, job_id)
            if failed_job is not None:
                failed_job.state = "failed"
                failed_job.failure = str(exc)
                failed_job.metadata_ = {**(failed_job.metadata_ or {}), "failed_at": datetime.now(UTC).isoformat()}
                write_audit_event(
                    failure_session,
                    actor="system",
                    action="entity_reresolution_failed",
                    target_type="entity_reresolution_job",
                    target_id=failed_job.id,
                    before=None,
                    after={"failure": str(exc)},
                )
        _log_task_interaction(
            event="reresolve_affected_entity_catalog",
            request_payload=request_payload,
            response_payload=None,
            start=start,
            error=str(exc),
        )
        raise
    _log_task_interaction(
        event="reresolve_affected_entity_catalog",
        request_payload=request_payload,
        response_payload=payload,
        start=start,
    )
    return payload


@dataclass(frozen=True, slots=True)
class MaintenanceHealthReport:
    hygiene_findings: int
    relationship_consistency_findings: int
    graph_hygiene_findings: int
    known_unknowns: int
    contradiction_tasks: int
    blind_spot_tasks: int
    review_tasks_created: int
    ontology_records_reevaluated: int = 0
    ontology_records_withdrawn: int = 0
    stalled_jobs_resumed: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@celery_app.task(name="margin.export.domain", queue="export")
def build_domain_export(export_id: str) -> dict[str, object]:
    """Compose a domain export and store the result.

    The export is composed under the scope captured when it was requested, so
    the archive carries exactly what the requester was able to read.
    """

    with session_scope(_session_factory()) as session:
        try:
            bundle = complete_export(session, export_id)
        except Exception as error:
            logger.exception("failed to compose export %s", export_id)
            fail_export(session, export_id, str(error))
            session.commit()
            return {"export_id": export_id, "state": "failed"}
        session.commit()
        return {"export_id": bundle.id, "state": bundle.state, "content_hash": bundle.content_hash}


@celery_app.task(name="margin.maintenance.health_check", queue="maintenance")
def maintenance_health_check() -> dict[str, int]:
    recovery_requests: tuple[tuple[str, str], ...]
    with session_scope(_session_factory()) as session:
        ontology = reevaluate_ontology_records(session)
        ontology_tasks = create_review_tasks_for_findings(session, list(ontology.findings))
        findings = run_hygiene_lint(session)
        hygiene_tasks = create_review_tasks_for_findings(session, findings)
        relationship_findings = run_relationship_consistency_lint(session)
        relationship_tasks = create_review_tasks_for_findings(session, relationship_findings)
        graph_findings = run_graph_hygiene_lint(session)
        graph_tasks = create_review_tasks_for_findings(session, graph_findings)
        semantic_findings = run_semantic_lint(session)
        semantic_tasks = create_review_tasks_for_findings(session, semantic_findings)
        known_unknowns = detect_known_unknowns(session)
        known_unknown_tasks = create_known_unknown_tasks(session, known_unknowns)
        contradiction_tasks = detect_contradiction_candidates(session)
        blind_spot_tasks = detect_blind_spots(session)
        recovery_requests = _stalled_ingestion_recovery_requests(session)
        report = MaintenanceHealthReport(
            hygiene_findings=len(findings),
            relationship_consistency_findings=len(relationship_findings),
            graph_hygiene_findings=len(graph_findings),
            known_unknowns=len(known_unknowns),
            contradiction_tasks=len(contradiction_tasks),
            blind_spot_tasks=len(blind_spot_tasks),
            review_tasks_created=(
                len(hygiene_tasks)
                + len(relationship_tasks)
                + len(graph_tasks)
                + len(semantic_tasks)
                + len(known_unknown_tasks)
                + len(contradiction_tasks)
                + len(blind_spot_tasks)
                + len(ontology_tasks)
            ),
            ontology_records_reevaluated=ontology.restamped,
            ontology_records_withdrawn=ontology.withdrawn,
        ).to_dict()

    resumed = 0
    for stage, ingestion_job_id in recovery_requests:
        try:
            _RECOVERY_DISPATCHERS[stage](ingestion_job_id)
            resumed += 1
        except Exception:
            logger.exception("failed to resume stalled ingestion job %s at stage %s", ingestion_job_id, stage)
    report["stalled_jobs_resumed"] = resumed
    return report


def _stalled_ingestion_recovery_requests(
    session: Session, *, now: datetime | None = None
) -> tuple[tuple[str, str], ...]:
    current_time = now or datetime.now(UTC)
    stale_before = current_time - _STALLED_INGESTION_AFTER
    rows = session.scalars(
        select(IngestionJob)
        .where(
            IngestionJob.status.in_(
                (
                    INGESTION_JOB_RECEIVED,
                    INGESTION_JOB_CONVERTING,
                    INGESTION_JOB_EXTRACTING,
                    INGESTION_JOB_RESOLVING,
                    INGESTION_JOB_EMBEDDING,
                )
            ),
            IngestionJob.updated_at.is_not(None),
            IngestionJob.updated_at < stale_before,
        )
        .order_by(IngestionJob.updated_at, IngestionJob.id)
        .limit(_STALLED_INGESTION_LIMIT)
        .with_for_update(skip_locked=True)
    ).all()
    requests: list[tuple[str, str]] = []
    for job in rows:
        stage = job.status
        if stage not in _RECOVERY_DISPATCHERS:
            continue
        metadata = dict(job.metadata_ or {})
        recovery = dict(metadata.get("recovery") or {})
        recovery.update(
            {
                "requested_at": current_time.isoformat(),
                "stage": stage,
                "reason": "stalled ingestion stage recovered by maintenance",
            }
        )
        metadata["recovery"] = recovery
        job.metadata_ = metadata
        job.updated_at = current_time
        requests.append((stage, str(job.id)))
    if requests:
        session.flush()
    return tuple(requests)


def enqueue_ingestion_job(ingestion_job_id: str) -> str:
    task = process_ingestion_job.delay(ingestion_job_id)
    return str(task.id)


def _get_ingestion_job(session: Session, ingestion_job_id: str) -> IngestionJob:
    job = session.get(IngestionJob, ingestion_job_id)
    if job is None:
        raise ValueError(f"ingestion job not found: {ingestion_job_id}")
    return job


def _source_document_id(job: IngestionJob) -> str:
    if job.source_document_id is None:
        raise ValueError(f"ingestion job has no source document: {job.id}")
    return job.source_document_id


def _mark_ingestion_job_failed(session_factory: sessionmaker[Session], ingestion_job_id: str, exc: Exception) -> None:
    with session_scope(session_factory) as session:
        job = session.get(IngestionJob, ingestion_job_id)
        if job is None or job.status in INGESTION_JOB_TERMINAL_STATUSES:
            return
        source_document_id = _source_document_id(job)
        source_document = session.get(SourceDocument, source_document_id)
        if source_document is not None:
            source_document.status = "failed"
            source_document.metadata_ = {**(source_document.metadata_ or {}), "pipeline_failure": str(exc)}
        has_run = session.scalar(select(ExtractionRun.id).where(ExtractionRun.ingestion_job_id == job.id).limit(1))
        if has_run is None:
            extraction_mode = _source_extraction_mode(session, source_document_id)
            extractor_name = {
                "deterministic": "deterministic_markdown",
                "llm": "deterministic_markdown+openai_compatible_inference",
                "deterministic_llm": "deterministic_markdown+openai_compatible_inference",
            }[extraction_mode]
            record_failed_extraction_run(
                session,
                ingestion_job_id=str(job.id),
                source_document_id=source_document_id,
                mode=extraction_mode,
                extractor=extractor_name,
                model_id=None,
                prompt_version=None,
                prompt_hash_value=None,
                max_window_tokens=1_500,
                neighbor_tokens=160,
                error=exc,
            )
        set_job_status(job, INGESTION_JOB_FAILED, error_message=f"pipeline failed: {exc}")


def _clear_source_pipeline_failure(session: Session, source_document_id: str) -> None:
    source_document = session.get(SourceDocument, source_document_id)
    if source_document is None:
        return
    metadata = dict(source_document.metadata_ or {})
    if "pipeline_failure" not in metadata:
        return
    metadata.pop("pipeline_failure", None)
    source_document.metadata_ = metadata


def _source_extraction_mode(session: Session, source_document_id: str) -> str:
    source_document = session.get(SourceDocument, source_document_id)
    metadata = source_document.metadata_ if source_document is not None else {}
    if not isinstance(metadata, dict):
        return "deterministic"
    extraction_mode = str(metadata.get("extraction_mode") or "deterministic")
    normalized = extraction_mode.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"deterministic_+_llm", "deterministic+llm"}:
        return "deterministic_llm"
    if normalized in {"deterministic", "deterministic_llm", "llm"}:
        return normalized
    return "deterministic"


def _log_task_interaction(
    *,
    event: str,
    request_payload: dict[str, object],
    response_payload: dict[str, object] | None,
    start: datetime,
    error: str | None = None,
) -> None:
    end = datetime.now(UTC)
    payload = {
        "event": event,
        "request": request_payload,
        "response": response_payload,
        "error": error,
        "start_timestamp": start.isoformat(),
        "end_timestamp": end.isoformat(),
        "duration_ms": round((end - start).total_seconds() * 1000, 3),
    }
    logger.info(
        "celery_task_interaction %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        extra={"celery_task_interaction": payload},
    )
