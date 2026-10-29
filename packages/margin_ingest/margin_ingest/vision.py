from __future__ import annotations

import base64
import json
import logging
import math
import os
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from margin_db.base import utc_now
from margin_db.extraction_models import ExtractionRun
from margin_db.model_catalog_models import ModelConfiguration
from margin_db.model_registry import ActiveModelConfig, ModelRegistryError, configured_active_model
from margin_db.review_models import ReviewTask
from margin_db.source_models import (
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourceElementOutcome,
    UnmappedExtraction,
)
from margin_inference import (
    InferenceClient,
    InferencePolicyError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    validate_capabilities,
)
from margin_ingest.contracts import NormalizedElement

logger = logging.getLogger("margin_ingest.vision")

PROMPT_VERSION = "2026-09-07.v1"
SCHEMA_VERSION = "2026-09-07.v1"
DEFAULT_MAX_REQUEST_BYTES = 20 * 1024 * 1024  # 20 MB

VISION_SYSTEM_PROMPT = (
    "You are a precise technical vision and OCR analysis system. "
    "Analyze the provided image region and return ONLY a valid JSON object matching this schema:\n"
    "{\n"
    '  "region_id": "<string identifier>",\n'
    '  "element_kind": "<chart|diagram|screenshot|table|scan|text|figure>",\n'
    '  "transcription": "<exact text transcribed from the image or empty string if none>",\n'
    '  "factual_description": "<objective, factual description of visual elements, layout, relationships, and data>",\n'
    '  "candidate_assertions": ["<factual proposition 1>", "<factual proposition 2>"],\n'
    '  "confidence": <float between 0.0 and 1.0>\n'
    "}\n"
    "Never invent labels, captions, or facts not visible in the image."
)


class VisionError(Exception):
    """Base exception for vision processing errors."""


class VisionConstraintError(VisionError):
    """Raised when a model constraint or policy validation fails prior to execution."""


class VisionResponseParsingError(VisionError):
    """Raised when the vision model response is invalid JSON or missing required fields."""


@dataclass(frozen=True)
class VisionStructuredResult:
    region_id: str
    element_kind: str
    transcription: str
    factual_description: str
    candidate_assertions: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class VisionExecutionRecord:
    provider: str
    model_id: str
    prompt_version: str
    schema_version: str
    asset_checksum: str
    region: dict[str, Any] | None
    raw_response: dict[str, Any]
    parsed_response: dict[str, Any]
    usage: dict[str, int]
    is_derived_visual: bool = True
    model_fingerprint: str | None = None


@dataclass(frozen=True)
class VisionOutcome:
    element_id: str
    status: Literal["success", "quarantined", "skipped"]
    is_retryable: bool = False
    error_kind: str | None = None
    error_message: str | None = None
    result: VisionStructuredResult | None = None
    traceability: VisionExecutionRecord | None = None
    review_task_id: uuid.UUID | None = None


def select_visual_element(element: SourceElement | NormalizedElement) -> tuple[bool, str | None]:
    """Determine if a source or normalized element requires remote vision processing.

    Returns (should_process, selection_reason).
    Only selects unreadable text, charts, diagrams, screenshots, ambiguous tables,
    and difficult scans. If local Docling output is valid, returns (False, None).
    """
    kind = (getattr(element, "kind", None) or "").strip().lower()
    text = (getattr(element, "text", None) or "").strip()
    raw_meta = getattr(element, "metadata_", None)
    if not isinstance(raw_meta, dict):
        candidate = getattr(element, "metadata", None)
        raw_meta = candidate if isinstance(candidate, dict) else {}
    metadata = raw_meta or {}
    confidence = getattr(element, "confidence", None)
    figure_type = str(metadata.get("figure_type") or "").strip().lower()

    # 1. Charts
    if kind in {"chart", "graph"} or figure_type in {"chart", "graph"}:
        return True, "chart"

    # 2. Diagrams
    if (
        kind in {"diagram", "flowchart", "architecture_diagram"}
        or figure_type in {"diagram", "flowchart"}
        or metadata.get("is_diagram") is True
    ):
        return True, "diagram"

    # 3. Screenshots
    if kind in {"screenshot", "screen_capture"} or figure_type == "screenshot" or metadata.get("is_screenshot") is True:
        return True, "screenshot"

    # 4. Ambiguous tables
    if kind == "table" and (
        metadata.get("ambiguous") is True
        or metadata.get("quality_issue") == "ambiguous_table"
        or (not text and bool(getattr(element, "asset_key", None)))
    ):
        return True, "ambiguous_table"

    # 5. Difficult scans
    if (
        kind in {"scan", "difficult_scan"}
        or metadata.get("is_scan") is True
        or metadata.get("scan_quality") == "difficult"
    ):
        return True, "difficult_scan"

    # 6. Unreadable text
    if kind in {"text", "paragraph", "heading"} or not text:
        if metadata.get("unreadable") is True or metadata.get("ocr_status") == "unreadable":
            return True, "unreadable_text"
        if confidence is not None and float(confidence) < 0.5 and getattr(element, "asset_key", None):
            return True, "unreadable_text"

    # 7. Visual pictures / figures lacking local text
    if kind in {"image", "picture", "figure"}:
        if not text or metadata.get("requires_vlm") is True:
            return True, "visual_figure"

    # Local Docling output is valid
    return False, None


