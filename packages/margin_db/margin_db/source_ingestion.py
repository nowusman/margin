from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.ingestion_observability import (
    log_ingestion_interaction as _log_ingestion_interaction,
    process_job_request_payload as _process_job_request_payload,
    process_job_response_payload as _process_job_response_payload,
    upload_request_payload as _upload_request_payload,
    upload_result_payload as _upload_result_payload,
)
from margin_db.object_storage import ContentAddressableObjectStore, StoredObject, sha256_bytes
from margin_db.source_indexing import (
    IndexedSection,
    IndexedSpan,
    LocalSourceTreeSourceIndex,
    SourceIndex,
    SourceIndexer,
)
from margin_db.source_ingestion_helpers import (
    ALL_DOMAINS_ID,
    MetadataCorrectionError,
    RetryNotAllowedError,
    SourceIngestionError,
    SourceNotFoundError,
    UnsupportedSourceTypeError,
    _conversion_filename,
    _normalize_dublin_core,
    apply_converted_metadata,
    ensure_ingestion_actor,
    failed_conversion_quality_report,
    get_job_status,
    job_to_dict,
    make_ingestion_job,
    normalize_job_id,
    normalize_source_uri,
    resolve_category_id,
    resolve_domain_id,
    set_if_present,
    set_job_status,
    set_quality_report,
    validate_file_signature,
    validate_sensitivity,
    validate_source_type,
)
from margin_db.source_ingestion_serializers import (
    element_to_dict,
    element_to_tree_node,
    page_to_dict,
    source_document_to_dict,
    source_version_to_dict,
)
from margin_db.source_manifest import (
    persist_source_element_outcomes,
    persist_source_manifest,
    persist_source_page_outcomes,
    validate_persisted_source_manifest,
)
from margin_db.source_models import (
    INGESTION_JOB_CONVERTING,
    INGESTION_JOB_RECEIVED,
    ConversionQualityReport,
    IngestionJob,
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourcePage,
    SourcePageOutcome,
)
from margin_db.source_versioning import activate_source_version
from margin_ingest.contracts import ConversionResult, NormalizedDocument, NormalizedElement, NormalizedPage
from margin_ingest.indexing import compute_ocr_idempotency_key, reconcile_mixed_ocr_document
from margin_ingest.quality import expected_source_manifest, validate_normalized_document
from margin_schema.ids import source_id, source_version_id


class SourceConverter(Protocol):
    def convert(self, *, filename: str, media_type: str, data: bytes) -> ConversionResult:
        """Convert raw source bytes into normalized, citable document result."""


@dataclass(frozen=True)
class SourceUploadResult:
    source_document: SourceDocument
    ingestion_job: Any | None
    cached: bool


class WorkerDocumentConverter:
    """Default local source-ingestion converter using the worker production path."""

    version = "0.1"

    def convert(self, *, filename: str, media_type: str, data: bytes) -> ConversionResult:
        from margin_ingest.converters import convert_source_document
        from workers.ingest.metadata import extract_metadata

        safe_name = Path(filename).name or "upload"
        with tempfile.TemporaryDirectory(prefix="margin-ingest-") as temp_dir:
            source_path = Path(temp_dir) / safe_name
            source_path.write_bytes(data)
            result = convert_source_document(source_path, media_type=media_type)
            source_metadata = extract_metadata(
                source_path, markdown=result.markdown, conversion_metadata=result.metadata, media_type=media_type
            )

        metadata = {
            **result.metadata,
            "source_metadata": {
                "title": source_metadata.title,
                "creator": source_metadata.creator,
                "date": source_metadata.date,
                "format": source_metadata.format,
                "page_count": source_metadata.page_count,
                "language": source_metadata.language,
                "document_type": source_metadata.document_type,
                "document_type_hints": list(source_metadata.hints),
                "warnings": list(source_metadata.warnings),
            },
            "dublin_core": source_metadata.dublin_core,
            "raw_json": result.raw_json,
            "warnings": list(result.warnings),
        }
        updated_doc = replace(result.normalized_document, metadata=metadata)
        return replace(result, normalized_document=updated_doc)


