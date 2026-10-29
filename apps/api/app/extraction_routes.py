from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.authz import require_entity_permission, require_source_permission
from app.source_routes import get_session_factory
from margin_db.extraction import extraction_quality_report
from margin_db.ingestion_receipt import IngestionReceiptError, build_ingestion_receipt
from margin_db.knowledge_models import ReprocessingDiff
from margin_db.relationship_evidence import (
    RelationshipApprovalError,
    approve_candidate_relationship,
    reject_candidate_relationship,
)
from margin_db.source_models import SourceDocument
from workers.extract.accounting import record_failed_extraction_run
from workers.extract.pipeline import MarginIdeaBlockPipeline

router = APIRouter(prefix="/api/sources", tags=["extraction"])


def _permissions(request: Request):
    return getattr(request.state, "permissions", None)


class RelationshipRejection(BaseModel):
    reason: str = "rejected during relationship review"


@router.post("/{source_document_id}/extract")
def run_deterministic_extraction(source_document_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            permissions = _permissions(request)
            if permissions is not None:
                require_source_permission(session, source_document_id, permissions=permissions, action="write")
            source_document = session.get(SourceDocument, source_document_id)
            metadata = source_document.metadata_ if source_document is not None else {}
            extraction_mode = (
                str(metadata.get("extraction_mode") or "deterministic")
                if isinstance(metadata, dict)
                else "deterministic"
            )
            if extraction_mode != "deterministic":
                raise HTTPException(
                    status_code=409, detail="LLM and hybrid sources must be extracted by the ingestion queue"
                )
            result = MarginIdeaBlockPipeline().run_once(session, source_document_id=source_document_id)
            session.commit()
    except HTTPException:
        raise
    except Exception as exc:
        with session_factory() as failure_session:
            record_failed_extraction_run(
                failure_session,
                source_document_id=source_document_id,
                mode="deterministic",
                extractor="deterministic_markdown",
                model_id=None,
                prompt_version=None,
                prompt_hash_value=None,
                max_window_tokens=1_500,
                neighbor_tokens=160,
                error=exc,
            )
            failure_session.commit()
        raise
    return {
        "source_document_id": source_document_id,
        "extraction_run_id": result.extraction_run_id,
        "report": result.report.to_dict(),
    }


@router.get("/{source_document_id}/ingestion-receipt")
def get_ingestion_receipt(source_document_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            permissions = _permissions(request)
            if permissions is not None:
                require_source_permission(session, source_document_id, permissions=permissions, action="read")
            return build_ingestion_receipt(session, source_document_id=source_document_id)
    except IngestionReceiptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{source_document_id}/reprocessing-diffs")
def get_reprocessing_diffs(source_document_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        permissions = _permissions(request)
        if permissions is not None:
            require_source_permission(session, source_document_id, permissions=permissions, action="read")
        rows = session.scalars(
            select(ReprocessingDiff)
            .where(ReprocessingDiff.source_document_id == source_document_id)
            .order_by(ReprocessingDiff.created_at.desc())
        ).all()
        return {
            "diffs": [
                {
                    "id": row.id,
                    "previous_version_id": row.previous_version_id,
                    "current_version_id": row.current_version_id,
                    "extraction_run_id": row.extraction_run_id,
                    "added_claim_ids": row.added_claim_ids,
                    "removed_claim_ids": row.removed_claim_ids,
                    "modified_claims": row.modified_claims,
                    "changed_entity_resolutions": row.changed_entity_resolutions,
                    "added_relationship_ids": row.added_relationship_ids,
                    "removed_relationship_ids": row.removed_relationship_ids,
                    "newly_unresolved": row.newly_unresolved,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        }


@router.get("/{source_document_id}/extraction-report")
def get_extraction_report(source_document_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        permissions = _permissions(request)
        if permissions is not None:
            require_source_permission(session, source_document_id, permissions=permissions, action="read")
        report = extraction_quality_report(session, source_document_id=source_document_id)
        return {"source_document_id": source_document_id, "report": report.to_dict()}


@router.post("/relationships/{relationship_id}/approve")
def approve_relationship(relationship_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            from margin_db.relationship_models import EntityRelationship

            relationship_row = session.get(EntityRelationship, relationship_id)
            if relationship_row is None:
                raise RelationshipApprovalError(f"relationship not found: {relationship_id}")
            permissions = _permissions(request)
            if permissions is not None:
                require_entity_permission(
                    session, relationship_row.source_entity_id, permissions=permissions, action="write"
                )
                require_entity_permission(
                    session, relationship_row.target_entity_id, permissions=permissions, action="write"
                )
            relationship = approve_candidate_relationship(
                session, relationship_id, actor=getattr(request.state, "principal_id", "api")
            )
            session.commit()
            return {
                "relationship_id": relationship.id,
                "status": relationship.status,
                "confidence": relationship.confidence,
            }
    except RelationshipApprovalError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/relationships/{relationship_id}/reject")
def reject_relationship(relationship_id: str, rejection: RelationshipRejection, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            from margin_db.relationship_models import EntityRelationship

            relationship_row = session.get(EntityRelationship, relationship_id)
            if relationship_row is None:
                raise RelationshipApprovalError(f"relationship not found: {relationship_id}")
            permissions = _permissions(request)
            if permissions is not None:
                require_entity_permission(
                    session, relationship_row.source_entity_id, permissions=permissions, action="write"
                )
                require_entity_permission(
                    session, relationship_row.target_entity_id, permissions=permissions, action="write"
                )
            relationship = reject_candidate_relationship(
                session, relationship_id, actor=getattr(request.state, "principal_id", "api"), reason=rejection.reason
            )
            session.commit()
            return {
                "relationship_id": relationship.id,
                "status": relationship.status,
                "confidence": relationship.confidence,
            }
    except RelationshipApprovalError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{source_document_id}/extractions")
def create_manual_extraction(
    source_document_id: str, payload: dict[str, object], request: Request
) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        permissions = _permissions(request)
        if permissions is not None:
            require_source_permission(session, source_document_id, permissions=permissions, action="write")
        from margin_db.extraction import ExtractionPersistenceService
        from margin_schema.extraction import ExtractedIdeaBlock, ExtractorOutput

        def _normalize_block(block: dict[str, Any]) -> dict[str, Any]:
            item = dict(block)
            item.pop("category", None)
            item["extraction_method"] = "manual"
            metadata = item.get("metadata")
            item["metadata"] = {
                **(metadata if isinstance(metadata, dict) else {}),
                "verification_policy": "manual_authorized",
            }
            if "evidence" not in item:
                element_id = item.pop("source_element_id", None)
                quote = item.pop("source_quote", None)
                if element_id and quote:
                    item["evidence"] = [{"source_element_id": element_id, "source_quote": quote}]
            return item

        idea_blocks = payload.get("idea_blocks", [])
        if isinstance(idea_blocks, list):
            blocks = tuple(
                ExtractedIdeaBlock.model_validate(_normalize_block(b)) for b in idea_blocks if isinstance(b, dict)
            )
        else:
            blocks = ()
        output = ExtractorOutput(idea_blocks=blocks)
        service = ExtractionPersistenceService()
        report = service.persist_output(session, output, source_document_id=source_document_id)
        session.flush()
        session.commit()
        return {
            "source_document_id": source_document_id,
            "persisted_claims": report.idea_block_count,
            "persisted_entities": report.entity_count,
            "persisted_relationships": report.relationship_count,
        }
