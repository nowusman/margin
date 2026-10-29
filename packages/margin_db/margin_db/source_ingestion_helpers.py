from __future__ import annotations

import mimetypes
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from margin_db.entity_models import Entity, EntityAlias
from margin_db.source_models import DUBLIN_CORE_FIELDS, ConversionQualityReport, IngestionJob, SourceDocument
from margin_db.user_models import Domain
from margin_db.users import ensure_user
from margin_schema.text_normalization import normalize_entity_name

ALL_DOMAINS_ID = "ALL"

SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".dsl": "text/vnd.structurizr",
    ".mmd": "text/vnd.mermaid",
    ".mermaid": "text/vnd.mermaid",
    ".puml": "text/vnd.plantuml",
    ".plantuml": "text/vnd.plantuml",
    ".iuml": "text/vnd.plantuml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
}

SUPPORTED_MEDIA_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/markdown",
    "text/plain",
    "text/vnd.structurizr",
    "text/vnd.mermaid",
    "text/vnd.plantuml",
    "application/yaml",
    "application/json",
    "application/x-yaml",
    "text/yaml",
    "text/x-yaml",
    "image/png",
    "image/jpeg",
    "image/tiff",
    "image/webp",
}

SENSITIVITY_VALUES = {"public", "internal", "confidential", "regulated", "security-sensitive"}


class SourceIngestionError(RuntimeError):
    """Base exception for source ingestion failures."""


class UnsupportedSourceTypeError(SourceIngestionError):
    """Raised when an upload type is outside the Margin ingestion baseline."""


class MetadataCorrectionError(SourceIngestionError):
    """Raised when metadata correction would violate ingestion constraints."""


class SourceNotFoundError(SourceIngestionError):
    """Raised when a source document or ingestion job cannot be found."""


class RetryNotAllowedError(SourceIngestionError):
    """Raised when a non-failed ingestion job is retried."""


def normalize_source_uri(value: str | None) -> str | None:
    """Return the stable, domain-scoped identity for a supplied source URI."""
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return None
    parsed = urlsplit(normalized)
    if not parsed.scheme:
        return normalized
    identity = SplitResult(
        scheme=parsed.scheme.casefold(),
        netloc=parsed.netloc.casefold(),
        path=parsed.path,
        query=parsed.query,
        fragment="",
    )
    return urlunsplit(identity)


def ensure_ingestion_actor(session: Session, actor: str | None) -> str | None:
    return ensure_user(session, actor)


def _conversion_filename(document: SourceDocument) -> str:
    metadata = document.metadata_ or {}
    upload_filename = metadata.get("upload_filename")
    if isinstance(upload_filename, str) and upload_filename.strip():
        return upload_filename
    return document.source_uri or document.title


def make_ingestion_job(session: Session, source_document_id: str) -> Any:
    """Create the next ingestion job for a source document.

    Jobs are sequenced per document, so the history of a document has an
    explicit order even when two jobs are created within the same clock tick.
    """

    session.flush()
    sequence = (
        session.scalar(select(func.count(IngestionJob.id)).where(IngestionJob.source_document_id == source_document_id))
        or 0
    ) + 1
    return IngestionJob(
        source_document_id=source_document_id,
        sequence=sequence,
        status="received",
        retry_count=0,
        max_retries=3,
        metadata_={"quality_report": {}},
    )


def normalize_job_id(job_id: Any) -> Any:
    return job_id


def get_job_status(job: Any) -> str:
    return job.status


def set_job_status(job: Any, status: str, *, error_message: str | None = None) -> None:
    job.transition_to(status, error_message=error_message)


def failed_conversion_quality_report(document: SourceDocument, exc: Exception) -> dict[str, Any]:
    return {
        "status": "failed",
        "converter": "conversion_failed",
        "failure_reason": str(exc),
        "source_document_id": document.id,
        "source_name": document.title,
        "format": document.media_type,
        "text_coverage": 0,
        "table_coverage": 0,
        "image_or_diagram_detected": False,
        "ocr_needed": False,
        "section_tree_confidence": 0,
        "section_count": 0,
        "span_count": 0,
        "warnings": ["conversion_failed"],
    }


def set_if_present(target: Any, attribute: str, value: Any) -> None:
    if hasattr(target, attribute):
        setattr(target, attribute, value)


def set_quality_report(
    session: Session, job: Any, source_document_id: str, source_version_id: str, quality_report: dict[str, Any]
) -> None:
    job.metadata_ = {**(job.metadata_ or {}), "quality_report": quality_report}

    session.add(
        ConversionQualityReport(
            source_document_id=source_document_id,
            source_version_id=source_version_id,
            text_coverage=Decimal(str(quality_report.get("text_coverage", 0))),
            table_coverage=Decimal(str(quality_report.get("table_coverage", 0))),
            image_diagram_detected=bool(quality_report.get("image_or_diagram_detected", False)),
            ocr_needed=bool(quality_report.get("ocr_needed", False)),
            section_tree_confidence=Decimal(str(quality_report.get("section_tree_confidence", 0))),
            warnings=quality_report.get("warnings", []),
            metadata_=quality_report,
        )
    )