class SourceIngestionService:
    def __init__(
        self,
        object_store: ContentAddressableObjectStore,
        *,
        converter: SourceConverter | None = None,
        source_index: SourceIndexer | None = None,
    ) -> None:
        self.object_store = object_store
        self.converter = converter or WorkerDocumentConverter()
        self.source_index = source_index or LocalSourceTreeSourceIndex()

    def upload_source(
        self,
        session: Session,
        *,
        filename: str,
        media_type: str | None,
        data: bytes,
        source_uri: str | None = None,
        domain_id: str | None = None,
        category: str | None = None,
        sensitivity: str = "internal",
        actor: str | None = None,
        auto_process: bool = True,
        owner: str | None = None,
        steward: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SourceUploadResult:
        start = datetime.now(UTC)
        request_payload = _upload_request_payload(
            filename=filename,
            media_type=media_type,
            size_bytes=len(data),
            domain_id=domain_id,
            category=category,
            sensitivity=sensitivity,
            actor=actor,
            auto_process=auto_process,
        )
        try:
            self._validate_upload_metadata(session, filename, media_type, domain_id, category, sensitivity, data=data)
            self.object_store.ensure_ready()
            stored = self.object_store.put_bytes(data)
            result = self._record_upload(
                session,
                filename=filename,
                media_type=media_type,
                stored=stored,
                source_uri=source_uri,
                domain_id=domain_id,
                category=category,
                sensitivity=sensitivity,
                actor=actor,
                auto_process=auto_process,
                owner=owner,
                steward=steward,
                metadata=metadata,
            )
        except Exception as exc:
            _log_ingestion_interaction(
                event="upload_source",
                request_payload=request_payload,
                response_payload=None,
                start=start,
                error=str(exc),
            )
            raise
        _log_ingestion_interaction(
            event="upload_source",
            request_payload=request_payload,
            response_payload=_upload_result_payload(result),
            start=start,
        )
        return result

    def upload_source_file(
        self,
        session: Session,
        *,
        filename: str,
        media_type: str | None,
        path: str | Path,
        source_uri: str | None = None,
        domain_id: str | None = None,
        category: str | None = None,
        sensitivity: str = "internal",
        actor: str | None = None,
        auto_process: bool = True,
        owner: str | None = None,
        steward: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SourceUploadResult:
        source_path = Path(path)
        start = datetime.now(UTC)
        request_payload = _upload_request_payload(
            filename=filename,
            media_type=media_type,
            size_bytes=source_path.stat().st_size if source_path.exists() else None,
            domain_id=domain_id,
            category=category,
            sensitivity=sensitivity,
            actor=actor,
            auto_process=auto_process,
        )
        try:
            file_bytes = source_path.read_bytes() if source_path.exists() else None
            self._validate_upload_metadata(
                session, filename, media_type, domain_id, category, sensitivity, data=file_bytes
            )
            self.object_store.ensure_ready()
            stored = self.object_store.put_file(path)
            result = self._record_upload(
                session,
                filename=filename,
                media_type=media_type,
                stored=stored,
                source_uri=source_uri,
                domain_id=domain_id,
                category=category,
                sensitivity=sensitivity,
                actor=actor,
                auto_process=auto_process,
                owner=owner,
                steward=steward,
                metadata=metadata,
            )
        except Exception as exc:
            _log_ingestion_interaction(
                event="upload_source_file",
                request_payload=request_payload,
                response_payload=None,
                start=start,
                error=str(exc),
            )
            raise
        _log_ingestion_interaction(
            event="upload_source_file",
            request_payload=request_payload,
            response_payload=_upload_result_payload(result),
            start=start,
        )
        return result

    def _validate_upload_metadata(
        self,
        session: Session,
        filename: str,
        media_type: str | None,
        domain_id: str | None,
        category: str | None,
        sensitivity: str,
        data: bytes | None = None,
    ) -> None:
        validate_source_type(filename, media_type)
        if data:
            validate_file_signature(data, filename, media_type)
        resolve_domain_id(session, domain_id)
        resolve_category_id(session, category)
        validate_sensitivity(sensitivity)

    def _record_upload(
        self,
        session: Session,
        *,
        filename: str,
        media_type: str | None,
        stored: StoredObject,
        source_uri: str | None,
        domain_id: str | None,
        category: str | None,
        sensitivity: str,
        actor: str | None,
        auto_process: bool,
        owner: str | None,
        steward: str | None,
        metadata: dict[str, Any] | None,
    ) -> SourceUploadResult:
        resolved_media_type = validate_source_type(filename, media_type)
        resolved_domain_id = resolve_domain_id(session, domain_id)
        logical_identity = normalize_source_uri(source_uri)
        existing = (
            session.scalar(
                select(SourceDocument).where(
                    SourceDocument.domain_id == resolved_domain_id, SourceDocument.logical_identity == logical_identity
                )
            )
            if logical_identity is not None
            else None
        )
        if existing is not None and existing.sha256 == stored.content_hash:
            return SourceUploadResult(
                source_document=existing, ingestion_job=self.latest_job_for_source(session, existing.id), cached=True
            )

        if existing is not None:
            existing.object_key = stored.content_hash
            existing.sha256 = stored.content_hash
            existing.size_bytes = stored.size_bytes
            existing.media_type = resolved_media_type
            existing.status = "uploaded"
            job = make_ingestion_job(session, existing.id)
            session.add(job)
            session.flush()
            if auto_process:
                self.process_job(session, job.id)
            return SourceUploadResult(source_document=existing, ingestion_job=job, cached=False)

        actor = ensure_ingestion_actor(session, actor)
        upload_metadata = {**(metadata or {}), "upload_filename": filename}
        document = SourceDocument(
            id=source_id(
                {
                    "domain_id": resolved_domain_id,
                    "logical_identity": logical_identity,
                    "occurrence": None if logical_identity is not None else uuid.uuid4().hex,
                }
            ),
            title=Path(filename).stem or filename,
            source_uri=source_uri.strip() if source_uri and source_uri.strip() else None,
            logical_identity=logical_identity,
            object_key=stored.content_hash,
            media_type=resolved_media_type,
            sha256=stored.content_hash,
            size_bytes=stored.size_bytes,
            domain_id=resolved_domain_id,
            category_id=resolve_category_id(session, category),
            sensitivity=sensitivity,
            status="uploaded",
            owner=owner,
            steward=steward,
            metadata_=upload_metadata,
            ingested_by=actor,
        )
        session.add(document)
        session.flush()

        job = make_ingestion_job(session, document.id)
        session.add(job)
        session.flush()

        if auto_process:
            self.process_job(session, job.id)

        return SourceUploadResult(source_document=document, ingestion_job=job, cached=False)

    def process_job(self, session: Session, job_id: str) -> Any:
        start = datetime.now(UTC)
        job = self.get_job(session, job_id)
        if get_job_status(job) not in {INGESTION_JOB_RECEIVED, INGESTION_JOB_CONVERTING}:
            _log_ingestion_interaction(
                event="process_job",
                request_payload={"job_id": str(job_id)},
                response_payload={"job_id": str(job.id), "status": get_job_status(job), "skipped": True},
                start=start,
            )
            return job
        document = session.get(SourceDocument, job.source_document_id)
        if document is None:
            exc = SourceNotFoundError(f"source document not found for job {job_id}")
            _log_ingestion_interaction(
                event="process_job",
                request_payload={"job_id": str(job_id), "source_document_id": job.source_document_id},
                response_payload=None,
                start=start,
                error=str(exc),
            )
            raise exc
        request_payload = _process_job_request_payload(job, document, filename=_conversion_filename(document))

        set_job_status(job, "converting")
        job.error_message = None
        document.status = "converting"
        session.flush()

        try:
            raw_bytes = self.object_store.get_bytes(document.sha256)
            conversion_filename = _conversion_filename(document)
            converted = self.converter.convert(
                filename=conversion_filename, media_type=document.media_type, data=raw_bytes
            )
            apply_converted_metadata(document, converted)
            version = self._append_source_index(
                session, document=document, converted=converted, job=job, source_bytes=raw_bytes
            )
            job.source_version_id = version.id
            from margin_db.embeddings import enqueue_source_element_embeddings
            from margin_embedding.factory import required_embedding_index_config

            index_config = required_embedding_index_config()
            enqueue_source_element_embeddings(
                session,
                session.scalars(select(SourceElement.id).where(SourceElement.source_version_id == version.id)).all(),
                model_id=index_config.model_id,
                index_version=index_config.index_version,
                ingestion_job_id=str(job.id),
            )
            set_job_status(job, "extracting")
            document.status = "extraction_ready"
        except Exception as exc:
            set_job_status(job, "failed", error_message=str(exc))
            document.status = "failed"
            job.metadata_ = {**(job.metadata_ or {}), "quality_report": failed_conversion_quality_report(document, exc)}
        session.flush()

        _log_ingestion_interaction(
            event="process_job",
            request_payload=request_payload,
            response_payload=_process_job_response_payload(job),
            start=start,
            error=job.error_message,
        )
        return job

    def retry_job(self, session: Session, job_id: str) -> Any:
        job = self.get_job(session, job_id)
        if get_job_status(job) != "failed":
            raise RetryNotAllowedError(f"only failed ingestion jobs are retryable: {job_id}")

        set_job_status(job, "received")
        job.error_message = None
        session.flush()
        return self.process_job(session, job.id)

    def requeue_job(self, session: Session, job_id: str) -> Any:
        job = self.get_job(session, job_id)
        if get_job_status(job) != "failed":
            raise RetryNotAllowedError(f"only failed ingestion jobs are retryable: {job_id}")
        set_job_status(job, "received")
        job.error_message = None
        session.flush()
        return job

    def requeue_latest_for_source(self, session: Session, source_document_id: str) -> Any:
        job = self.latest_job_for_source(session, source_document_id)
        if job is None:
            raise SourceNotFoundError(f"no ingestion job found for source {source_document_id}")
        return self.requeue_job(session, job.id)

    def create_reprocess_job(self, session: Session, source_document_id: str) -> Any:
        document = session.get(SourceDocument, source_document_id)
        if document is None:
            raise SourceNotFoundError(f"source document not found: {source_document_id}")

        job = make_ingestion_job(session, document.id)
        session.add(job)
        session.flush()
        return job

    def requeue_or_create_reprocess_job(self, session: Session, source_document_id: str) -> Any:
        latest_job = self.latest_job_for_source(session, source_document_id)
        if latest_job is not None and get_job_status(latest_job) == "failed" and latest_job.can_retry():
            return self.requeue_job(session, latest_job.id)
        return self.create_reprocess_job(session, source_document_id)

    def update_metadata(
        self,
        session: Session,
        source_document_id: str,
        *,
        domain_id: str | None = None,
        category: str | None = None,
        sensitivity: str | None = None,
    ) -> SourceDocument:
        document = session.get(SourceDocument, source_document_id)
        if document is None:
            raise SourceNotFoundError(f"source document not found: {source_document_id}")
        if sensitivity is not None:
            validate_sensitivity(sensitivity)
            document.sensitivity = sensitivity
        if domain_id is not None:
            document.domain_id = resolve_domain_id(session, domain_id)
        if category is not None:
            document.category_id = resolve_category_id(session, category)
        session.flush()
        return document

    def preview(self, session: Session, source_document_id: str) -> dict[str, Any]:
        document = session.get(SourceDocument, source_document_id)
        if document is None:
            raise SourceNotFoundError(f"source document not found: {source_document_id}")

        version = self.latest_version_for_source(session, source_document_id)
        pages: Sequence[SourcePage] = (
            session.scalars(
                select(SourcePage).where(SourcePage.source_version_id == version.id).order_by(SourcePage.page_number)
            ).all()
            if version is not None
            else []
        )
        elements: Sequence[SourceElement] = (
            session.scalars(
                select(SourceElement)
                .where(SourceElement.source_version_id == version.id)
                .order_by(SourceElement.ordinal)
            ).all()
            if version is not None
            else []
        )
        raw_text = self._raw_text_for_preview(version, elements)
        children_by_parent: dict[str | None, list[SourceElement]] = {}
        for element in elements:
            children_by_parent.setdefault(element.parent_element_id, []).append(element)

        return {
            "source_document": source_document_to_dict(document),
            "version": source_version_to_dict(version) if version is not None else None,
            "tree": [element_to_tree_node(element, children_by_parent) for element in children_by_parent.get(None, [])],
            "pages": [page_to_dict(page) for page in pages],
            "elements": [element_to_dict(element) for element in elements],
            "raw_extracted_text": raw_text,
        }

    def list_jobs(self, session: Session, *, state: str | None = None) -> list[Any]:
        statement = select(IngestionJob).order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
        if state is not None:
            statement = statement.where(IngestionJob.status == state)
        return list(session.scalars(statement).all())

    def get_job(self, session: Session, job_id: str) -> Any:
        job = session.get(IngestionJob, normalize_job_id(job_id))
        if job is None:
            raise SourceNotFoundError(f"ingestion job not found: {job_id}")
        return job

    def latest_job_for_source(self, session: Session, source_document_id: str) -> Any | None:
        return session.scalar(
            select(IngestionJob)
            .where(IngestionJob.source_document_id == source_document_id)
            .order_by(IngestionJob.sequence.desc(), IngestionJob.id.desc())
            .limit(1)
        )

    def latest_version_for_source(self, session: Session, source_document_id: str) -> SourceDocumentVersion | None:
        return session.scalar(
            select(SourceDocumentVersion)
            .where(
                SourceDocumentVersion.source_document_id == source_document_id, SourceDocumentVersion.status == "active"
            )
            .order_by(SourceDocumentVersion.created_at.desc())
            .limit(1)
        )

    def _append_source_index(
        self,
        session: Session,
        *,
        document: SourceDocument,
        converted: ConversionResult,
        job: Any,
        source_bytes: bytes | None = None,
    ) -> SourceDocumentVersion:
        try:
            with session.begin_nested():
                return self._append_source_index_uncommitted(
                    session, document=document, converted=converted, job=job, source_bytes=source_bytes
                )
        except SourceIngestionError as exc:
            page_ocr_outcomes = getattr(exc, "_page_ocr_outcomes", None)
            if isinstance(page_ocr_outcomes, dict):
                job.metadata_ = {**(job.metadata_ or {}), "page_ocr_outcomes": page_ocr_outcomes}
                session.flush()
            raise

    def _append_source_index_uncommitted(
        self,
        session: Session,
        *,
        document: SourceDocument,
        converted: ConversionResult,
        job: Any,
        source_bytes: bytes | None = None,
    ) -> SourceDocumentVersion:
        normalized_text = converted.normalized_document.text
        metadata = converted.normalized_document.metadata
        converter_name = converted.converter
        converter_version = converted.converter_version

        manifest_errors = validate_normalized_document(converted.normalized_document)
        if manifest_errors:
            raise SourceIngestionError("normalized source manifest is invalid: " + "; ".join(manifest_errors))
        expected_manifest = expected_source_manifest(converted.normalized_document)

        indexed_sections, quality_report = self.source_index.build(
            normalized_text, default_heading=document.title, metadata=metadata
        )
        page_manifest = quality_report.get("pages") if isinstance(quality_report.get("pages"), list) else []
        ocr_page_numbers = []
        if not document.media_type.casefold().startswith("image/"):
            ocr_page_numbers = [
                int(page["page_number"])
                for page in page_manifest
                if isinstance(page, dict)
                and isinstance(page.get("page_number"), int)
                and page.get("classification") == "ocr_required"
            ]
        requires_vision_ocr = bool(quality_report.get("ocr_needed") or ocr_page_numbers)
        ocr_outcomes_by_page: dict[int, dict[str, Any]] = {}
        if source_bytes is not None and requires_vision_ocr:
            from margin_db.model_registry import configured_active_model
            from margin_ingest.quality import extract_document_text

            model = configured_active_model(session, "vision_ocr")
            if model is None:
                raise SourceIngestionError("document text extraction requires an active vision/OCR model")
            page_numbers = ocr_page_numbers or [1]
            _validate_vision_ocr_request(page_numbers=page_numbers, source_byte_count=len(source_bytes))

            target_version_id = source_version_id(
                {
                    "source_document_id": document.id,
                    "version_sha256": document.sha256,
                    "converter": converter_name,
                    "converter_version": converter_version,
                }
            )

            model_version = getattr(model, "model_name", None) or getattr(model, "runtime", None) or "0.1"
            job_metadata = dict(job.metadata_ or {})
            existing_job_outcomes = dict(job_metadata.get("page_ocr_outcomes") or {})
            ocr_results: dict[int, str] = {}

            for page_number in page_numbers:
                cache_key = str(page_number)
                idemp_key = compute_ocr_idempotency_key(
                    source_version_id=target_version_id,
                    page_number=page_number,
                    asset_checksum=document.sha256,
                    processor_model_id=model.id,
                    processor_version=model_version,
                )

                existing_outcome = session.scalar(
                    select(SourcePageOutcome)
                    .where(SourcePageOutcome.idempotency_key == idemp_key, SourcePageOutcome.outcome == "extracted")
                    .order_by(SourcePageOutcome.created_at.desc())
                )

                job_outcome = existing_job_outcomes.get(cache_key)
                prior_attempts = (
                    existing_outcome.attempt_count
                    if existing_outcome
                    else (job_outcome.get("attempt_count", 0) if isinstance(job_outcome, dict) else 0)
                )

                if existing_outcome is not None and existing_outcome.result:
                    ocr_results[page_number] = existing_outcome.result
                    ocr_outcomes_by_page[page_number] = {
                        "attempt_count": existing_outcome.attempt_count,
                        "processor": existing_outcome.processor or model.id,
                        "processor_version": existing_outcome.processor_version
                        or getattr(model, "runtime", None)
                        or "0.1",
                        "idempotency_key": idemp_key,
                        "result": existing_outcome.result,
                        "outcome": "extracted",
                    }
                    continue

                if (
                    isinstance(job_outcome, dict)
                    and job_outcome.get("status") == "extracted"
                    and job_outcome.get("idempotency_key") == idemp_key
                    and job_outcome.get("result")
                ):
                    ocr_results[page_number] = job_outcome["result"]
                    ocr_outcomes_by_page[page_number] = {
                        "attempt_count": job_outcome.get("attempt_count", 1),
                        "processor": job_outcome.get("processor", model.id),
                        "processor_version": job_outcome.get(
                            "processor_version", getattr(model, "runtime", None) or "0.1"
                        ),
                        "idempotency_key": idemp_key,
                        "result": job_outcome["result"],
                        "outcome": "extracted",
                    }
                    continue

                max_retries = int(getattr(job, "max_retries", 3) or 3)
                if prior_attempts >= max_retries:
                    raise SourceIngestionError(
                        f"document text extraction retry policy exhausted for page {page_number}: "
                        f"{prior_attempts} attempts"
                    )

                attempt_count = prior_attempts + 1
                existing_job_outcomes[cache_key] = {
                    "attempt_count": attempt_count,
                    "idempotency_key": idemp_key,
                    "status": "in_progress",
                    "processor": model.id,
                    "processor_version": getattr(model, "runtime", None) or "0.1",
                }
                job.metadata_ = {**job_metadata, "page_ocr_outcomes": existing_job_outcomes}
                session.flush()

                try:
                    extracted_text = extract_document_text(
                        source_bytes,
                        model=model,
                        page_number=page_number,
                        domain_id=document.domain_id,
                        sensitivity=document.sensitivity,
                    )
                except Exception as exc:
                    existing_job_outcomes[cache_key] = {
                        "attempt_count": attempt_count,
                        "idempotency_key": idemp_key,
                        "status": "failed",
                        "reason": str(exc),
                        "processor": model.id,
                        "processor_version": getattr(model, "runtime", None) or "0.1",
                    }
                    job.metadata_ = {**(job.metadata_ or {}), "page_ocr_outcomes": existing_job_outcomes}
                    session.flush()
                    error = SourceIngestionError(f"vision/OCR text extraction failed for page {page_number}: {exc}")
                    error._page_ocr_outcomes = existing_job_outcomes
                    raise error from exc

                ocr_results[page_number] = extracted_text
                outcome_info = {
                    "attempt_count": attempt_count,
                    "idempotency_key": idemp_key,
                    "status": "extracted",
                    "result": extracted_text,
                    "processor": model.id,
                    "processor_version": model_version,
                    "outcome": "extracted",
                }
                existing_job_outcomes[cache_key] = outcome_info
                ocr_outcomes_by_page[page_number] = outcome_info
                job.metadata_ = {**job_metadata, "page_ocr_outcomes": existing_job_outcomes}
                session.flush()

            ocr_metadata = {
                **metadata,
                "vision_ocr_text_extraction_applied": True,
                "page_ocr_outcomes": existing_job_outcomes,
            }
            base_doc = converted.normalized_document
            updated_doc = reconcile_mixed_ocr_document(base_doc, ocr_results, metadata=ocr_metadata)
            converted = replace(converted, normalized_document=updated_doc)
            normalized_text = updated_doc.text
            metadata = ocr_metadata
            indexed_sections, extracted_quality_report = self.source_index.build(
                normalized_text, default_heading=document.title, metadata=ocr_metadata
            )
            quality_report = {
                **quality_report,
                **extracted_quality_report,
                "vision_ocr_text_extraction_applied": True,
                "page_ocr_outcomes": existing_job_outcomes,
            }
            manifest_errors = validate_normalized_document(converted.normalized_document)
            if manifest_errors:
                raise SourceIngestionError("reconciled source manifest is invalid: " + "; ".join(manifest_errors))
            expected_manifest = expected_source_manifest(converted.normalized_document)
        has_visual_elements = any(
            elem.kind in {"image", "picture", "figure"} or bool(elem.asset_key)
            for elem in converted.normalized_document.elements
        )
        citable_element_count = sum(1 for section in indexed_sections for span in section.spans if span.text.strip())
        if not has_visual_elements and (not indexed_sections or citable_element_count <= 0):
            raise SourceIngestionError("source conversion did not produce citable elements")

        if converted.normalized_document.visual_assets:
            for visual_asset in converted.normalized_document.visual_assets:
                if visual_asset.data:
                    stored_asset = self.object_store.put_bytes(visual_asset.data)
                    if (
                        stored_asset.content_hash != visual_asset.asset_key
                        or stored_asset.content_hash != visual_asset.checksum
                    ):
                        raise SourceIngestionError(
                            f"visual asset {visual_asset.asset_id} checksum does not match its object key"
                        )

        converted_bytes = normalized_text.encode("utf-8")
        converted_sha256 = normalized_document_hash(converted.normalized_document)
        previous = self.latest_version_for_source(session, document.id)
        existing = session.scalar(
            select(SourceDocumentVersion).where(
                SourceDocumentVersion.source_document_id == document.id,
                SourceDocumentVersion.version_sha256 == converted_sha256,
                SourceDocumentVersion.converter_name == converter_name,
                SourceDocumentVersion.converter_version == converter_version,
                SourceDocumentVersion.status == "active",
            )
        )
        if existing is not None:
            visual_stats = self._refresh_reused_version_visual_processing(
                session, document=document, source_version=existing, converted=converted, source_bytes=source_bytes
            )
            quality_row = session.scalar(
                select(ConversionQualityReport).where(ConversionQualityReport.source_version_id == existing.id)
            )
            quality_payload = dict(quality_row.metadata_ or {}) if quality_row is not None else {}
            if not quality_payload:
                quality_payload = dict((job.metadata_ or {}).get("quality_report") or {})
            quality_payload["visual_processing"] = visual_stats
            job.metadata_ = {
                **(job.metadata_ or {}),
                "quality_report": quality_payload,
                "reused_immutable_version": existing.id,
            }
            if quality_row is not None:
                quality_row.metadata_ = quality_payload
            return existing

        converted_object = self.object_store.put_bytes(converted_bytes)
        version = SourceDocumentVersion(
            id=source_version_id(
                {
                    "source_document_id": document.id,
                    "version_sha256": converted_sha256,
                    "converter": converter_name,
                    "converter_version": converter_version,
                    "previous_version_id": previous.id if previous is not None else None,
                }
            ),
            source_document_id=document.id,
            version_sha256=converted_sha256,
            converter_name=converter_name,
            converter_version=converter_version,
            converted_object_key=converted_object.content_hash,
            metadata_=metadata,
            dublin_core=_normalize_dublin_core(metadata.get("dublin_core")),
        )
        session.add(version)
        activate_source_version(session, version=version)

        native_elements = list(converted.normalized_document.elements)
        pages = list(converted.normalized_document.pages)

        session.flush()
        persist_source_manifest(session, source_version_id=version.id, elements=native_elements, pages=pages)
        persisted_manifest_errors = validate_persisted_source_manifest(
            session,
            source_version_id=version.id,
            document_text=normalized_text,
            expected_manifest=expected_manifest,
            object_store=self.object_store,
        )
        if persisted_manifest_errors:
            raise SourceIngestionError("persisted source manifest is invalid: " + "; ".join(persisted_manifest_errors))

        persisted_pages = list(
            session.scalars(
                select(SourcePage).where(SourcePage.source_version_id == version.id).order_by(SourcePage.page_number)
            ).all()
        )
        page_outcomes: list[SourcePageOutcome] = []
        for p in persisted_pages:
            p_outcome = ocr_outcomes_by_page.get(p.page_number)
            outcome_id = "page_outcome_" + sha256_bytes(f"{version.id}\0{p.page_number}".encode())
            if p_outcome:
                page_outcomes.append(
                    SourcePageOutcome(
                        id=outcome_id,
                        extraction_run_id=None,
                        source_page_id=p.id,
                        outcome=p_outcome.get("outcome", "extracted"),
                        reason=p_outcome.get("reason"),
                        attempt_count=p_outcome.get("attempt_count", 1),
                        processor=p_outcome.get("processor"),
                        processor_version=p_outcome.get("processor_version"),
                        idempotency_key=p_outcome.get("idempotency_key"),
                        result=p_outcome.get("result"),
                        metadata_={"page_number": p.page_number, "ocr": True},
                    )
                )
            else:
                native_key = compute_ocr_idempotency_key(
                    source_version_id=version.id,
                    page_number=p.page_number,
                    asset_checksum=p.content_checksum or "",
                    processor_model_id=converter_name,
                    processor_version=converter_version,
                )
                page_outcomes.append(
                    SourcePageOutcome(
                        id=outcome_id,
                        extraction_run_id=None,
                        source_page_id=p.id,
                        outcome="extracted",
                        reason=None,
                        attempt_count=1,
                        processor=converter_name,
                        processor_version=converter_version,
                        idempotency_key=native_key,
                        result=None,
                        metadata_={"page_number": p.page_number, "ocr": False},
                    )
                )
        if page_outcomes:
            persist_source_page_outcomes(session, outcomes=page_outcomes)

        from margin_ingest.vision import process_version_visual_elements

        vision_outcomes = process_version_visual_elements(
            session,
            source_document=document,
            source_version=version,
            object_store=self.object_store,
            source_bytes=source_bytes,
            visual_assets=converted.normalized_document.visual_assets,
        )
        visual_stats = {
            "processed": len(vision_outcomes),
            "success": sum(1 for o in vision_outcomes if o.status == "success"),
            "quarantined": sum(1 for o in vision_outcomes if o.status == "quarantined"),
        }
        quality_payload = {
            **quality_report,
            "converter": converter_name,
            "converter_version": converter_version,
            "metadata": metadata,
            "visual_processing": visual_stats,
        }
        if expected_manifest is not None:
            quality_payload["expected_manifest"] = expected_manifest
        set_quality_report(session, job, document.id, version.id, quality_payload)
        return version

    def _refresh_reused_version_visual_processing(
        self,
        session: Session,
        *,
        document: SourceDocument,
        source_version: SourceDocumentVersion,
        converted: ConversionResult,
        source_bytes: bytes | None,
    ) -> dict[str, int]:
        """Retry visual elements when immutable conversion output is reused."""
        from margin_ingest.vision import process_version_visual_elements

        outcomes = process_version_visual_elements(
            session,
            source_document=document,
            source_version=source_version,
            object_store=self.object_store,
            source_bytes=source_bytes,
            visual_assets=converted.normalized_document.visual_assets,
        )
        return {
            "processed": len(outcomes),
            "success": sum(1 for outcome in outcomes if outcome.status == "success"),
            "quarantined": sum(1 for outcome in outcomes if outcome.status == "quarantined"),
        }

    def _raw_text_for_preview(self, version: SourceDocumentVersion | None, elements: Sequence[SourceElement]) -> str:
        if version is not None and version.converted_object_key:
            try:
                return self.object_store.get_bytes(version.converted_object_key).decode("utf-8", errors="replace")
            except Exception:
                pass
        return "\n\n".join(element.text or "" for element in elements).strip()


def _validate_vision_ocr_request(*, page_numbers: list[int], source_byte_count: int) -> None:
    max_pages = int(os.getenv("MARGIN_LLM_OCR_MAX_PAGES", "25"))
    max_bytes = int(os.getenv("MARGIN_LLM_OCR_MAX_SOURCE_BYTES", str(25 * 1024 * 1024)))
    if len(page_numbers) > max_pages:
        raise SourceIngestionError(
            f"vision/OCR text extraction requires {len(page_numbers)} OCR pages, "
            f"which exceeds MARGIN_LLM_OCR_MAX_PAGES={max_pages}"
        )
    if source_byte_count > max_bytes:
        raise SourceIngestionError(
            f"vision/OCR text extraction source size {source_byte_count} bytes exceeds "
            f"MARGIN_LLM_OCR_MAX_SOURCE_BYTES={max_bytes}"
        )


def normalized_document_hash(document: NormalizedDocument) -> str:
    """Hash normalized content and visual assets for immutable version identity."""

    def element_payload(element: NormalizedElement) -> dict[str, object]:
        return {
            "element_id": element.element_id,
            "kind": element.kind,
            "text": element.text,
            "ordinal": element.ordinal,
            "page_number": element.page_number,
            "parent_id": element.parent_id,
            "char_start": element.char_start,
            "char_end": element.char_end,
            "bounding_box": element.bounding_box,
            "asset_key": element.asset_key,
            "confidence": element.confidence,
            "metadata": element.metadata,
        }

    payload = {
        "source_name": document.source_name,
        "format": document.format,
        "text": document.text,
        "pages": [
            {
                "page_number": page.page_number,
                "checksum": page.checksum,
                "text": page.text,
                "elements": [element_payload(element) for element in page.elements],
                "width": page.width,
                "height": page.height,
                "metadata": page.metadata,
            }
            for page in document.pages
        ],
        "elements": [element_payload(element) for element in document.elements],
        "visual_assets": [
            {
                "asset_id": asset.asset_id,
                "asset_key": asset.asset_key,
                "media_type": asset.media_type,
                "checksum": asset.checksum,
                "page_number": asset.page_number,
                "bounding_box": asset.bounding_box,
                "metadata": asset.metadata,
            }
            for asset in document.visual_assets
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_bytes(canonical.encode("utf-8"))


__all__ = [
    "ALL_DOMAINS_ID",
    "ConversionResult",
    "IndexedSection",
    "IndexedSpan",
    "IngestionJob",
    "LocalSourceTreeSourceIndex",
    "MetadataCorrectionError",
    "NormalizedDocument",
    "NormalizedElement",
    "NormalizedPage",
    "RetryNotAllowedError",
    "SourceConverter",
    "SourceIndex",
    "SourceIndexer",
    "SourceIngestionError",
    "SourceIngestionService",
    "SourceNotFoundError",
    "SourceUploadResult",
    "UnsupportedSourceTypeError",
    "get_job_status",
    "job_to_dict",
    "persist_source_element_outcomes",
    "persist_source_manifest",
    "persist_source_page_outcomes",
    "resolve_category_id",
    "resolve_domain_id",
    "set_if_present",
    "source_document_to_dict",
    "source_version_to_dict",
    "validate_source_type",
]
