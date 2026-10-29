from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.authz import has_permission, require_permission
from app.config import get_settings
from margin_db.entity_models import Entity
from margin_db.extraction import purge_deleted_source_extractions
from margin_db.model_registry import ModelRegistryError, configured_active_model
from margin_db.object_storage import ObjectStorageError
from margin_db.object_store_factory import ObjectStoreConfig, create_object_store
from margin_db.review_models import ReviewTask, ReviewTaskComment
from margin_db.source_ingestion import (
    MetadataCorrectionError,
    RetryNotAllowedError,
    SourceIngestionService,
    SourceNotFoundError,
    UnsupportedSourceTypeError,
    get_job_status,
    job_to_dict,
    set_if_present,
    set_job_status,
    source_document_to_dict,
    source_version_to_dict,
)
from margin_db.source_ingestion_serializers import element_to_dict
from margin_db.source_models import (
    INGESTION_JOB_TERMINAL_STATUSES,
    ConversionQualityReport,
    IngestionJob,
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourcePage,
)
from margin_db.user_models import Domain
from margin_inference import InferenceUnavailableError, probe_openai_compatible_endpoint
from margin_schema.text_normalization import normalize_canonical_text
from workers.tasks import enqueue_ingestion_job

logger = logging.getLogger(__name__)

EXTRACTION_MODES = {"deterministic", "deterministic_llm", "llm"}
OFFICE_PREVIEW_MEDIA_TYPES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
PREVIEW_PDF_METADATA_KEY = "preview_pdf_object_key"


def _normalized_group_ids(request: Request) -> set[str]:
    group_ids = getattr(request.state, "group_ids", None)
    if group_ids is None:
        group_ids = request.headers.get("x-margin-groups", "")
    if isinstance(group_ids, str):
        values = group_ids.split(",")
    else:
        values = group_ids
    return {
        str(group_id).strip().lower().split("/")[-1].replace("_", "-") for group_id in values if str(group_id).strip()
    }


def _require_margin_admin_group(request: Request) -> None:
    if "margin-admin" not in _normalized_group_ids(request):
        raise HTTPException(status_code=403, detail="margin-admin group is required to delete sources")


def maybe_require_permission(
    request: Request, *, domain_id: str | None, category: str | None, sensitivity: str | None, action: str
) -> None:
    permissions = getattr(request.state, "permissions", None)
    if permissions is None:
        return
    require_permission(
        permissions=permissions, domain_id=domain_id, category=category, sensitivity=sensitivity, action=action
    )


def _require_document_read(session: Session, request: Request, document: SourceDocument) -> None:
    category = session.get(Entity, document.category_id)
    if category is None or category.ontology_type != "category" or category.lifecycle_status != "active":
        raise HTTPException(status_code=409, detail="source document has an invalid category reference")
    maybe_require_permission(
        request,
        domain_id=document.domain_id,
        category=category.preferred_label,
        sensitivity=document.sensitivity,
        action="read",
    )


router = APIRouter(prefix="/api", tags=["sources"])


class SourceMetadataCorrection(BaseModel):
    domain_id: str | None = None
    category: str | None = None
    sensitivity: str | None = None
    owner: str | None = None
    steward: str | None = None
    source_language: str | None = None
    extraction_mode: str | None = None
    extraction_priority: str | None = None
    source_version: str | None = None
    source_date: str | None = None
    source_type: str | None = None


def normalize_extraction_mode(extraction_mode: str | None) -> str:
    normalized = (extraction_mode or "deterministic").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"deterministic_+_llm", "deterministic+llm"}:
        normalized = "deterministic_llm"
    if normalized not in EXTRACTION_MODES:
        raise HTTPException(status_code=422, detail="extraction_mode must be deterministic, deterministic_llm, or llm")
    return normalized


def require_llm_ingestion_ready(session: Session) -> None:
    try:
        model = configured_active_model(session, "extraction")
    except ModelRegistryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if model is None:
        raise HTTPException(status_code=503, detail="LLM ingestion requires one active configured extraction model")
    if model.last_test_status != "healthy":
        raise HTTPException(status_code=503, detail="configured extraction model has no successful admin health test")
    try:
        probe_openai_compatible_endpoint(
            base_url=model.endpoint_url,
            model=model.model_name,
            secret_reference=getattr(model, "secret_reference", None),
        )
    except InferenceUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail=f"configured extraction LLM({model.model_name}) is not accessible"
        ) from exc


