from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from margin_db.object_storage import ContentAddressableObjectStore, sha256_bytes
from margin_db.review_models import ReviewTask
from margin_db.source_models import SourceElement, SourceElementOutcome, SourcePage, SourcePageOutcome
from margin_ingest.contracts import NormalizedElement, NormalizedPage
from margin_schema.ids import element_id


class SourceManifestError(ValueError):
    """Raised when source structure cannot be persisted unambiguously."""


def _string_key_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _object_items(value: object) -> tuple[object, ...]:
    return tuple(value) if isinstance(value, list) else ()


def validate_persisted_source_manifest(
    session: Session,
    *,
    source_version_id: str,
    document_text: str,
    expected_manifest: dict[str, object] | None = None,
    object_store: ContentAddressableObjectStore | None = None,
) -> tuple[str, ...]:
    """Validate the persisted page/element graph before extraction can publish it."""

    errors: list[str] = []
    pages = list(
        session.query(SourcePage)
        .filter(SourcePage.source_version_id == source_version_id)
        .order_by(SourcePage.page_number)
        .all()
    )
    elements = list(
        session.query(SourceElement)
        .filter(SourceElement.source_version_id == source_version_id)
        .order_by(SourceElement.ordinal, SourceElement.id)
        .all()
    )
    page_by_id = {page.id: page for page in pages}
    element_by_id = {element.id: element for element in elements}
    if len(page_by_id) != len(pages):
        errors.append("persisted source pages are not unique")
    if len(element_by_id) != len(elements):
        errors.append("persisted source elements are not unique")
    if [page.page_number for page in pages] != list(range(1, len(pages) + 1)):
        errors.append("persisted source pages are not contiguous")
    if [element.ordinal for element in elements] != list(range(len(elements))):
        errors.append("persisted source element ordinals are not contiguous")

    expected_pages = expected_manifest.get("pages") if expected_manifest else None
    if isinstance(expected_pages, list) and len(pages) != len(expected_pages):
        errors.append("persisted page count differs from converter manifest")
    if isinstance(expected_pages, list):
        expected_pages_by_number = {
            int(item["page_number"]): item
            for item in expected_pages
            if isinstance(item, dict) and isinstance(item.get("page_number"), int)
        }
        for page_number, expected in expected_pages_by_number.items():
            actual = next((page for page in pages if page.page_number == page_number), None)
            if actual is None:
                continue
            if expected.get("checksum") != actual.content_checksum:
                errors.append(f"page {page_number} checksum differs from converter manifest")
            expected_element_ids = {
                _persisted_element_id(source_version_id, str(value))
                for value in expected.get("element_ids", [])
                if value
            }
            actual_element_ids = {element.id for element in elements if element.source_page_id == actual.id}
            if expected_element_ids != actual_element_ids:
                errors.append(f"page {page_number} element membership differs from converter manifest")
    expected_elements = expected_manifest.get("elements") if expected_manifest else None
    if isinstance(expected_elements, list):
        expected_ids = {
            _persisted_element_id(source_version_id, str(item.get("id")))
            for item in expected_elements
            if isinstance(item, dict) and item.get("id")
        }
        if set(element_by_id) != expected_ids:
            errors.append("persisted element ids differ from converter manifest")

    manifest_asset_keys = expected_manifest.get("asset_keys") if expected_manifest else None
    asset_keys = {str(value) for value in _object_items(manifest_asset_keys) if value}
    for element in elements:
        if element.parent_element_id is not None:
            parent = element_by_id.get(element.parent_element_id)
            if parent is None:
                errors.append(f"element {element.id} has an orphan parent link")
        if element.source_page_id is not None and element.source_page_id not in page_by_id:
            errors.append(f"element {element.id} has an orphan page link")
        if element.page_number is not None:
            page = next((candidate for candidate in pages if candidate.page_number == element.page_number), None)
            if page is None or element.source_page_id != page.id:
                errors.append(f"element {element.id} has inconsistent page placement")
        if (element.char_start is None) != (element.char_end is None):
            errors.append(f"element {element.id} has an incomplete evidence offset")
        if element.char_start is not None and element.char_end is not None:
            if element.char_start < 0 or element.char_end < element.char_start or element.char_end > len(document_text):
                errors.append(f"element {element.id} has an invalid evidence offset")
        if element.text:
            if element.checksum != sha256_bytes(element.text.encode("utf-8")):
                errors.append(f"element {element.id} checksum does not match persisted text")
        elif element.checksum is not None:
            errors.append(f"element {element.id} has a checksum without persisted text")
        if element.asset_key:
            if asset_keys and element.asset_key not in asset_keys:
                errors.append(f"element {element.id} references an unmanifested asset")
            if object_store is not None:
                try:
                    if not object_store.exists(element.asset_key):
                        errors.append(f"element {element.id} references a missing asset object")
                except (TypeError, ValueError):
                    errors.append(f"element {element.id} has an invalid asset object key")
        bounding_box = element.bounding_box
        if bounding_box is not None:
            coords = bounding_box.get("coords") if isinstance(bounding_box, dict) else None
            if (
                not isinstance(coords, list | tuple)
                or len(coords) != 4
                or not all(isinstance(value, int | float) for value in coords)
                or any(value < 0 for value in coords)
            ):
                errors.append(f"element {element.id} has an invalid visual region")

    if expected_elements and isinstance(expected_elements, list):
        expected_by_id = {
            _persisted_element_id(source_version_id, str(item.get("id"))): item
            for item in expected_elements
            if isinstance(item, dict) and item.get("id")
        }
        for persisted_id, expected in expected_by_id.items():
            actual = element_by_id.get(persisted_id)
            if actual is None:
                continue
            if expected.get("char_start") != actual.char_start or expected.get("char_end") != actual.char_end:
                errors.append(f"element {persisted_id} evidence offsets differ from converter manifest")
            if expected.get("asset_key") != actual.asset_key:
                errors.append(f"element {persisted_id} asset reference differs from converter manifest")
            if expected.get("kind") != actual.kind or expected.get("ordinal") != actual.ordinal:
                errors.append(f"element {persisted_id} structural fields differ from converter manifest")
            expected_parent_id = expected.get("parent_id")
            if expected_parent_id:
                expected_parent_id = _persisted_element_id(source_version_id, str(expected_parent_id))
            if expected.get("page_number") != actual.page_number or expected_parent_id != actual.parent_element_id:
                errors.append(f"element {persisted_id} hierarchy differs from converter manifest")
    return tuple(dict.fromkeys(errors))