def check_vision_model_constraints(
    model: ModelConfiguration | ActiveModelConfig,
    *,
    domain_id: str | None = None,
    sensitivity: str | None = None,
    media_type: str | None = None,
    payload_size_bytes: int = 0,
    require_image_input: bool = True,
) -> None:
    """Enforce model capability, domain, sensitivity, MIME, request size, and health state."""
    status = getattr(model, "status", "active")
    if status != "active":
        raise VisionConstraintError(f"model status is '{status}', expected 'active'")

    last_test_status = getattr(model, "last_test_status", None)
    if last_test_status != "healthy":
        detail = getattr(model, "last_test_detail", None) or "no successful health test recorded"
        if last_test_status == "failed":
            raise VisionConstraintError(f"model health check is in failed state: {detail}")
        raise VisionConstraintError(f"model health check is not healthy: {detail}")

    try:
        capabilities = validate_capabilities(getattr(model, "capabilities", None))
    except InferencePolicyError as exc:
        raise VisionConstraintError(f"model capabilities are invalid: {exc}") from exc

    # Image input capability
    if require_image_input and not capabilities.get("image_input"):
        raise VisionConstraintError("model does not support image input")

    # MIME type support
    supported_mimes = capabilities.get("supported_mime_types")
    if supported_mimes and media_type:
        normalized_mime = media_type.split(";")[0].strip().lower()
        if normalized_mime not in [m.lower() for m in supported_mimes]:
            raise VisionConstraintError(
                f"media type '{normalized_mime}' is not in model supported MIME types: {supported_mimes}"
            )

    # Request size limit
    max_bytes = capabilities.get("max_request_bytes")
    if max_bytes is None:
        try:
            max_bytes = int(os.getenv("MARGIN_VISION_MAX_REQUEST_BYTES", str(DEFAULT_MAX_REQUEST_BYTES)))
        except (TypeError, ValueError) as exc:
            raise VisionConstraintError("MARGIN_VISION_MAX_REQUEST_BYTES must be a non-negative integer") from exc
    if max_bytes < 0:
        raise VisionConstraintError("maximum vision request size must be a non-negative integer")
    if payload_size_bytes > max_bytes:
        raise VisionConstraintError(f"request payload size {payload_size_bytes} exceeds max allowed {max_bytes} bytes")

    # Domain constraints
    allowed_domains = capabilities.get("allowed_domains")
    if allowed_domains and domain_id and domain_id != "all":
        if domain_id not in allowed_domains:
            raise VisionConstraintError(f"domain '{domain_id}' is not in model allowed domains: {allowed_domains}")

    # Sensitivity constraints
    allowed_sensitivities = capabilities.get("allowed_sensitivities")
    if allowed_sensitivities and sensitivity:
        if sensitivity.lower() not in [s.lower() for s in allowed_sensitivities]:
            raise VisionConstraintError(
                f"sensitivity '{sensitivity}' is not in model allowed sensitivities: {allowed_sensitivities}"
            )


