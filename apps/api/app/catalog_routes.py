from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, or_, select

from app.authz import require_permission
from app.catalog_helpers import (
    can_read_idea_block as _can_read_idea_block,
    can_read_source as _can_read_source,
    element_dict as _element_dict,
    idea_block_dict as _idea_block_dict,
    page_dict as _page_dict,
    readable_idea_block_ids as _readable_idea_block_ids,
    require_any_review as _require_any_review,
    require_idea_block as _require_idea_block,
    require_idea_block_write as _require_idea_block_write,
    require_source as _require_source,
    require_source_read as _require_source_read,
    source_document_dict as _source_document_dict,
)
from app.source_routes import get_session_factory
from margin_db.audit import audit_report_query
from margin_db.embeddings import enqueue_embedding_job
from margin_db.entity_models import EntityMention, EntityResolutionDecision
from margin_db.extraction import ExtractionPersistenceService
from margin_db.idea_block_resolution import create_idea_block_revision, current_idea_block_revision
from margin_db.knowledge_models import (
    ClaimEvidence,
    EmbeddingJob,
    EmbeddingRecord,
    IdeaBlock,
    IdeaBlockEntity,
    IdeaBlockRevision,
)
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.review_models import ReviewTask, ReviewTaskComment
from margin_db.review_transitions import ReviewTransitionError, transition_idea_block_status
from margin_db.source_ingestion import MetadataCorrectionError, resolve_category_id
from margin_db.source_models import (
    ConversionQualityReport,
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourceElementOutcome,
    SourcePage,
    UnmappedExtraction,
)
from margin_db.user_models import Domain
from margin_embedding.factory import required_embedding_index_config
from margin_schema.extraction import (
    ExtractedClaimEvidence,
    ExtractedEntity,
    ExtractedIdeaBlock,
    ExtractedRelationship,
    ExtractorOutput,
)
from margin_schema.text_normalization import normalize_statement

router = APIRouter(prefix="/api", tags=["catalog"])


class IdeaBlockStatusUpdate(BaseModel):
    status: str = Field(pattern="^(candidate|verified|rejected|superseded)$")


class IdeaBlockUpdate(BaseModel):
    statement: str | None = Field(default=None, min_length=1)
    status: str | None = Field(default=None, pattern="^(candidate|verified|rejected|superseded)$")


class ManualEntityPayload(BaseModel):
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    role: str = "context"
    external_id: str | None = None


class ManualRelationshipPayload(BaseModel):
    source: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    target: str = Field(min_length=1)
    confidence: str = "extracted"
    polarity: str = Field(default="supports", pattern="^(supports|refutes)$")


class ManualIdeaBlockCreate(BaseModel):
    statement: str = Field(min_length=1)
    domain_id: str = Field(min_length=1)
    category: str = "Design Document"
    type: str = "statement"
    confidence: str = "extracted"
    source_quote: str | None = None
    entities: list[ManualEntityPayload] = Field(default_factory=list)
    relationships: list[ManualRelationshipPayload] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


def _manual_idea_block_source_ids() -> tuple[str, str, str]:
    suffix = uuid4().hex
    return (f"manual:ideablock:{suffix}", f"srcv_{suffix}", f"elem_{suffix}")


def _is_manual_source(source: SourceDocument) -> bool:
    metadata = source.metadata_ or {}
    return bool(metadata.get("manual_ideablock") or metadata.get("manual_entity") or metadata.get("manual_record"))


def _delete_embedding_targets(session, *, target_type: str, target_ids: set[str]) -> None:
    if not target_ids:
        return
    session.execute(
        delete(EmbeddingRecord).where(
            EmbeddingRecord.target_type == target_type, EmbeddingRecord.target_id.in_(target_ids)
        )
    )
    session.execute(
        delete(EmbeddingJob).where(EmbeddingJob.target_type == target_type, EmbeddingJob.target_id.in_(target_ids))
    )


def _delete_relationships(session, relationship_ids: set[str]) -> None:
    if not relationship_ids:
        return
    session.execute(delete(RelationshipProvenance).where(RelationshipProvenance.relationship_id.in_(relationship_ids)))
    session.execute(delete(EntityRelationship).where(EntityRelationship.id.in_(relationship_ids)))