def persist_source_manifest(session: Session, *, source_version_id: str, elements: object, pages: object) -> None:
    page_id_by_number = _persist_pages(session, source_version_id=source_version_id, pages=pages)
    session.flush()
    _persist_elements(
        session, source_version_id=source_version_id, elements=elements, page_id_by_number=page_id_by_number
    )
    session.flush()


def persist_source_page_outcomes(
    session: Session, *, outcomes: Sequence[SourcePageOutcome | dict[str, Any]]
) -> list[SourcePageOutcome]:
    persisted: list[SourcePageOutcome] = []
    for raw in outcomes:
        if isinstance(raw, SourcePageOutcome):
            outcome_id = raw.id
            existing = session.get(SourcePageOutcome, outcome_id) if outcome_id else None
            if existing is not None:
                existing.outcome = raw.outcome
                existing.reason = raw.reason
                existing.attempt_count = raw.attempt_count
                existing.processor = raw.processor
                existing.processor_version = raw.processor_version
                existing.idempotency_key = raw.idempotency_key
                existing.result = raw.result
                existing.metadata_ = raw.metadata_
                persisted.append(existing)
            else:
                session.add(raw)
                persisted.append(raw)
        elif isinstance(raw, dict):
            outcome_id = raw.get("id")
            existing = session.get(SourcePageOutcome, outcome_id) if outcome_id else None
            if existing is not None:
                for k, v in raw.items():
                    if k != "id" and hasattr(existing, k):
                        setattr(existing, k, v)
                persisted.append(existing)
            else:
                record = SourcePageOutcome(**raw)
                session.add(record)
                persisted.append(record)
    session.flush()
    return persisted