def classify_vision_error(exc: Exception) -> tuple[str, bool]:
    """Classify exception into (error_kind, is_retryable)."""
    if isinstance(exc, InferenceTimeoutError):
        return "timeout", True
    if isinstance(exc, InferenceUnavailableError):
        msg = str(exc).lower()
        if "429" in msg or "rate limit" in msg or "too many requests" in msg:
            return "rate_limit", True
        if "invalid json" in msg:
            return "invalid_response", False
        return "provider_rejection", False
    if isinstance(exc, VisionResponseParsingError | json.JSONDecodeError):
        return "invalid_response", False
    if isinstance(exc, VisionConstraintError | InferencePolicyError):
        msg = str(exc).lower()
        if "mime" in msg or "size" in msg:
            return "unsupported_content", False
        return "provider_rejection", False
    return "terminal_error", False


def parse_vision_structured_response(
    content: str, *, fallback_region_id: str = "", fallback_element_kind: str = "visual"
) -> VisionStructuredResult:
    """Parse and validate structured JSON response from vision model."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VisionResponseParsingError(f"invalid JSON from vision model: {exc}") from exc

    if not isinstance(data, dict):
        raise VisionResponseParsingError("vision response is not a JSON object")

    region_id = str(data.get("region_id") or fallback_region_id)
    element_kind = str(data.get("element_kind") or fallback_element_kind)
    transcription = str(data.get("transcription") or "").strip()
    factual_description = str(data.get("factual_description") or "").strip()

    assertions_raw = data.get("candidate_assertions")
    if assertions_raw is not None and not isinstance(assertions_raw, list):
        raise VisionResponseParsingError("candidate_assertions must be a list")
    candidate_assertions_list: list[str] = []
    seen_assertions: set[str] = set()
    for item in assertions_raw or []:
        if not isinstance(item, str):
            raise VisionResponseParsingError("candidate_assertions must contain strings")
        assertion = item.strip()
        if assertion and assertion not in seen_assertions:
            candidate_assertions_list.append(assertion)
            seen_assertions.add(assertion)
    candidate_assertions = tuple(candidate_assertions_list)

    conf_val = data.get("confidence")
    if conf_val is None:
        raise VisionResponseParsingError("missing required field 'confidence'")
    try:
        confidence = float(conf_val)
    except (ValueError, TypeError) as exc:
        raise VisionResponseParsingError(f"invalid confidence value: {conf_val}") from exc
    if not math.isfinite(confidence):
        raise VisionResponseParsingError("confidence must be a finite number")
    confidence = max(0.0, min(1.0, confidence))

    if not transcription and not factual_description and not candidate_assertions:
        raise VisionResponseParsingError("vision response has empty transcription, description, and assertions")

    return VisionStructuredResult(
        region_id=region_id,
        element_kind=element_kind,
        transcription=transcription,
        factual_description=factual_description,
        candidate_assertions=candidate_assertions,
        confidence=confidence,
    )


def detect_image_media_type(data: bytes, fallback: str = "image/png") -> str:
    """Detect image media type from magic bytes."""
    if len(data) >= 8 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 3 and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 4 and (
        data.startswith(b"II*\x00")
        or data.startswith(b"MM\x00*")
        or data.startswith(b"II+\x00")
        or data.startswith(b"MM\x00+")
    ):
        return "image/tiff"
    if len(data) >= 6 and data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if len(data) >= 5 and data.startswith(b"%PDF-"):
        return "application/pdf"
    return fallback


def quarantine_element(
    session: Session | None,
    element: SourceElement,
    *,
    reason: str,
    error_kind: str,
    is_retryable: bool,
    source_document_id: str | None = None,
    source_version_id: str | None = None,
    extraction_run_id: str | None = None,
    model_id: str | None = None,
) -> ReviewTask | None:
    """Quarantine an element that cannot complete visual processing and create a ReviewTask.

    Never substitutes placeholder captions or invented text into element.text.
    """
    element_metadata = dict(element.metadata_ or {})
    element_metadata["quarantined"] = True
    element_metadata["quarantine_reason"] = reason
    element_metadata["error_kind"] = error_kind
    element_metadata["retryable"] = is_retryable
    element.metadata_ = element_metadata

    if session is None:
        return None

    # Find or create an open ReviewTask for this element
    existing_task = session.scalar(
        select(ReviewTask).where(
            ReviewTask.task_type == "vision_quarantine",
            ReviewTask.target_type == "source_element",
            ReviewTask.target_id == element.id,
            ReviewTask.status.in_(("open", "assigned", "needs_evidence")),
        )
    )
    if existing_task is not None:
        existing_task.reason = reason
        existing_evidence = list(existing_task.evidence or [])
        evidence_entry = {
            "element_id": element.id,
            "asset_key": element.asset_key,
            "error_kind": error_kind,
            "detail": reason,
        }
        if evidence_entry not in existing_evidence:
            existing_evidence.append(evidence_entry)
        existing_task.evidence = existing_evidence
        existing_task.metadata_ = {
            **(existing_task.metadata_ or {}),
            "source_document_id": source_document_id,
            "source_version_id": source_version_id or getattr(element, "source_version_id", None),
            "element_id": element.id,
            "retryable": is_retryable,
            "error_kind": error_kind,
        }
        review_task = existing_task
    else:
        review_task = ReviewTask(
            task_type="vision_quarantine",
            target_type="source_element",
            target_id=element.id,
            reason=reason,
            status="open",
            priority="medium",
            evidence=[
                {"element_id": element.id, "asset_key": element.asset_key, "error_kind": error_kind, "detail": reason}
            ],
            metadata_={
                "source_document_id": source_document_id,
                "source_version_id": source_version_id or getattr(element, "source_version_id", None),
                "element_id": element.id,
                "retryable": is_retryable,
                "error_kind": error_kind,
            },
        )
        session.add(review_task)

    if extraction_run_id:
        _upsert_element_outcome(
            session,
            extraction_run_id=extraction_run_id,
            source_element_id=element.id,
            outcome="quarantined",
            reason=reason,
            extractor=model_id or "vision_ocr",
            model_id=model_id,
            attempt_metadata={"error_kind": error_kind, "retryable": is_retryable},
        )

    return review_task


def _element_outcome_id(extraction_run_id: str, source_element_id: str) -> str:
    return "element_outcome_" + sha256(f"{extraction_run_id}\0{source_element_id}".encode()).hexdigest()


def _upsert_element_outcome(
    session: Session,
    *,
    extraction_run_id: str,
    source_element_id: str,
    outcome: str,
    reason: str | None,
    extractor: str,
    model_id: str | None,
    attempt_metadata: dict[str, Any],
) -> SourceElementOutcome:
    outcome_row = session.get(SourceElementOutcome, _element_outcome_id(extraction_run_id, source_element_id))
    if outcome_row is None:
        outcome_row = SourceElementOutcome(
            id=_element_outcome_id(extraction_run_id, source_element_id),
            extraction_run_id=extraction_run_id,
            source_element_id=source_element_id,
        )
        session.add(outcome_row)
    outcome_row.outcome = outcome
    outcome_row.reason = reason
    outcome_row.extractor = extractor
    outcome_row.model_id = model_id
    outcome_row.prompt_version = PROMPT_VERSION
    outcome_row.attempt_metadata = dict(attempt_metadata)
    return outcome_row


def _close_recovered_visual_tasks(session: Session, *, source_element_id: str) -> None:
    tasks = session.scalars(
        select(ReviewTask).where(
            ReviewTask.target_type == "source_element",
            ReviewTask.target_id == source_element_id,
            ReviewTask.task_type.in_(("vision_quarantine", "source_element_triage", "source_element_outcome_review")),
            ReviewTask.status.in_(("open", "assigned", "needs_evidence")),
        )
    ).all()
    for task in tasks:
        task.status = "closed"
        task.resolution = "resolved by successful visual processing"
        task.closed_at = utc_now()


def _visual_assertion_id(source_version_id: str, source_element_id: str, assertion: str) -> str:
    value = f"vision_assertion\0{source_version_id}\0{source_element_id}\0{assertion}"
    return "unmapped_" + sha256(value.encode()).hexdigest()


def _model_fingerprint(model: ModelConfiguration | ActiveModelConfig) -> str:
    payload = {
        "purpose": getattr(model, "purpose", None),
        "runtime": getattr(model, "runtime", None),
        "model_name": getattr(model, "model_name", None),
        "endpoint_url": getattr(model, "endpoint_url", None),
        "secret_reference": getattr(model, "secret_reference", None),
        "capabilities": getattr(model, "capabilities", None),
        "max_tokens": getattr(model, "max_tokens", None),
        "temperature_default": getattr(model, "temperature_default", None),
        "system_prompt": getattr(model, "system_prompt", None),
    }
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _upsert_visual_assertion(
    session: Session,
    *,
    source_document: SourceDocument,
    element: SourceElement,
    extraction_run_id: str | None,
    model_id: str,
    assertion: str,
    confidence: float,
) -> None:
    raw_payload = {
        "statement": assertion,
        "element_id": element.id,
        "confidence": confidence,
        "derived_visual": True,
        "status": "pending_review",
    }
    row_id = _visual_assertion_id(element.source_version_id, element.id, assertion)
    row = session.get(UnmappedExtraction, row_id)
    if row is None:
        session.add(
            UnmappedExtraction(
                id=row_id,
                source_document_id=source_document.id,
                source_version_id=element.source_version_id,
                source_element_id=element.id,
                extraction_run_id=extraction_run_id,
                kind="claim",
                reason="visual candidate assertion requires review",
                raw_payload=raw_payload,
                model_id=model_id,
                prompt_version=PROMPT_VERSION,
                status="open",
            )
        )
    elif row.status == "open":
        row.extraction_run_id = extraction_run_id
        row.raw_payload = raw_payload
        row.model_id = model_id
        row.prompt_version = PROMPT_VERSION


def _cached_vision_result(
    element: SourceElement, *, image_bytes: bytes, model: ModelConfiguration | ActiveModelConfig | None
) -> tuple[VisionStructuredResult, VisionExecutionRecord] | None:
    metadata = element.metadata_ or {}
    if not isinstance(metadata, dict) or metadata.get("quarantined"):
        return None
    trace = metadata.get("vision_traceability")
    if not isinstance(trace, dict):
        return None
    expected_model_id = getattr(model, "id", None)
    cached_model_id = trace.get("model_id")
    if not isinstance(cached_model_id, str) or (expected_model_id and cached_model_id != expected_model_id):
        return None
    if model is not None and (
        getattr(model, "last_test_status", None) != "healthy"
        or trace.get("model_fingerprint") != _model_fingerprint(model)
    ):
        return None
    if (
        trace.get("prompt_version") != PROMPT_VERSION
        or trace.get("schema_version") != SCHEMA_VERSION
        or trace.get("asset_checksum") != sha256(image_bytes).hexdigest()
    ):
        return None
    parsed_payload = trace.get("parsed_response")
    raw_response = trace.get("raw_response")
    usage = trace.get("usage")
    if not isinstance(parsed_payload, dict) or not isinstance(raw_response, dict) or not isinstance(usage, dict):
        return None
    try:
        parsed = parse_vision_structured_response(
            json.dumps(parsed_payload), fallback_region_id=element.id, fallback_element_kind=element.kind
        )
        usage_payload = {key: int(usage[key]) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    except (TypeError, ValueError, KeyError, VisionError):
        return None
    region = trace.get("region")
    if region is not None and not isinstance(region, dict):
        return None
    return parsed, VisionExecutionRecord(
        provider=str(trace.get("provider") or "openai-compatible"),
        model_id=cached_model_id,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        asset_checksum=trace["asset_checksum"],
        region=region,
        raw_response=raw_response,
        parsed_response=parsed_payload,
        usage=usage_payload,
        is_derived_visual=bool(trace.get("is_derived_visual", True)),
        model_fingerprint=trace.get("model_fingerprint") if isinstance(trace.get("model_fingerprint"), str) else None,
    )


def _restore_cached_vision_success(
    element: SourceElement,
    *,
    parsed: VisionStructuredResult,
    traceability: VisionExecutionRecord,
    source_document: SourceDocument | None,
    session: Session | None,
    extraction_run_id: str | None,
) -> None:
    element_metadata = dict(element.metadata_ or {})
    element_metadata["quarantined"] = False
    element_metadata["candidate_assertions"] = list(parsed.candidate_assertions)
    element.metadata_ = element_metadata
    if session is not None and source_document is not None:
        for assertion in parsed.candidate_assertions:
            _upsert_visual_assertion(
                session,
                source_document=source_document,
                element=element,
                extraction_run_id=extraction_run_id,
                model_id=traceability.model_id,
                assertion=assertion,
                confidence=parsed.confidence,
            )
        _close_recovered_visual_tasks(session, source_element_id=element.id)
    if session is not None and extraction_run_id:
        _upsert_element_outcome(
            session,
            extraction_run_id=extraction_run_id,
            source_element_id=element.id,
            outcome="extracted",
            reason=None,
            extractor=traceability.model_id,
            model_id=traceability.model_id,
            attempt_metadata={"is_derived_visual": True, "confidence": parsed.confidence, "cached": True},
        )


def ensure_vision_ingest_run(
    session: Session,
    *,
    source_document: SourceDocument,
    source_version: SourceDocumentVersion,
    model_id: str | None = None,
) -> ExtractionRun:
    """Create or reuse the durable ingest-scoped ExtractionRun for visual outcomes.

    SourceElementOutcome requires a non-null extraction_run_id. Vision quarantine
    happens during ingestion before claim extraction, so this run is the stable
    accounting anchor for ingest-time visual outcomes.
    """
    run_id = "run_" + sha256(f"vision_ingest\0{source_version.id}".encode()).hexdigest()
    existing = session.get(ExtractionRun, run_id)
    if existing is not None:
        return existing
    run = ExtractionRun(
        id=run_id,
        source_document_id=source_document.id,
        source_version_id=source_version.id,
        mode="vision_ingest",
        status="completed",
        extractor="vision_ocr",
        model_id=model_id,
        prompt_version=PROMPT_VERSION,
        expected_window_count=0,
        processed_window_count=0,
        metadata_={"purpose": "ingest_visual_processing"},
        completed_at=utc_now(),
    )
    try:
        with session.begin_nested():
            session.add(run)
            session.flush()
    except IntegrityError:
        # Concurrent ingest for the same version created the durable run first.
        existing = session.get(ExtractionRun, run_id)
        if existing is None:
            raise
        return existing
    return run


def process_single_element_vision(
    element: SourceElement,
    *,
    model: ModelConfiguration | ActiveModelConfig | None,
    client: InferenceClient | None,
    image_bytes: bytes | None,
    media_type: str,
    source_document: SourceDocument | None = None,
    session: Session | None = None,
    extraction_run_id: str | None = None,
    model_error: str | None = None,
    model_error_kind: str = "missing_model",
) -> VisionOutcome:
    """Process a single visual element with remote vision processing."""
    if image_bytes:
        cached = _cached_vision_result(element, image_bytes=image_bytes, model=model)
        if cached is not None:
            parsed, traceability = cached
            _restore_cached_vision_success(
                element,
                parsed=parsed,
                traceability=traceability,
                source_document=source_document,
                session=session,
                extraction_run_id=extraction_run_id,
            )
            return VisionOutcome(element_id=element.id, status="success", result=parsed, traceability=traceability)

    # 1. Missing model / client check
    if model is None or client is None:
        error_message = model_error or "no active vision_ocr model configured in model registry"
        task = quarantine_element(
            session,
            element,
            reason=error_message,
            error_kind=model_error_kind,
            is_retryable=False,
            source_document_id=getattr(source_document, "id", None),
            source_version_id=getattr(element, "source_version_id", None),
            extraction_run_id=extraction_run_id,
            model_id=getattr(model, "id", None),
        )
        return VisionOutcome(
            element_id=element.id,
            status="quarantined",
            is_retryable=False,
            error_kind=model_error_kind,
            error_message=error_message,
            review_task_id=getattr(task, "id", None),
        )

    # 2. Missing image bytes check
    if not image_bytes:
        task = quarantine_element(
            session,
            element,
            reason=f"visual asset data not found for element {element.id}",
            error_kind="unsupported_content",
            is_retryable=False,
            source_document_id=getattr(source_document, "id", None),
            source_version_id=getattr(element, "source_version_id", None),
            extraction_run_id=extraction_run_id,
            model_id=getattr(model, "id", None),
        )
        return VisionOutcome(
            element_id=element.id,
            status="quarantined",
            is_retryable=False,
            error_kind="unsupported_content",
            error_message=f"visual asset data not found for element {element.id}",
            review_task_id=getattr(task, "id", None),
        )

    # 3. Model constraint checks
    domain_id = getattr(source_document, "domain_id", None)
    sensitivity = getattr(source_document, "sensitivity", None)
    try:
        check_vision_model_constraints(
            model,
            domain_id=domain_id,
            sensitivity=sensitivity,
            media_type=media_type,
            payload_size_bytes=len(image_bytes),
        )
    except VisionConstraintError as exc:
        error_kind, is_retryable = classify_vision_error(exc)
        task = quarantine_element(
            session,
            element,
            reason=str(exc),
            error_kind=error_kind,
            is_retryable=is_retryable,
            source_document_id=getattr(source_document, "id", None),
            source_version_id=getattr(element, "source_version_id", None),
            extraction_run_id=extraction_run_id,
            model_id=getattr(model, "id", None),
        )
        return VisionOutcome(
            element_id=element.id,
            status="quarantined",
            is_retryable=is_retryable,
            error_kind=error_kind,
            error_message=str(exc),
            review_task_id=getattr(task, "id", None),
        )

    # 4. Invoke inference client
    encoded = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{media_type};base64,{encoded}"
    messages = [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Analyze region '{element.id}' of kind '{element.kind}'. Provide structured extraction.",
                },
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    max_tokens = getattr(model, "max_tokens", 4096) or 4096
    try:
        response = client.chat_completions(messages=messages, max_tokens=max_tokens, event="vision_ocr")
        parsed = parse_vision_structured_response(
            response.content, fallback_region_id=element.id, fallback_element_kind=element.kind
        )
    except Exception as exc:
        error_kind, is_retryable = classify_vision_error(exc)
        task = quarantine_element(
            session,
            element,
            reason=f"vision processing failed: {exc}",
            error_kind=error_kind,
            is_retryable=is_retryable,
            source_document_id=getattr(source_document, "id", None),
            source_version_id=getattr(element, "source_version_id", None),
            extraction_run_id=extraction_run_id,
            model_id=getattr(model, "id", None),
        )
        return VisionOutcome(
            element_id=element.id,
            status="quarantined",
            is_retryable=is_retryable,
            error_kind=error_kind,
            error_message=str(exc),
            review_task_id=getattr(task, "id", None),
        )

    # 5. Build traceability record
    checksum = sha256(image_bytes).hexdigest()
    model_id = getattr(model, "id", "vision_ocr")
    provider = getattr(model, "runtime", "openai-compatible")
    traceability = VisionExecutionRecord(
        provider=provider,
        model_id=model_id,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        asset_checksum=checksum,
        region=element.bounding_box,
        raw_response=response.raw_response,
        parsed_response={
            "region_id": parsed.region_id,
            "element_kind": parsed.element_kind,
            "transcription": parsed.transcription,
            "factual_description": parsed.factual_description,
            "candidate_assertions": list(parsed.candidate_assertions),
            "confidence": parsed.confidence,
        },
        usage={
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        is_derived_visual=True,
        model_fingerprint=_model_fingerprint(model),
    )

    # 6. Update SourceElement
    if parsed.transcription and parsed.factual_description:
        element.text = f"{parsed.transcription}\n\n[Visual Description]: {parsed.factual_description}"
    else:
        element.text = parsed.transcription or parsed.factual_description

    element.confidence = Decimal(str(round(parsed.confidence, 4)))
    element.converter_method = "vision_ocr"
    element.converter_version = PROMPT_VERSION

    element_metadata = dict(element.metadata_ or {})
    element_metadata["transcription"] = parsed.transcription
    element_metadata["factual_description"] = parsed.factual_description
    element_metadata["candidate_assertions"] = list(parsed.candidate_assertions)
    element_metadata["vision_traceability"] = asdict(traceability)
    element_metadata["is_derived_visual"] = True
    element_metadata["evidence_kind"] = "derived_visual"
    element_metadata["quarantined"] = False
    element.metadata_ = element_metadata

    # 7. Persist candidate assertions as unmapped extractions
    if session is not None and source_document is not None:
        for assertion in parsed.candidate_assertions:
            _upsert_visual_assertion(
                session,
                source_document=source_document,
                element=element,
                extraction_run_id=extraction_run_id,
                model_id=model_id,
                assertion=assertion,
                confidence=parsed.confidence,
            )
        _close_recovered_visual_tasks(session, source_element_id=element.id)

    # 8. Record outcome if extraction run is present
    if session is not None and extraction_run_id:
        _upsert_element_outcome(
            session,
            extraction_run_id=extraction_run_id,
            source_element_id=element.id,
            outcome="extracted",
            reason=None,
            extractor=model_id,
            model_id=model_id,
            attempt_metadata={"is_derived_visual": True, "confidence": parsed.confidence},
        )

    return VisionOutcome(element_id=element.id, status="success", result=parsed, traceability=traceability)


def process_version_visual_elements(
    session: Session,
    *,
    source_document: SourceDocument,
    source_version: SourceDocumentVersion,
    object_store: Any,
    source_bytes: bytes | None = None,
    visual_assets: Sequence[Any] | None = None,
    extraction_run_id: str | None = None,
) -> list[VisionOutcome]:
    """Execute targeted visual/OCR processing on selected elements of a source version."""
    elements = list(
        session.scalars(
            select(SourceElement)
            .where(SourceElement.source_version_id == source_version.id)
            .order_by(SourceElement.ordinal)
        ).all()
    )

    # Select only targeted elements
    selected_elements: list[SourceElement] = []
    for element in elements:
        should_process, _ = select_visual_element(element)
        if should_process:
            selected_elements.append(element)

    if not selected_elements:
        return []

    # Resolve active vision_ocr model once per job
    active_model: ActiveModelConfig | None = None
    client: InferenceClient | None = None
    model_config: ModelConfiguration | ActiveModelConfig | None = None
    model_error: str | None = None
    model_error_kind = "missing_model"
    try:
        active_model = configured_active_model(session, "vision_ocr")
    except ModelRegistryError as exc:
        model_error = str(exc)
        model_error_kind = "provider_rejection"
    if active_model is not None:
        model_row = session.get(ModelConfiguration, active_model.id)
        model_config = model_row if model_row is not None else active_model
        try:
            client = InferenceClient(
                base_url=active_model.endpoint_url,
                model=active_model.model_name,
                secret_reference=active_model.secret_reference,
                timeout_seconds=getattr(active_model, "timeout_seconds", 120) or 120,
            )
        except InferencePolicyError as exc:
            model_error = str(exc)
            model_error_kind = "provider_rejection"

    resolved_run_id = extraction_run_id
    if resolved_run_id is None:
        resolved_run_id = ensure_vision_ingest_run(
            session,
            source_document=source_document,
            source_version=source_version,
            model_id=getattr(model_config, "id", None),
        ).id

    # Map visual assets by asset_key / asset_id
    visual_assets_by_key: dict[str, bytes] = {}
    if visual_assets:
        for asset in visual_assets:
            data = getattr(asset, "data", None)
            key = getattr(asset, "asset_key", None) or getattr(asset, "asset_id", None)
            if key and data:
                visual_assets_by_key[key] = data

    outcomes: list[VisionOutcome] = []
    for element in selected_elements:
        # Retrieve image bytes
        image_bytes: bytes | None = None
        if element.asset_key and element.asset_key in visual_assets_by_key:
            image_bytes = visual_assets_by_key[element.asset_key]
        elif element.asset_key and object_store is not None:
            try:
                image_bytes = object_store.get_bytes(element.asset_key)
            except Exception:
                image_bytes = None

        if image_bytes is None:
            doc_media = getattr(source_document, "media_type", "") or ""
            if doc_media.startswith("image/") and source_bytes:
                image_bytes = source_bytes
            elif (
                doc_media.startswith("image/") and object_store is not None and getattr(source_document, "sha256", None)
            ):
                try:
                    image_bytes = object_store.get_bytes(source_document.sha256)
                except Exception:
                    image_bytes = None

        media_type = (
            detect_image_media_type(
                image_bytes, fallback=getattr(source_document, "media_type", "image/png") or "image/png"
            )
            if image_bytes
            else getattr(source_document, "media_type", "image/png") or "image/png"
        )

        outcome = process_single_element_vision(
            element,
            model=model_config,
            client=client,
            image_bytes=image_bytes,
            media_type=media_type,
            source_document=source_document,
            session=session,
            extraction_run_id=resolved_run_id,
            model_error=model_error,
            model_error_kind=model_error_kind,
        )
        outcomes.append(outcome)

    session.flush()
    return outcomes