def _delete_source_records(session, source_ids: set[str]) -> None:
    if not source_ids:
        return
    version_ids = set(
        session.scalars(
            select(SourceDocumentVersion.id).where(SourceDocumentVersion.source_document_id.in_(source_ids))
        ).all()
    )
    element_ids = set(
        session.scalars(select(SourceElement.id).where(SourceElement.source_version_id.in_(version_ids))).all()
    )
    page_ids = set(session.scalars(select(SourcePage.id).where(SourcePage.source_version_id.in_(version_ids))).all())
    if element_ids:
        _delete_embedding_targets(session, target_type="source_element", target_ids=element_ids)
        mention_ids = set(
            session.scalars(select(EntityMention.id).where(EntityMention.source_element_id.in_(element_ids))).all()
        )
        if mention_ids:
            session.execute(
                delete(EntityResolutionDecision).where(EntityResolutionDecision.mention_id.in_(mention_ids))
            )
            session.execute(delete(EntityMention).where(EntityMention.id.in_(mention_ids)))
        session.execute(delete(SourceElementOutcome).where(SourceElementOutcome.source_element_id.in_(element_ids)))
        session.execute(delete(UnmappedExtraction).where(UnmappedExtraction.source_element_id.in_(element_ids)))
        session.execute(delete(SourceElement).where(SourceElement.id.in_(element_ids)))
    if page_ids:
        session.execute(delete(SourcePage).where(SourcePage.id.in_(page_ids)))
    session.execute(delete(SourceDocumentVersion).where(SourceDocumentVersion.id.in_(version_ids)))
    session.execute(delete(ConversionQualityReport).where(ConversionQualityReport.source_document_id.in_(source_ids)))
    session.execute(delete(SourceDocument).where(SourceDocument.id.in_(source_ids)))


def _delete_review_tasks(session, review_task_ids: set[object]) -> None:
    if not review_task_ids:
        return
    session.execute(delete(ReviewTaskComment).where(ReviewTaskComment.review_task_id.in_(review_task_ids)))
    session.execute(delete(ReviewTask).where(ReviewTask.id.in_(review_task_ids)))