def persist_source_element_outcomes(
    session: Session, *, outcomes: Sequence[SourceElementOutcome | dict[str, Any]]
) -> list[SourceElementOutcome]:
    persisted: list[SourceElementOutcome] = []
    for raw in outcomes:
        if isinstance(raw, SourceElementOutcome):
            session.add(raw)
            persisted.append(raw)
        elif isinstance(raw, dict):
            record = SourceElementOutcome(**raw)
            session.add(record)
            persisted.append(record)
    session.flush()
    return persisted


def _persisted_element_id(source_version_id: str, native_element_id: str) -> str:
    """Keep database source-element IDs unique across immutable source versions."""
    return element_id({"source_version_id": source_version_id, "native_element_id": native_element_id})


def _persist_pages(session: Session, *, source_version_id: str, pages: object) -> dict[int, str]:
    if pages is None:
        pages = []
    if not isinstance(pages, list | tuple):
        raise SourceManifestError("source index returned an invalid page manifest")

    page_id_by_number: dict[int, str] = {}
    if not pages:
        page_number = 1
        page_id = "page_" + sha256_bytes(f"{source_version_id}\0{page_number}".encode())
        page_id_by_number[page_number] = page_id
        session.add(
            SourcePage(
                id=page_id,
                source_version_id=source_version_id,
                page_number=page_number,
                content_checksum=sha256_bytes(f"{source_version_id}:logical_page_1".encode()),
                classification="text_extracted",
                metadata_={"logical": True},
            )
        )
        return page_id_by_number

    for raw in pages:
        if isinstance(raw, NormalizedPage):
            page_number = raw.page_number
            classification = str(raw.metadata.get("classification") or "text_extracted")
            checksum = raw.checksum or sha256_bytes(f"missing:{page_number}".encode())
            page_metadata = raw.metadata
        elif isinstance(raw, dict) and isinstance(raw.get("page_number"), int):
            page_number = int(raw["page_number"])
            classification = str(raw.get("classification") or "unsupported")
            checksum = str(raw.get("checksum") or sha256_bytes(f"missing:{page_number}".encode()))
            page_metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        else:
            raise SourceManifestError("source page manifest contains an invalid page")

        page_id = "page_" + sha256_bytes(f"{source_version_id}\0{page_number}".encode())
        page_id_by_number[page_number] = page_id
        existing_page = session.get(SourcePage, page_id)
        if existing_page is not None:
            existing_page.content_checksum = checksum
            existing_page.classification = classification
            existing_page.metadata_ = page_metadata
        else:
            session.add(
                SourcePage(
                    id=page_id,
                    source_version_id=source_version_id,
                    page_number=page_number,
                    content_checksum=checksum,
                    classification=classification,
                    metadata_=page_metadata,
                )
            )
        if classification == "unsupported":
            session.add(
                ReviewTask(
                    task_type="source_page_review",
                    target_type="source_page",
                    target_id=page_id,
                    reason="configured pipeline cannot interpret the source page",
                    status="open",
                    priority="medium",
                )
            )
    return page_id_by_number