def _source_extraction_mode(source_document: SourceDocument | dict[str, object]) -> str:
    metadata = (
        source_document.metadata_
        if isinstance(source_document, SourceDocument)
        else source_document.get("metadata", {})
    )
    if not isinstance(metadata, dict):
        metadata = {}
    return normalize_extraction_mode(str(metadata.get("extraction_mode") or "deterministic"))


def requires_llm_extraction(extraction_mode: str) -> bool:
    return extraction_mode in {"deterministic_llm", "llm"}


def _require_source_extraction_ready(session: Session, source_document: SourceDocument | dict[str, object]) -> None:
    if requires_llm_extraction(_source_extraction_mode(source_document)):
        require_llm_ingestion_ready(session)


def _download_filename(document: SourceDocument) -> str:
    metadata = document.metadata_ if isinstance(document.metadata_, dict) else {}
    upload_filename = metadata.get("upload_filename")
    if isinstance(upload_filename, str) and upload_filename.strip():
        return upload_filename.strip()
    fallback = document.source_uri or document.title or document.id
    normalized = fallback.split("?", 1)[0].split("#", 1)[0].replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1] or document.id


def _preview_pdf_filename(document: SourceDocument) -> str:
    return f"{Path(_download_filename(document)).stem or document.id}.pdf"


def _is_manual_source(document: SourceDocument) -> bool:
    metadata = document.metadata_ if isinstance(document.metadata_, dict) else {}
    return bool(metadata.get("manual_ideablock") or metadata.get("manual_entity") or metadata.get("manual_record"))