def validate_sensitivity(value: str) -> None:
    if value not in SENSITIVITY_VALUES:
        raise MetadataCorrectionError(f"sensitivity must be one of {', '.join(sorted(SENSITIVITY_VALUES))}")


def resolve_domain_id(session: Session, value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized or normalized.casefold() in {"all domain", "all domains", "all"}:
        return ALL_DOMAINS_ID
    if session.get(Domain, normalized) is None:
        domains = session.query(Domain).all()
        if not domains:
            return normalized
        matching = next((d for d in domains if d.name == normalized or d.id == normalized), None)
        if matching is not None:
            return matching.id
        raise MetadataCorrectionError(f"unknown domain: {normalized}")
    return normalized


def resolve_category_id(session: Session, value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        first_category = session.scalar(
            select(Entity.id)
            .where(Entity.ontology_type == "category", Entity.lifecycle_status == "active")
            .order_by(Entity.preferred_label, Entity.id)
            .limit(1)
        )
        if first_category is not None:
            return first_category
        raise MetadataCorrectionError("a canonical category Entity is required before ingesting a source")
    direct = session.get(Entity, normalized)
    if direct is not None:
        if direct.ontology_type != "category" or direct.lifecycle_status != "active":
            raise MetadataCorrectionError(f"category Entity is not active: {normalized}")
        return direct.id
    normalized_name = normalize_entity_name(normalized)
    records = list(
        session.scalars(
            select(Entity).where(
                Entity.ontology_type == "category",
                Entity.lifecycle_status == "active",
                Entity.normalized_label == normalized_name,
            )
        ).all()
    )
    if not records:
        records = list(
            session.scalars(
                select(Entity)
                .join(EntityAlias, EntityAlias.entity_id == Entity.id)
                .where(
                    Entity.ontology_type == "category",
                    Entity.lifecycle_status == "active",
                    EntityAlias.lifecycle_status == "active",
                    EntityAlias.normalized_alias == normalized_name,
                )
            ).all()
        )
    distinct = {record.id: record for record in records}
    if len(distinct) != 1:
        if len(distinct) > 1:
            raise MetadataCorrectionError(f"category alias is ambiguous: {normalized}")
        raise MetadataCorrectionError(f"unknown canonical category: {normalized}")
    return next(iter(distinct))


def validate_source_type(filename: str, media_type: str | None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in SUPPORTED_EXTENSIONS:
        return SUPPORTED_EXTENSIONS[suffix]

    normalized_media_type = (media_type or "").split(";")[0].strip().lower()
    guessed = mimetypes.guess_type(filename)[0]
    if normalized_media_type in SUPPORTED_MEDIA_TYPES:
        return normalized_media_type
    if guessed in SUPPORTED_MEDIA_TYPES:
        return guessed
    raise UnsupportedSourceTypeError(f"unsupported source type for upload: {filename}")


def validate_file_signature(data: bytes, filename: str, media_type: str | None = None) -> str:
    """Validate file signature (magic bytes) against extension and media type.

    Raises UnsupportedSourceTypeError if data is empty or signature is mismatched/corrupted.
    Returns the resolved media type.
    """
    from margin_ingest.converters import validate_file_signature as _validate_sig

    try:
        return _validate_sig(data, filename, media_type)
    except Exception as exc:
        raise UnsupportedSourceTypeError(str(exc)) from exc


def _normalize_dublin_core(dublin_core: dict[str, Any] | None) -> dict[str, Any]:
    values = dublin_core or {}
    normalized: dict[str, Any] = {}
    for field in DUBLIN_CORE_FIELDS:
        value = values.get(field)
        if value is None:
            value = values.get(f"dc:{field}")
        normalized[field] = value
    return normalized


def apply_converted_metadata(document: SourceDocument, converted: Any) -> None:
    source_metadata = converted.metadata.get("source_metadata", {})
    title = source_metadata.get("title")
    if title:
        document.title = str(title)
    document.metadata_ = {**(document.metadata_ or {}), **converted.metadata}
    document.dublin_core = _normalize_dublin_core(converted.metadata.get("dublin_core"))


def job_to_dict(job: Any | None) -> dict[str, Any] | None:
    if job is None:
        return None
    quality_report = (job.metadata_ or {}).get("quality_report", {})
    attempts = (job.retry_count or 0) + (1 if job.started_at else 0)
    finished_at = job.completed_at
    return {
        "id": str(job.id),
        "source_document_id": job.source_document_id,
        "state": get_job_status(job),
        "attempts": attempts,
        "error_message": job.error_message,
        "quality_report": quality_report or {},
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": finished_at.isoformat() if finished_at else None,
    }