def _persist_elements(
    session: Session, *, source_version_id: str, elements: object, page_id_by_number: dict[int, str]
) -> None:
    if not isinstance(elements, list | tuple):
        raise SourceManifestError("source index did not return a native element manifest")
    raw_elements = list(elements)
    default_page_id = page_id_by_number.get(1) if page_id_by_number else None
    persisted_element_ids: set[str] = set()
    persisted_id_by_native_id: dict[str, str] = {}

    for ordinal, raw in enumerate(raw_elements):
        if isinstance(raw, NormalizedElement):
            native_id = raw.element_id
        elif isinstance(raw, dict):
            native_id = str(raw.get("native_id") or raw.get("id") or f"element:{ordinal}")
        else:
            raise SourceManifestError("native element manifest contains an invalid element")
        persisted_id_by_native_id[native_id] = _persisted_element_id(source_version_id, native_id)

    for ordinal, raw in enumerate(raw_elements):
        if isinstance(raw, NormalizedElement):
            native_id = raw.element_id
            kind = raw.kind or "unknown"
            text = (raw.text or "").strip()
            structural_path = str(raw.metadata.get("structural_path") or native_id)
            occurrence_ordinal = int(raw.metadata.get("occurrence_ordinal") or ordinal)
            element_id = persisted_id_by_native_id[raw.element_id]
            page_number = raw.page_number
            char_start = raw.char_start
            char_end = raw.char_end
            bounding_box = {"coords": list(raw.bounding_box)} if raw.bounding_box else None
            asset_key = raw.asset_key
            converter_method = None
            converter_version = None
            confidence = Decimal(str(raw.confidence)) if raw.confidence is not None else None
            element_metadata = {
                "structural_path": structural_path,
                "occurrence_ordinal": occurrence_ordinal,
                **raw.metadata,
            }
        elif isinstance(raw, dict):
            native_id = str(raw.get("native_id") or f"element:{ordinal}")
            kind = str(raw.get("kind") or "unknown")
            text = str(raw.get("text") or "").strip()
            structural_path = str(raw.get("structural_path") or native_id)
            occurrence_ordinal = int(raw.get("occurrence_ordinal") or 0)
            element_id = persisted_id_by_native_id[native_id]
            page_number = raw.get("page_number") if isinstance(raw.get("page_number"), int) else None
            char_start = raw.get("char_start") if isinstance(raw.get("char_start"), int) else None
            char_end = raw.get("char_end") if isinstance(raw.get("char_end"), int) else None
            bounding_box = raw.get("bounding_box") if isinstance(raw.get("bounding_box"), dict) else None
            asset_key = raw.get("asset_key")
            converter_method = str(raw.get("converter_method")) if raw.get("converter_method") else None
            converter_version = str(raw.get("converter_version")) if raw.get("converter_version") else None
            confidence = raw.get("confidence")
            element_metadata = {
                "structural_path": structural_path,
                "occurrence_ordinal": occurrence_ordinal,
                **_string_key_mapping(raw.get("metadata")),
            }
        else:
            raise SourceManifestError("native element manifest contains an invalid element")

        source_page_id = page_id_by_number.get(page_number) if page_number is not None else default_page_id
        native_parent_id = raw.parent_id if isinstance(raw, NormalizedElement) else raw.get("parent_element_id")
        parent_element_id = (
            persisted_id_by_native_id.get(str(native_parent_id)) if native_parent_id is not None else None
        )
        persisted_element_ids.add(element_id)

        existing_element = session.get(SourceElement, element_id)
        if existing_element is not None:
            existing_element.source_page_id = source_page_id
            existing_element.parent_element_id = parent_element_id
            existing_element.native_element_id = native_id
            existing_element.kind = kind
            existing_element.ordinal = ordinal
            existing_element.text = text or None
            existing_element.checksum = sha256_bytes(text.encode("utf-8")) if text else None
            existing_element.page_number = page_number
            existing_element.char_start = char_start
            existing_element.char_end = char_end
            existing_element.bounding_box = bounding_box
            existing_element.asset_key = asset_key
            existing_element.converter_method = converter_method
            existing_element.converter_version = converter_version
            existing_element.confidence = confidence
            existing_element.metadata_ = element_metadata
        else:
            session.add(
                SourceElement(
                    id=element_id,
                    source_version_id=source_version_id,
                    source_page_id=source_page_id,
                    parent_element_id=parent_element_id,
                    native_element_id=native_id,
                    kind=kind,
                    ordinal=ordinal,
                    text=text or None,
                    checksum=sha256_bytes(text.encode("utf-8")) if text else None,
                    page_number=page_number,
                    char_start=char_start,
                    char_end=char_end,
                    bounding_box=bounding_box,
                    asset_key=asset_key,
                    converter_method=converter_method,
                    converter_version=converter_version,
                    confidence=confidence,
                    metadata_=element_metadata,
                )
            )

    if persisted_element_ids:
        session.execute(
            delete(SourceElement).where(
                SourceElement.source_version_id == source_version_id, SourceElement.id.not_in(persisted_element_ids)
            )
        )