@router.get("/sources")
def list_sources(request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        rows = session.execute(select(SourceDocument).order_by(SourceDocument.created_at)).scalars()
        return {
            "sources": [
                _source_document_dict(row)
                for row in rows
                if not _is_manual_source(row) and _can_read_source(request, row, session)
            ]
        }


@router.get("/sources/{source_document_id}/pages")
def list_source_pages(source_document_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        source = _require_source(session, source_document_id)
        _require_source_read(request, source, session)
        rows = session.execute(
            select(SourcePage)
            .join(SourceDocumentVersion, SourceDocumentVersion.id == SourcePage.source_version_id)
            .where(
                SourceDocumentVersion.source_document_id == source_document_id, SourceDocumentVersion.status == "active"
            )
            .order_by(SourcePage.page_number)
        ).scalars()
        return {"source_document_id": source_document_id, "pages": [_page_dict(row) for row in rows]}


@router.get("/sources/{source_document_id}/elements")
def list_source_elements(source_document_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        source = _require_source(session, source_document_id)
        _require_source_read(request, source, session)
        rows = session.execute(
            select(SourceElement)
            .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
            .where(
                SourceDocumentVersion.source_document_id == source_document_id, SourceDocumentVersion.status == "active"
            )
            .order_by(SourceElement.source_version_id, SourceElement.ordinal, SourceElement.id)
        ).scalars()
        return {"source_document_id": source_document_id, "elements": [_element_dict(row) for row in rows]}


@router.get("/ideablocks")
def list_idea_blocks(request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        rows = list(session.execute(select(IdeaBlock).order_by(IdeaBlock.created_at)).scalars())
        readable_ids = _readable_idea_block_ids(session, request, rows)
        return {"idea_blocks": [_idea_block_dict(session, request, row) for row in rows if row.id in readable_ids]}


@router.get("/sources/{source_document_id}/ideablocks")
def list_source_idea_blocks(source_document_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        source = _require_source(session, source_document_id)
        _require_source_read(request, source, session)
        rows = session.execute(
            select(IdeaBlock)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
            .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .where(
                SourceDocumentVersion.source_document_id == source_document_id,
                SourceDocumentVersion.status == "active",
                ClaimEvidence.idea_block_revision_id == IdeaBlock.current_revision_id,
                ClaimEvidence.status == "active",
                SourceDocument.domain_id == IdeaBlock.domain_id,
                IdeaBlock.merged_into_id.is_(None),
                IdeaBlock.tombstoned_at.is_(None),
                IdeaBlock.archived_at.is_(None),
            )
            .distinct()
            .order_by(IdeaBlock.created_at, IdeaBlock.id)
        ).scalars()
        return {
            "source_document_id": source_document_id,
            "idea_blocks": [_idea_block_dict(session, request, row) for row in rows],
        }


@router.get("/ideablocks/{idea_block_id}")
def get_idea_block(idea_block_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        block = _require_idea_block(session, idea_block_id)
        if not _can_read_idea_block(session, request, block):
            raise HTTPException(status_code=403, detail="missing read access for IdeaBlock")
        return {"idea_block": _idea_block_dict(session, request, block)}


@router.post("/ideablocks", status_code=201)
def create_manual_idea_block(body: ManualIdeaBlockCreate, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        if session.get(Domain, body.domain_id) is None:
            raise HTTPException(status_code=422, detail=f"unknown domain: {body.domain_id}")
        require_permission(
            permissions=request.state.permissions,
            domain_id=body.domain_id,
            category=body.category,
            sensitivity="internal",
            action="write",
        )
        try:
            category_id = resolve_category_id(session, body.category)
        except MetadataCorrectionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        source_id, version_id, element_id = _manual_idea_block_source_ids()
        source_suffix = source_id.removeprefix("manual:ideablock:")
        statement = body.statement.strip()
        source_quote = (body.source_quote or statement).strip()
        idea_block_type = body.type.strip()
        confidence = body.confidence.strip()
        if not statement or not source_quote or not idea_block_type or not confidence:
            raise HTTPException(status_code=422, detail="statement, quote, type, and confidence are required")
        source = SourceDocument(
            id=source_id,
            title=f"Manual IdeaBlock {source_suffix}",
            source_uri=f"margin://manual/ideablocks/{source_suffix}",
            object_key=f"manual/ideablocks/{source_suffix}",
            media_type="application/vnd.margin.ideablock+json",
            sha256=sha256(source_id.encode("utf-8")).hexdigest(),
            size_bytes=len(source_quote.encode("utf-8")),
            domain_id=body.domain_id,
            category_id=category_id,
            sensitivity="internal",
            status="extraction_ready",
            metadata_={"manual_ideablock": True, "created_by": getattr(request.state, "principal_id", "api")},
            dublin_core={},
            ingested_by=None,
        )
        version = SourceDocumentVersion(
            id=version_id,
            source_document_id=source.id,
            version_sha256=sha256(source_quote.encode("utf-8")).hexdigest(),
            converter_name="manual",
            converter_version="1",
            metadata_={"manual_ideablock": True},
            dublin_core={},
        )
        element = SourceElement(
            id=element_id,
            source_version_id=version.id,
            kind="manual",
            ordinal=1,
            text=source_quote,
            char_start=0,
            char_end=len(source_quote),
            checksum=sha256(source_quote.encode("utf-8")).hexdigest(),
            converter_method="manual",
            converter_version="1",
            confidence="verified",
        )
        session.add_all([source, version, element])
        session.flush()

        try:
            candidate = ExtractedIdeaBlock(
                statement=statement,
                type=idea_block_type,
                confidence=confidence,
                extraction_method="manual",
                evidence=[ExtractedClaimEvidence(source_element_id=element.id, source_quote=source_quote)],
                entities=[ExtractedEntity(**entity.model_dump()) for entity in body.entities],
                relationships=[
                    ExtractedRelationship(**relationship.model_dump()) for relationship in body.relationships
                ],
                tags=body.tags,
                metadata={"manual_ideablock": True},
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report = ExtractionPersistenceService().persist_output(
            session, ExtractorOutput(idea_blocks=[candidate]), source_document_id=source.id, force_candidate=True
        )
        block = session.scalar(
            select(IdeaBlock)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .where(ClaimEvidence.source_element_id == element.id)
        )
        if block is None:
            raise HTTPException(status_code=500, detail="manual IdeaBlock was not persisted")
        session.commit()
        return {
            "idea_block": _idea_block_dict(session, request, block),
            "source_document_id": source.id,
            "report": report.to_dict(),
        }


@router.patch("/ideablocks/{idea_block_id}/status")
def update_idea_block_status(idea_block_id: str, body: IdeaBlockStatusUpdate, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            block = _require_idea_block(session, idea_block_id)
            _require_idea_block_write(session, request, block)
            block, _event = transition_idea_block_status(
                session,
                idea_block_id=idea_block_id,
                target_status=body.status,
                actor=getattr(request.state, "principal_id", "api"),
                note="IB status patched via /ideablocks/{id}/status",
            )
            session.commit()
            return {"idea_block": _idea_block_dict(session, request, block)}
    except ReviewTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/ideablocks/{idea_block_id}")
def patch_idea_block(idea_block_id: str, body: IdeaBlockUpdate, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    try:
        with session_factory() as session:
            block = _require_idea_block(session, idea_block_id)
            _require_idea_block_write(session, request, block)
            if body.statement is not None:
                current_revision = current_idea_block_revision(session, block)
                if body.statement != current_revision.statement:
                    current_revision = create_idea_block_revision(
                        session,
                        block,
                        statement=body.statement,
                        normalized_statement=normalize_statement(body.statement),
                        type=current_revision.type,
                        confidence=current_revision.confidence,
                        polarity=current_revision.polarity,
                        qualifiers=current_revision.qualifiers,
                        valid_from=current_revision.valid_from,
                        valid_to=current_revision.valid_to,
                        publication_status=current_revision.publication_status,
                        actor=getattr(request.state, "principal_id", "api"),
                        process="catalog_api",
                        change_reason="statement edited through catalog API",
                        metadata=current_revision.metadata_,
                    )
                embedding_config = required_embedding_index_config()
                enqueue_embedding_job(
                    session,
                    target_type="idea_block_revision",
                    target_id=current_revision.id,
                    model_id=embedding_config.model_id,
                    index_version=embedding_config.index_version,
                    reason="idea_block_statement_changed",
                )
            if body.status is not None:
                block, _event = transition_idea_block_status(
                    session,
                    idea_block_id=idea_block_id,
                    target_status=body.status,
                    actor=getattr(request.state, "principal_id", "api"),
                    note="IB status patched via /ideablocks/{id}",
                )
            session.flush()
            session.commit()
            return {"idea_block": _idea_block_dict(session, request, block)}
    except ReviewTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/ideablocks/{idea_block_id}")
def delete_idea_block(idea_block_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        block = _require_idea_block(session, idea_block_id)
        _require_idea_block_write(session, request, block)
        source_ids = set(
            session.scalars(
                select(SourceDocument.id)
                .join(SourceDocumentVersion, SourceDocumentVersion.source_document_id == SourceDocument.id)
                .join(SourceElement, SourceElement.source_version_id == SourceDocumentVersion.id)
                .join(ClaimEvidence, ClaimEvidence.source_element_id == SourceElement.id)
                .where(ClaimEvidence.idea_block_id == idea_block_id)
            ).all()
        )
        relationship_ids = set(
            session.scalars(
                select(RelationshipProvenance.relationship_id).where(
                    RelationshipProvenance.idea_block_id == idea_block_id
                )
            ).all()
        )
        mention_ids = set(
            session.scalars(
                select(EntityMention.id)
                .join(IdeaBlockRevision, IdeaBlockRevision.id == EntityMention.idea_block_revision_id)
                .where(IdeaBlockRevision.idea_block_id == idea_block_id)
            ).all()
        )
        _delete_relationships(session, relationship_ids)
        if mention_ids:
            session.execute(
                delete(EntityResolutionDecision).where(EntityResolutionDecision.mention_id.in_(mention_ids))
            )
            session.execute(delete(EntityMention).where(EntityMention.id.in_(mention_ids)))
        review_task_ids = set(
            session.scalars(
                select(ReviewTask.id).where(
                    or_(
                        (ReviewTask.target_type == "idea_block") & (ReviewTask.target_id == idea_block_id),
                        (ReviewTask.target_type == "entity_relationship")
                        & (ReviewTask.target_id.in_(relationship_ids)),
                    )
                )
            ).all()
        )
        _delete_review_tasks(session, review_task_ids)
        revision_ids = set(
            session.scalars(select(IdeaBlockRevision.id).where(IdeaBlockRevision.idea_block_id == idea_block_id)).all()
        )
        _delete_embedding_targets(session, target_type="idea_block_revision", target_ids=revision_ids)
        session.execute(delete(ClaimEvidence).where(ClaimEvidence.idea_block_id == idea_block_id))
        session.execute(delete(IdeaBlockEntity).where(IdeaBlockEntity.idea_block_id == idea_block_id))
        session.delete(block)
        _delete_source_records(
            session, {source_id for source_id in source_ids if source_id.startswith("manual:ideablock:")}
        )
        session.commit()
    return {"status": "deleted", "idea_block_id": idea_block_id}


@router.get("/audit-events")
def audit_events(
    request: Request,
    actor: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, object]:
    _require_any_review(request)
    session_factory = get_session_factory(request)
    with session_factory() as session:
        rows = session.execute(
            audit_report_query(
                actor=actor, target_type=target_type, target_id=target_id, date_from=date_from, date_to=date_to
            )
        ).scalars()
        materialized = list(rows)[:limit]
        return {
            "audit_events": [
                {
                    "id": str(row.id),
                    "actor": row.actor,
                    "action": row.action,
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "before": row.before,
                    "after": row.after,
                    "git_commit_sha": row.git_commit_sha,
                    "created_at": row.created_at.isoformat(),
                }
                for row in materialized
            ]
        }
