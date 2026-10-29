from __future__ import annotations

from typing import Any

from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage


def source_document_to_dict(document: SourceDocument) -> dict[str, Any]:
    category_val = getattr(document, "category", None) or getattr(document, "category_id", None)
    return {
        "id": document.id,
        "title": document.title,
        "source_uri": document.source_uri,
        "logical_identity": document.logical_identity,
        "object_key": document.object_key,
        "media_type": document.media_type,
        "sha256": document.sha256,
        "size_bytes": document.size_bytes,
        "domain_id": None if document.domain_id == "ALL" else document.domain_id,
        "category": category_val,
        "sensitivity": document.sensitivity,
        "status": document.status,
        "metadata": document.metadata_,
        "dublin_core": document.dublin_core,
    }


def source_version_to_dict(version: SourceDocumentVersion | None) -> dict[str, Any] | None:
    if version is None:
        return None
    return {
        "id": version.id,
        "source_document_id": version.source_document_id,
        "version_sha256": version.version_sha256,
        "converter_name": version.converter_name,
        "converter_version": version.converter_version,
        "converted_object_key": version.converted_object_key,
        "status": version.status,
        "superseded_by_version_id": version.superseded_by_version_id,
        "metadata": version.metadata_,
        "dublin_core": version.dublin_core,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


def page_to_dict(page: SourcePage) -> dict[str, Any]:
    return {
        "id": page.id,
        "source_version_id": page.source_version_id,
        "page_number": page.page_number,
        "content_checksum": page.content_checksum,
        "classification": page.classification,
        "metadata": page.metadata_,
    }


def element_to_dict(element: SourceElement) -> dict[str, Any]:
    return {
        "id": element.id,
        "source_version_id": element.source_version_id,
        "source_page_id": element.source_page_id,
        "parent_element_id": element.parent_element_id,
        "native_element_id": element.native_element_id,
        "kind": element.kind,
        "ordinal": element.ordinal,
        "text": element.text,
        "checksum": element.checksum,
        "page_number": element.page_number,
        "char_start": element.char_start,
        "char_end": element.char_end,
        "bounding_box": element.bounding_box,
        "asset_key": element.asset_key,
        "converter_method": element.converter_method,
        "converter_version": element.converter_version,
        "confidence": float(element.confidence) if element.confidence is not None else None,
        "metadata": element.metadata_,
    }


def element_to_tree_node(
    element: SourceElement, children_by_parent: dict[str | None, list[SourceElement]]
) -> dict[str, Any]:
    node = element_to_dict(element)
    children = children_by_parent.get(element.id, [])
    node["children"] = [element_to_tree_node(child, children_by_parent) for child in children]
    return node