def _manual_source_content(document: SourceDocument) -> bytes:
    metadata = document.metadata_ if isinstance(document.metadata_, dict) else {}
    payload = {
        "id": document.id,
        "title": document.title,
        "source_uri": document.source_uri,
        "media_type": document.media_type,
        "domain_id": document.domain_id,
        "category_id": document.category_id,
        "sensitivity": document.sensitivity,
        "status": document.status,
        "metadata": metadata,
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def _ensure_manual_source_version(session: Session, document: SourceDocument, service: SourceIngestionService) -> None:
    if not _is_manual_source(document):
        return
    if service.latest_version_for_source(session, document.id) is not None:
        return
    content = _manual_source_content(document)
    digest = sha256(content).hexdigest()
    session.add(
        SourceDocumentVersion(
            id=f"srcv_{sha256(document.id.encode('utf-8')).hexdigest()[:32]}",
            source_document_id=document.id,
            version_sha256=digest,
            converter_name="manual",
            converter_version="1",
            metadata_={"manual_source": True},
            dublin_core={},
        )
    )
    session.commit()


def _is_office_preview_source(document: SourceDocument) -> bool:
    return (document.media_type or "").lower() in OFFICE_PREVIEW_MEDIA_TYPES


def _convert_office_to_pdf(
    *, filename: str, media_type: str, content: bytes, converter: Callable[..., bytes] | None = None
) -> bytes:
    if converter is not None:
        return converter(filename=filename, media_type=media_type, content=content)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        raise HTTPException(status_code=503, detail="Office preview conversion requires LibreOffice")
    with tempfile.TemporaryDirectory(prefix="margin-office-preview-") as temp_dir:
        temp_path = Path(temp_dir)
        input_path = temp_path / (Path(filename).name or "source-office-document")
        input_path.write_bytes(content)
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(temp_path), str(input_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        output_path = input_path.with_suffix(".pdf")
        if result.returncode != 0 or not output_path.exists():
            detail = (result.stderr or result.stdout or "Office preview conversion failed").strip()
            raise HTTPException(status_code=422, detail=detail)
        return output_path.read_bytes()


def normalize_domain_id(domain_id: str | None) -> str:
    if domain_id is None:
        return "ALL"
    normalized = domain_id.strip()
    if normalized.lower() in {"all domain", "all domains", "all"}:
        return "ALL"
    if not normalized:
        return "ALL"
    return normalized


def resolve_known_domain_id(session: Session, domain_id: str | None) -> str | None:
    normalized = normalize_domain_id(domain_id)
    if normalized is None:
        return None
    exact = session.get(Domain, normalized)
    if exact is not None:
        return exact.id
    normalized_key = normalize_canonical_text(normalized)
    domains = session.query(Domain).all()
    if not domains:
        return normalized
    for domain in domains:
        if normalize_canonical_text(domain.name) == normalized_key:
            return domain.id
    raise MetadataCorrectionError(f"unknown domain: {normalized}")


def _enqueue_ingestion_job(request: Request, job_id: str) -> str:
    enqueue = getattr(request.app.state, "enqueue_ingestion_job", None)
    if callable(enqueue):
        return str(enqueue(job_id))
    return enqueue_ingestion_job(job_id)


def dispatch_job_or_mark_failed(
    request: Request, session: Session, *, job: Any, mark_source_failed: bool = True
) -> str:
    start = datetime.now(UTC)
    request_payload = {
        "job_id": str(job.id),
        "source_document_id": getattr(job, "source_document_id", None),
        "status": getattr(job, "status", None),
    }
    try:
        queue_task_id = _enqueue_ingestion_job(request, str(job.id))
    except Exception as exc:
        set_job_status(job, "failed", error_message=f"queue dispatch failed: {exc}")
        if mark_source_failed:
            source_document = session.get(SourceDocument, getattr(job, "source_document_id", None))
            if source_document is not None:
                source_document.status = "failed"
        session.commit()
        session.refresh(job)
        log_api_interaction(
            event="dispatch_ingestion_job",
            request_payload=request_payload,
            response_payload={
                "job_id": str(job.id),
                "status": getattr(job, "status", None),
                "error_message": getattr(job, "error_message", None),
            },
            start=start,
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail="ingestion queue unavailable; job marked failed") from exc
    session.refresh(job)
    log_api_interaction(
        event="dispatch_ingestion_job",
        request_payload=request_payload,
        response_payload={
            "job_id": str(job.id),
            "status": getattr(job, "status", None),
            "queue_task_id": queue_task_id,
        },
        start=start,
    )
    return queue_task_id


def get_source_ingestion_service(request: Request) -> SourceIngestionService:
    service = getattr(request.app.state, "source_ingestion_service", None)
    if service is not None:
        return service

    object_store = getattr(request.app.state, "object_store", None)
    if object_store is None:
        settings = getattr(request.app.state, "settings", None) or get_settings()
        object_store = create_object_store(
            ObjectStoreConfig(
                endpoint=settings.object_store_endpoint,
                bucket=settings.object_store_bucket,
                region=settings.object_store_region,
                access_key_id=settings.object_store_access_key_id,
                secret_access_key=settings.object_store_secret_access_key,
                session_token=settings.object_store_session_token,
                filesystem_root=Path(settings.object_store_local_path),
            )
        )
        request.app.state.object_store = object_store

    converter = getattr(request.app.state, "source_converter", None)
    service = SourceIngestionService(object_store, converter=converter)
    request.app.state.source_ingestion_service = service
    return service


def get_session_factory(request: Request) -> Callable[[], Session]:
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise HTTPException(status_code=503, detail="source ingestion database is not configured")
    return session_factory


def _can_job_source_permission(session: Session, request: Request, job: object, *, action: str) -> bool:
    permissions = getattr(request.state, "permissions", None)
    if permissions is None:
        return True
    source = session.get(SourceDocument, getattr(job, "source_document_id", None))
    if source is None:
        return False
    category = session.get(Entity, source.category_id)
    if category is None or category.ontology_type != "category" or category.lifecycle_status != "active":
        return False
    return has_permission(
        permissions=permissions,
        domain_id=source.domain_id,
        category=category.preferred_label,
        sensitivity=source.sensitivity,
        action=action,
    )


def _require_job_source_permission(session: Session, request: Request, job: object, *, action: str) -> None:
    permissions = getattr(request.state, "permissions", None)
    if permissions is None:
        return
    source = session.get(SourceDocument, getattr(job, "source_document_id", None))
    if source is None:
        raise HTTPException(status_code=404, detail="source document not found for ingestion job")
    category = session.get(Entity, source.category_id)
    if category is None or category.ontology_type != "category" or category.lifecycle_status != "active":
        raise HTTPException(status_code=409, detail="source document has an invalid category reference")
    require_permission(
        permissions=permissions,
        domain_id=source.domain_id,
        category=category.preferred_label,
        sensitivity=source.sensitivity,
        action=action,
    )


def log_api_interaction(
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
        "source_upload_interaction %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        extra={"source_upload_interaction": payload},
    )


@router.post("/sources", status_code=201)
async def upload_source(
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
    source_uri: Annotated[str | None, Form()] = None,
    source_version: Annotated[str | None, Form()] = None,
    source_owner: Annotated[str | None, Form()] = None,
    source_language: Annotated[str | None, Form()] = None,
    extraction_mode: Annotated[str | None, Form()] = None,
    extraction_priority: Annotated[str | None, Form()] = None,
    review_owner: Annotated[str | None, Form()] = None,
    source_type: Annotated[str | None, Form()] = None,
    source_date: Annotated[str | None, Form()] = None,
    domain_id: Annotated[str | None, Form()] = None,
    category: Annotated[str | None, Form()] = None,
    sensitivity: Annotated[str, Form()] = "internal",
) -> dict[str, object]:
    start = datetime.now(UTC)
    domain_id = normalize_domain_id(domain_id)
    normalized_extraction_mode = normalize_extraction_mode(extraction_mode)
    request_payload: dict[str, object] = {
        "filename": file.filename if file is not None else None,
        "media_type": file.content_type if file is not None else None,
        "source_uri": source_uri,
        "source_version": source_version,
        "source_owner": source_owner,
        "source_language": source_language,
        "extraction_mode": normalized_extraction_mode,
        "extraction_priority": extraction_priority,
        "review_owner": review_owner,
        "source_type": source_type,
        "source_date": source_date,
        "domain_id": domain_id,
        "category": category,
        "sensitivity": sensitivity,
    }
    if file is None:
        log_api_interaction(
            event="upload_source",
            request_payload=request_payload,
            response_payload={"status_code": 422},
            start=start,
            error="file is required; source_uri is provenance metadata only",
        )
        raise HTTPException(status_code=422, detail="file is required; source_uri is provenance metadata only")
    filename = file.filename or "upload"
    media_type = file.content_type
    service = get_source_ingestion_service(request)
    session_factory = get_session_factory(request)

    try:
        with session_factory() as session:
            domain_id = resolve_known_domain_id(session, domain_id)
            if requires_llm_extraction(normalized_extraction_mode):
                require_llm_ingestion_ready(session)
            maybe_require_permission(
                request, domain_id=domain_id, category=category, sensitivity=sensitivity, action="write"
            )
            with tempfile.NamedTemporaryFile(prefix="margin-upload-", suffix=".tmp", delete=False) as temp_file:
                while chunk := await file.read(1024 * 1024):
                    temp_file.write(chunk)
                temp_file.flush()
                temp_path = Path(temp_file.name)
            try:
                result = service.upload_source_file(
                    session,
                    filename=filename,
                    media_type=media_type,
                    path=temp_path,
                    source_uri=source_uri,
                    domain_id=domain_id,
                    category=category,
                    sensitivity=sensitivity,
                    actor=getattr(request.state, "principal_id", None),
                    auto_process=False,
                    owner=source_owner,
                    steward=review_owner,
                    metadata={
                        "extraction_mode": normalized_extraction_mode,
                        **({"source_language": source_language} if source_language else {}),
                        **({"extraction_priority": extraction_priority} if extraction_priority else {}),
                        **({"source_version": source_version} if source_version else {}),
                        **({"source_date": source_date} if source_date else {}),
                        **({"source_type": source_type} if source_type else {}),
                    },
                )
            finally:
                temp_path.unlink(missing_ok=True)
            session.commit()
            queue_task_id = None
            if result.ingestion_job is not None and not result.cached:
                queue_task_id = dispatch_job_or_mark_failed(request, session, job=result.ingestion_job)
            payload = {
                "source_document": {
                    **source_document_to_dict(result.source_document),
                    "owner": result.source_document.owner,
                    "steward": result.source_document.steward,
                },
                "ingestion_job": job_to_dict(result.ingestion_job),
                "cached": result.cached,
            }
            if queue_task_id is not None:
                payload["queue_task_id"] = queue_task_id
            log_api_interaction(
                event="upload_source",
                request_payload=request_payload,
                response_payload={
                    "status_code": 201,
                    "source_document_id": payload["source_document"]["id"],
                    "ingestion_job_id": (
                        payload["ingestion_job"]["id"] if payload["ingestion_job"] is not None else None
                    ),
                    "cached": payload["cached"],
                    "queue_task_id": queue_task_id,
                },
                start=start,
            )
            return payload
    except HTTPException as exc:
        log_api_interaction(
            event="upload_source",
            request_payload=request_payload,
            response_payload={"status_code": exc.status_code, "detail": exc.detail},
            start=start,
            error=str(exc.detail),
        )
        raise
    except UnsupportedSourceTypeError as exc:
        log_api_interaction(
            event="upload_source",
            request_payload=request_payload,
            response_payload={"status_code": 415},
            start=start,
            error=str(exc),
        )
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except MetadataCorrectionError as exc:
        log_api_interaction(
            event="upload_source",
            request_payload=request_payload,
            response_payload={"status_code": 422},
            start=start,
            error=str(exc),
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/sources/{source_document_id}")
def get_source(request: Request, source_document_id: str) -> dict[str, object]:
    service = get_source_ingestion_service(request)
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            document = session.get(SourceDocument, source_document_id)
            if document is not None:
                _ensure_manual_source_version(session, document, service)
            preview = service.preview(session, source_document_id)
            source_document = preview["source_document"]
            source_row = session.get(SourceDocument, source_document_id)
            if source_row is None:
                raise HTTPException(status_code=404, detail=f"source document not found: {source_document_id}")
            _require_document_read(session, request, source_row)
            return {
                "source_document": {
                    **source_document,
                    "owner": source_row.owner if source_row is not None else None,
                    "steward": source_row.steward if source_row is not None else None,
                },
                "version": preview["version"],
                "latest_job": job_to_dict(service.latest_job_for_source(session, source_document_id)),
            }
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sources/{source_document_id}/elements")
def source_elements(request: Request, source_document_id: str) -> dict[str, object]:
    """Return exact source elements only after the owning source is authorized."""

    session_factory = get_session_factory(request)
    with session_factory() as session:
        document = session.get(SourceDocument, source_document_id)
        if document is None:
            raise HTTPException(status_code=404, detail=f"source document not found: {source_document_id}")
        _require_document_read(session, request, document)
        elements = session.scalars(
            select(SourceElement)
            .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
            .where(
                SourceDocumentVersion.source_document_id == source_document_id, SourceDocumentVersion.status == "active"
            )
            .order_by(SourceElement.page_number, SourceElement.ordinal, SourceElement.id)
        ).all()
        return {"elements": [element_to_dict(element) for element in elements]}


@router.get("/sources/{source_document_id}/preview")
def preview_source(request: Request, source_document_id: str) -> dict[str, object]:
    service = get_source_ingestion_service(request)
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            document = session.get(SourceDocument, source_document_id)
            if document is not None:
                _ensure_manual_source_version(session, document, service)
            preview = service.preview(session, source_document_id)
            source_row = session.get(SourceDocument, source_document_id)
            if source_row is None:
                raise HTTPException(status_code=404, detail=f"source document not found: {source_document_id}")
            _require_document_read(session, request, source_row)
            return preview
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sources/{source_document_id}/file")
def source_file(request: Request, source_document_id: str) -> Response:
    service = get_source_ingestion_service(request)
    session_factory = get_session_factory(request)
    with session_factory() as session:
        document = session.get(SourceDocument, source_document_id)
        if document is None:
            raise HTTPException(status_code=404, detail=f"source document not found: {source_document_id}")
        _require_document_read(session, request, document)
        if _is_manual_source(document):
            encoded_filename = quote(f"{document.id}.json", safe="")
            return Response(
                content=_manual_source_content(document),
                media_type="text/plain; charset=utf-8",
                headers={
                    "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        try:
            content = service.object_store.get_bytes(document.sha256)
        except ObjectStorageError as exc:
            raise HTTPException(status_code=404, detail="source file object not found") from exc

        encoded_filename = quote(_download_filename(document), safe="")
        return Response(
            content=content,
            media_type=document.media_type or "application/octet-stream",
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
                "X-Content-Type-Options": "nosniff",
            },
        )


@router.get("/sources/{source_document_id}/preview-file")
def source_preview_file(request: Request, source_document_id: str) -> Response:
    service = get_source_ingestion_service(request)
    session_factory = get_session_factory(request)
    with session_factory() as session:
        document = session.get(SourceDocument, source_document_id)
        if document is None:
            raise HTTPException(status_code=404, detail=f"source document not found: {source_document_id}")
        _require_document_read(session, request, document)
        if _is_manual_source(document):
            encoded_filename = quote(f"{document.id}.json", safe="")
            return Response(
                content=_manual_source_content(document),
                media_type="text/plain; charset=utf-8",
                headers={
                    "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        if document.media_type == "application/pdf":
            try:
                content = service.object_store.get_bytes(document.sha256)
            except ObjectStorageError as exc:
                raise HTTPException(status_code=404, detail="source file object not found") from exc
        elif _is_office_preview_source(document):
            metadata = dict(document.metadata_ or {})
            preview_key = metadata.get(PREVIEW_PDF_METADATA_KEY)
            if isinstance(preview_key, str) and preview_key:
                try:
                    content = service.object_store.get_bytes(preview_key)
                except ObjectStorageError:
                    content = b""
            else:
                content = b""
            if not content:
                try:
                    original = service.object_store.get_bytes(document.sha256)
                except ObjectStorageError as exc:
                    raise HTTPException(status_code=404, detail="source file object not found") from exc
                content = _convert_office_to_pdf(
                    filename=_download_filename(document),
                    media_type=document.media_type or "",
                    content=original,
                    converter=getattr(request.app.state, "office_preview_converter", None),
                )
                stored = service.object_store.put_bytes(content)
                metadata[PREVIEW_PDF_METADATA_KEY] = stored.content_hash
                document.metadata_ = metadata
                session.commit()
        else:
            raise HTTPException(status_code=415, detail="source type does not support PDF preview")

        encoded_filename = quote(_preview_pdf_filename(document), safe="")
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
                "X-Content-Type-Options": "nosniff",
            },
        )


@router.get("/sources/{source_document_id}/versions")
def source_versions(request: Request, source_document_id: str) -> dict[str, object]:
    service = get_source_ingestion_service(request)
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            document = session.get(SourceDocument, source_document_id)
            if document is not None:
                _ensure_manual_source_version(session, document, service)
            source_row = session.get(SourceDocument, source_document_id)
            if source_row is None:
                raise HTTPException(status_code=404, detail=f"source document not found: {source_document_id}")
            _require_document_read(session, request, source_row)
            versions = session.scalars(
                select(SourceDocumentVersion)
                .where(SourceDocumentVersion.source_document_id == source_document_id)
                .order_by(SourceDocumentVersion.created_at, SourceDocumentVersion.id)
            ).all()
            return {
                "source_document_id": source_document_id,
                "versions": [source_version_to_dict(version) for version in versions],
                "current_version_id": next((version.id for version in versions if version.status == "active"), None),
            }
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/admin/sources/{source_document_id}/metadata")
def correct_source_metadata(
    request: Request, source_document_id: str, correction: SourceMetadataCorrection
) -> dict[str, object]:
    correction.domain_id = normalize_domain_id(correction.domain_id)
    service = get_source_ingestion_service(request)
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            preview = service.preview(session, source_document_id)
            source_document = preview["source_document"]
            maybe_require_permission(
                request,
                domain_id=source_document.get("domain_id"),
                category=source_document.get("category"),
                sensitivity=source_document.get("sensitivity"),
                action="write",
            )
            correction.domain_id = resolve_known_domain_id(session, correction.domain_id)
            target_domain_id = (
                correction.domain_id if correction.domain_id is not None else source_document.get("domain_id")
            )
            target_category = (
                correction.category if correction.category is not None else source_document.get("category")
            )
            target_sensitivity = (
                correction.sensitivity if correction.sensitivity is not None else source_document.get("sensitivity")
            )
            maybe_require_permission(
                request,
                domain_id=target_domain_id,
                category=target_category,
                sensitivity=target_sensitivity,
                action="write",
            )
            document = service.update_metadata(
                session,
                source_document_id,
                domain_id=correction.domain_id,
                category=correction.category,
                sensitivity=correction.sensitivity,
            )
            if correction.owner is not None:
                document.owner = correction.owner
            if correction.steward is not None:
                document.steward = correction.steward
            metadata = dict(document.metadata_ or {})
            if correction.source_language is not None:
                metadata["source_language"] = correction.source_language
            if correction.extraction_mode is not None:
                metadata["extraction_mode"] = normalize_extraction_mode(correction.extraction_mode)
            if correction.extraction_priority is not None:
                metadata["extraction_priority"] = correction.extraction_priority
            if correction.source_version is not None:
                metadata["source_version"] = correction.source_version
            if correction.source_date is not None:
                metadata["source_date"] = correction.source_date
            if correction.source_type is not None:
                metadata["source_type"] = correction.source_type
            document.metadata_ = metadata
            session.commit()
            return {
                "source_document": {
                    **source_document_to_dict(document),
                    "owner": document.owner,
                    "steward": document.steward,
                }
            }
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MetadataCorrectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/sources/{source_document_id}")
def patch_source_metadata(
    request: Request, source_document_id: str, correction: SourceMetadataCorrection
) -> dict[str, object]:
    return correct_source_metadata(request, source_document_id, correction)


@router.delete("/sources/{source_document_id}")
def delete_source(request: Request, source_document_id: str) -> dict[str, object]:
    _require_margin_admin_group(request)
    session_factory = get_session_factory(request)
    with session_factory() as session:
        document = session.get(SourceDocument, source_document_id)
        if document is None:
            raise HTTPException(status_code=404, detail=f"source document not found: {source_document_id}")
        maybe_require_permission(
            request,
            domain_id=document.domain_id,
            category=document.category,
            sensitivity=document.sensitivity,
            action="write",
        )
        version_ids = list(
            session.scalars(
                select(SourceDocumentVersion.id).where(SourceDocumentVersion.source_document_id == source_document_id)
            ).all()
        )
        job_filters = [IngestionJob.source_document_id == source_document_id]
        if version_ids:
            job_filters.append(IngestionJob.source_version_id.in_(version_ids))
        session.execute(delete(IngestionJob).where(or_(*job_filters)))
        session.execute(
            delete(ConversionQualityReport).where(ConversionQualityReport.source_document_id == source_document_id)
        )
        purge_deleted_source_extractions(session, old_version_ids=version_ids)
        element_ids = list(
            session.scalars(select(SourceElement.id).where(SourceElement.source_version_id.in_(version_ids))).all()
        )
        page_ids = list(
            session.scalars(select(SourcePage.id).where(SourcePage.source_version_id.in_(version_ids))).all()
        )
        review_task_ids = list(
            session.scalars(
                select(ReviewTask.id).where(
                    or_(
                        (ReviewTask.target_type == "source_element") & ReviewTask.target_id.in_(element_ids),
                        (ReviewTask.target_type == "source_page") & ReviewTask.target_id.in_(page_ids),
                    )
                )
            ).all()
        )
        if review_task_ids:
            session.execute(delete(ReviewTaskComment).where(ReviewTaskComment.review_task_id.in_(review_task_ids)))
            session.execute(delete(ReviewTask).where(ReviewTask.id.in_(review_task_ids)))
        if element_ids:
            session.execute(delete(SourceElement).where(SourceElement.id.in_(element_ids)))
        if page_ids:
            session.execute(delete(SourcePage).where(SourcePage.id.in_(page_ids)))
        session.execute(
            delete(SourceDocumentVersion).where(SourceDocumentVersion.source_document_id == source_document_id)
        )
        session.flush()
        session.delete(document)
        session.commit()
    return {"status": "deleted", "source_document_id": source_document_id}


@router.get("/admin/ingestion-jobs")
def list_ingestion_jobs(request: Request, state: str | None = None) -> dict[str, object]:
    session_factory = get_session_factory(request)
    # Operational endpoint; caller must hold at least one writable domain grant.
    permissions = getattr(request.state, "permissions", None)
    if permissions is not None and not any("write" in grants for grants in permissions.domain_permissions.values()):
        raise HTTPException(status_code=403, detail="missing write access for admin ingestion endpoints")
    service = get_source_ingestion_service(request)
    with session_factory() as session:
        return {
            "jobs": [
                job_to_dict(job)
                for job in service.list_jobs(session, state=state)
                if _can_job_source_permission(session, request, job, action="write")
            ]
        }


@router.get("/jobs")
def list_jobs(request: Request, state: str | None = None) -> dict[str, object]:
    session_factory = get_session_factory(request)
    service = get_source_ingestion_service(request)
    with session_factory() as session:
        return {
            "jobs": [
                job_to_dict(job)
                for job in service.list_jobs(session, state=state)
                if _can_job_source_permission(session, request, job, action="read")
            ]
        }


@router.get("/admin/ingestion-jobs/{job_id}")
def get_ingestion_job(request: Request, job_id: str) -> dict[str, object]:
    session_factory = get_session_factory(request)
    service = get_source_ingestion_service(request)
    try:
        with session_factory() as session:
            job = service.get_job(session, job_id)
            _require_job_source_permission(session, request, job, action="read")
            return {"ingestion_job": job_to_dict(job)}
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> dict[str, object]:
    return get_ingestion_job(request, job_id)


@router.post("/admin/ingestion-jobs/{job_id}/retry")
def retry_ingestion_job(request: Request, job_id: str) -> dict[str, object]:
    session_factory = get_session_factory(request)
    service = get_source_ingestion_service(request)
    try:
        with session_factory() as session:
            job = service.get_job(session, job_id)
            _require_job_source_permission(session, request, job, action="write")
            source_document = session.get(SourceDocument, job.source_document_id)
            if source_document is not None:
                _require_source_extraction_ready(session, source_document)
            job = service.requeue_job(session, job_id)
            session.commit()
            queue_task_id = dispatch_job_or_mark_failed(request, session, job=job)
            return {"ingestion_job": job_to_dict(job), "queue_task_id": queue_task_id}
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RetryNotAllowedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/retry")
def retry_job(request: Request, job_id: str) -> dict[str, object]:
    return retry_ingestion_job(request, job_id)


@router.post("/admin/ingestion-jobs/{job_id}/cancel")
def cancel_ingestion_job(request: Request, job_id: str) -> dict[str, object]:
    session_factory = get_session_factory(request)
    service = get_source_ingestion_service(request)
    try:
        with session_factory() as session:
            job = service.get_job(session, job_id)
            _require_job_source_permission(session, request, job, action="write")
            if get_job_status(job) in INGESTION_JOB_TERMINAL_STATUSES:
                raise HTTPException(status_code=409, detail="ingestion job is not cancellable")
            set_job_status(job, "failed", error_message="cancelled by user")
            metadata = getattr(job, "metadata_", None) or {}
            metadata["cancelled"] = True
            set_if_present(job, "metadata_", metadata)
            session.commit()
            return {"status": "cancelled", "ingestion_job": job_to_dict(job)}
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str) -> dict[str, object]:
    return cancel_ingestion_job(request, job_id)


@router.post("/admin/sources/{source_document_id}/retry")
def retry_latest_source_job(request: Request, source_document_id: str) -> dict[str, object]:
    service = get_source_ingestion_service(request)
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            preview = service.preview(session, source_document_id)
            source_document = preview["source_document"]
            maybe_require_permission(
                request,
                domain_id=source_document.get("domain_id"),
                category=source_document.get("category"),
                sensitivity=source_document.get("sensitivity"),
                action="write",
            )
            _require_source_extraction_ready(session, source_document)
            job = service.requeue_latest_for_source(session, source_document_id)
            session.commit()
            queue_task_id = dispatch_job_or_mark_failed(request, session, job=job)
            return {"ingestion_job": job_to_dict(job), "queue_task_id": queue_task_id}
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RetryNotAllowedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/sources/{source_document_id}/reprocess")
def reprocess_source(request: Request, source_document_id: str) -> dict[str, object]:
    service = get_source_ingestion_service(request)
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            preview = service.preview(session, source_document_id)
            source_document = preview["source_document"]
            maybe_require_permission(
                request,
                domain_id=source_document.get("domain_id"),
                category=source_document.get("category"),
                sensitivity=source_document.get("sensitivity"),
                action="write",
            )
            _require_source_extraction_ready(session, source_document)
            latest_job = service.latest_job_for_source(session, source_document_id)
            latest_job_failed = latest_job is not None and get_job_status(latest_job) == "failed"
            job = service.requeue_or_create_reprocess_job(session, source_document_id)
            session.commit()
            queue_task_id = dispatch_job_or_mark_failed(request, session, job=job, mark_source_failed=latest_job_failed)
            return {"ingestion_job": job_to_dict(job), "queue_task_id": queue_task_id}
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RetryNotAllowedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
