"""Entity catalog and entity-resolution governance endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.authz import require_any_domain_permission, require_permission
from app.source_routes import get_session_factory
from margin_db.entity_catalog import (
    EntityMergeConflict,
    add_alias,
    approve_mention_resolution,
    create_entity,
    deprecate_alias,
    schedule_affected_reresolution,
    update_entity,
)
from margin_db.entity_catalog_lifecycle import merge_entities, retire_entity, supersede_entity
from margin_db.entity_models import Entity, EntityAlias, EntityMention, EntityReresolutionJob, EntityResolutionDecision
from margin_db.governance_models import GovernanceRecord, GovernanceRecordEntity
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_schema.ontology import ALL_ENTITY_KINDS, ONTOLOGY_VERSION
from margin_schema.text_normalization import normalize_entity_name
from workers.tasks import reresolve_affected_entity_catalog

router = APIRouter(prefix="/api/entities", tags=["entities"])
source_entity_router = APIRouter(prefix="/api/sources", tags=["entities"])
logger = logging.getLogger(__name__)


class EntityCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_label: str = Field(min_length=1)
    ontology_type: str = Field(min_length=1)
    domain_id: str | None = None
    locale: str = "en"
    definition: str | None = None
    definition_idea_block_id: str | None = None
    external_id: str | None = None
    lifecycle_status: str = "draft"
    owner_user_id: str | None = None
    steward_user_id: str | None = None


class EntityUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_label: str | None = Field(default=None, min_length=1)
    ontology_type: str | None = Field(default=None, min_length=1)
    locale: str | None = Field(default=None, min_length=1)
    definition: str | None = None
    definition_idea_block_id: str | None = None
    external_id: str | None = None
    lifecycle_status: str | None = None
    owner_user_id: str | None = None
    steward_user_id: str | None = None
    reason: str = Field(min_length=1)


class AliasPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1)
    source_provenance: dict[str, object] = Field(default_factory=dict)
    reason: str = Field(min_length=1)


class EntityActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_entity_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RetirePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)


class MentionResolutionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


@router.get("")
def list_entities(
    request: Request,
    domain_id: str | None = None,
    ontology_type: str | None = None,
    lifecycle_status: str | None = None,
    alias: str | None = None,
    unresolved: bool = False,
    limit: int = Query(default=100, ge=1, le=250),
) -> dict[str, object]:
    with get_session_factory(request)() as session:
        statement = select(Entity)
        if domain_id is not None:
            require_permission(
                permissions=request.state.permissions,
                domain_id=domain_id,
                category=None,
                sensitivity=None,
                action="read",
            )
            statement = statement.where(Entity.domain_id == domain_id)
        if ontology_type:
            statement = statement.where(Entity.ontology_type == ontology_type)
        if lifecycle_status:
            statement = statement.where(Entity.lifecycle_status == lifecycle_status)
        if alias:
            normalized = normalize_entity_name(alias)
            statement = statement.join(EntityAlias, EntityAlias.entity_id == Entity.id).where(
                EntityAlias.normalized_alias == normalized, EntityAlias.lifecycle_status == "active"
            )
        records = list(
            session.scalars(statement.order_by(Entity.preferred_label, Entity.id).limit(limit)).unique().all()
        )
        visible = [record for record in records if _can_read_entity(request, record)]
        payload: dict[str, object] = {
            "items": [_entity_payload(session, record) for record in visible],
            "count": len(visible),
        }
        if unresolved:
            mentions = _unresolved_mentions(session, request, domain_id=domain_id, limit=limit)
            payload["unresolved_mentions"] = mentions
            payload["unresolved_count"] = len(mentions)
        return payload


@router.post("", status_code=201)
def create_catalog_entity(body: EntityCreatePayload, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        _require_domain_action(request, body.domain_id, "write")
        if body.lifecycle_status != "draft":
            raise HTTPException(
                status_code=409, detail="new catalog entities must begin as draft and be approved explicitly"
            )
        try:
            record = create_entity(
                session,
                preferred_label=body.preferred_label,
                ontology_type=body.ontology_type,
                domain_id=body.domain_id,
                locale=body.locale,
                definition=body.definition,
                definition_idea_block_id=body.definition_idea_block_id,
                external_id=body.external_id,
                lifecycle_status=body.lifecycle_status,
                owner_user_id=body.owner_user_id,
                steward_user_id=body.steward_user_id,
                actor_id=_actor(request),
                reason="entity created through catalog API",
            )
            session.flush()
            schedule_affected_reresolution(
                session,
                domain_id=record.domain_id,
                entity_ids=(record.id,),
                normalized_aliases=(record.normalized_label,),
            )
            _commit_and_enqueue(request, session)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _entity_payload(session, record)


@router.get("/catalog/ontology")
def get_catalog_ontology() -> dict[str, object]:
    """Expose the one runtime ontology for the Entity Catalog form controls."""

    return {
        "ontology_version": ONTOLOGY_VERSION,
        "entity_types": list(ALL_ENTITY_KINDS),
        "lifecycle_statuses": ["draft", "active", "superseded", "retired"],
    }


@router.get("/mentions")
def list_unresolved_mentions(
    request: Request, domain_id: str | None = None, limit: int = Query(default=100, ge=1, le=250)
) -> dict[str, object]:
    with get_session_factory(request)() as session:
        statement = (
            select(EntityMention, EntityResolutionDecision)
            .join(EntityResolutionDecision, EntityResolutionDecision.mention_id == EntityMention.id)
            .where(EntityResolutionDecision.is_current.is_(True), EntityResolutionDecision.status == "review_required")
            .order_by(EntityMention.created_at, EntityMention.id)
        )
        if domain_id is not None:
            _require_domain_action(request, domain_id, "review")
            statement = statement.where(EntityMention.domain_id == domain_id)
        rows = session.execute(statement.limit(limit)).all()
        return {
            "items": [
                _mention_payload(mention, decision)
                for mention, decision in rows
                if _can_access_domain(request, mention.domain_id, "review")
            ]
        }


@router.post("/mentions/{mention_id}/resolve")
def resolve_catalog_mention(mention_id: str, body: MentionResolutionPayload, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        mention = session.get(EntityMention, mention_id)
        if mention is None:
            raise HTTPException(status_code=404, detail="entity mention not found")
        _require_domain_action(request, mention.domain_id, "review")
        entity = session.get(Entity, body.entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="resolution entity not found")
        try:
            decision = approve_mention_resolution(
                session, mention=mention, entity=entity, actor_id=_actor(request), reason=body.reason
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _decision_payload(decision)


@router.get("/{entity_id}")
def get_entity(entity_id: str, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        entity = _require_entity(session, request, entity_id, action="read")
        return _entity_payload(session, entity, include_detail=True)


@router.patch("/{entity_id}")
def update_catalog_entity(entity_id: str, body: EntityUpdatePayload, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        entity = _require_entity(session, request, entity_id, action="write")
        fields = body.model_fields_set
        if "lifecycle_status" in fields and body.lifecycle_status != entity.lifecycle_status:
            raise HTTPException(status_code=409, detail="use an Entity lifecycle operation to change lifecycle_status")
        values: dict[str, object] = {"entity": entity, "actor_id": _actor(request), "reason": body.reason}
        for name in (
            "preferred_label",
            "ontology_type",
            "locale",
            "definition",
            "definition_idea_block_id",
            "external_id",
            "lifecycle_status",
            "owner_user_id",
            "steward_user_id",
        ):
            if name in fields:
                values[name] = getattr(body, name)
        try:
            update_entity(session, **values)
            _commit_and_enqueue(request, session)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _entity_payload(session, entity, include_detail=True)


@router.post("/{entity_id}/approve")
def approve_catalog_entity(entity_id: str, body: RetirePayload, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        entity = _require_entity(session, request, entity_id, action="review")
        if entity.lifecycle_status != "draft":
            raise HTTPException(status_code=409, detail="only draft Entities can be approved")
        try:
            update_entity(
                session, entity=entity, lifecycle_status="active", actor_id=_actor(request), reason=body.reason
            )
            _commit_and_enqueue(request, session)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _entity_payload(session, entity)


@router.post("/{entity_id}/aliases", status_code=201)
def create_catalog_alias(entity_id: str, body: AliasPayload, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        entity = _require_entity(session, request, entity_id, action="write")
        try:
            alias = add_alias(
                session,
                entity=entity,
                alias=body.alias,
                source_provenance=body.source_provenance,
                actor_id=_actor(request),
                reason=body.reason,
            )
            _commit_and_enqueue(request, session)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _alias_payload(alias)


@router.post("/{entity_id}/aliases/{alias_id}/deprecate")
def deprecate_catalog_alias(entity_id: str, alias_id: str, body: RetirePayload, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        _require_entity(session, request, entity_id, action="write")
        alias = session.get(EntityAlias, alias_id)
        if alias is None or alias.entity_id != entity_id:
            raise HTTPException(status_code=404, detail="entity alias not found")
        try:
            deprecate_alias(session, alias=alias, actor_id=_actor(request), reason=body.reason)
            _commit_and_enqueue(request, session)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _alias_payload(alias)


@router.post("/{entity_id}/merge")
def merge_catalog_entity(entity_id: str, body: EntityActionPayload, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        source = _require_entity(session, request, entity_id, action="review")
        survivor = _require_entity(session, request, body.target_entity_id, action="review")
        try:
            merge_entities(session, source=source, survivor=survivor, actor_id=_actor(request), reason=body.reason)
            _commit_and_enqueue(request, session)
        except EntityMergeConflict as exc:
            session.rollback()
            raise HTTPException(
                status_code=409, detail={"message": str(exc), "blocking_fields": list(exc.blocking_fields)}
            ) from exc
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"source_entity_id": source.id, "survivor_entity_id": survivor.id, "status": source.lifecycle_status}


@router.post("/{entity_id}/supersede")
def supersede_catalog_entity(entity_id: str, body: EntityActionPayload, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        source = _require_entity(session, request, entity_id, action="review")
        successor = _require_entity(session, request, body.target_entity_id, action="review")
        try:
            supersede_entity(session, source=source, successor=successor, actor_id=_actor(request), reason=body.reason)
            _commit_and_enqueue(request, session)
        except EntityMergeConflict as exc:
            session.rollback()
            raise HTTPException(
                status_code=409, detail={"message": str(exc), "blocking_fields": list(exc.blocking_fields)}
            ) from exc
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"source_entity_id": source.id, "successor_entity_id": successor.id, "status": source.lifecycle_status}


@router.post("/{entity_id}/retire")
def retire_catalog_entity(entity_id: str, body: RetirePayload, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        entity = _require_entity(session, request, entity_id, action="review")
        try:
            retire_entity(session, entity=entity, actor_id=_actor(request), reason=body.reason)
            _commit_and_enqueue(request, session)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _entity_payload(session, entity)


@source_entity_router.get("/{source_document_id}/entities")
def list_source_entities(source_document_id: str, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        source = session.get(SourceDocument, source_document_id)
        if source is None:
            raise HTTPException(status_code=404, detail="source document not found")
        category = session.get(Entity, source.category_id)
        if category is None or category.ontology_type != "category" or category.lifecycle_status != "active":
            raise HTTPException(status_code=409, detail="source document has an invalid category reference")
        require_permission(
            permissions=request.state.permissions,
            domain_id=source.domain_id,
            category=category.preferred_label,
            sensitivity=source.sensitivity,
            action="read",
        )
        statement = (
            select(Entity)
            .join(IdeaBlockEntity, IdeaBlockEntity.entity_id == Entity.id)
            .join(IdeaBlock, IdeaBlock.id == IdeaBlockEntity.idea_block_id)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
            .where(
                SourceDocumentVersion.source_document_id == source_document_id,
                SourceDocumentVersion.status == "active",
                ClaimEvidence.status == "active",
                ClaimEvidence.idea_block_revision_id == IdeaBlock.current_revision_id,
                IdeaBlock.merged_into_id.is_(None),
                IdeaBlock.tombstoned_at.is_(None),
                IdeaBlock.archived_at.is_(None),
                Entity.lifecycle_status == "active",
                or_(Entity.domain_id.is_(None), Entity.domain_id == IdeaBlock.domain_id),
                SourceDocument.domain_id == IdeaBlock.domain_id,
            )
            .distinct()
            .order_by(Entity.preferred_label, Entity.id)
        )
        entities = session.scalars(statement).all()
        return {
            "source_document_id": source_document_id,
            "entities": [_entity_payload(session, entity) for entity in entities if _can_read_entity(request, entity)],
        }


def _require_entity(session: Session, request: Request, entity_id: str, *, action: str) -> Entity:
    entity = session.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")
    _require_domain_action(request, entity.domain_id, action)
    return entity


def _require_domain_action(request: Request, domain_id: str | None, action: str) -> None:
    if domain_id is None:
        require_any_domain_permission(
            permissions=request.state.permissions,
            action=action,
            detail=f"missing {action} access for the global Entity catalog",
        )
        return
    require_permission(
        permissions=request.state.permissions, domain_id=domain_id, category=None, sensitivity=None, action=action
    )


def _can_read_entity(request: Request, entity: Entity) -> bool:
    return _can_access_domain(request, entity.domain_id, "read")


def _can_access_domain(request: Request, domain_id: str | None, action: str) -> bool:
    try:
        _require_domain_action(request, domain_id, action)
    except HTTPException:
        return False
    return True


def _entity_payload(session: Session, entity: Entity, *, include_detail: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": entity.id,
        "preferred_label": entity.preferred_label,
        "normalized_label": entity.normalized_label,
        "definition": entity.definition,
        "definition_idea_block_id": entity.definition_idea_block_id,
        "ontology_type": entity.ontology_type,
        "ontology_version": entity.ontology_version,
        "catalog_version": entity.catalog_version,
        "domain_id": entity.domain_id,
        "locale": entity.locale,
        "external_id": entity.external_id,
        "lifecycle_status": entity.lifecycle_status,
        "superseded_by_entity_id": entity.superseded_by_entity_id,
        "retired_at": entity.retired_at.isoformat() if entity.retired_at else None,
        "owner_user_id": entity.owner_user_id,
        "steward_user_id": entity.steward_user_id,
        "metadata": entity.metadata_ or {},
        "created_at": entity.created_at.isoformat() if entity.created_at else None,
        "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
    }
    if not include_detail:
        return payload
    aliases = session.scalars(
        select(EntityAlias).where(EntityAlias.entity_id == entity.id).order_by(EntityAlias.alias)
    ).all()
    links = session.scalars(select(IdeaBlockEntity).where(IdeaBlockEntity.entity_id == entity.id)).all()
    decisions = session.scalars(
        select(EntityResolutionDecision)
        .where(EntityResolutionDecision.entity_id == entity.id)
        .order_by(EntityResolutionDecision.sequence.desc(), EntityResolutionDecision.id.desc())
    ).all()
    payload.update(
        {
            "aliases": [_alias_payload(alias) for alias in aliases],
            "idea_blocks": [
                {
                    "idea_block_id": link.idea_block_id,
                    "role": link.role,
                    "current_revision_id": _current_revision_id(session, link.idea_block_id),
                }
                for link in links
            ],
            "resolution_history": [_decision_payload(decision) for decision in decisions],
            "governance_records": _governance_payload(session, entity.id),
            "source_mentions": [
                {
                    "mention_id": mention.id,
                    "text": mention.text,
                    "source_element_id": mention.source_element_id,
                    "idea_block_revision_id": mention.idea_block_revision_id,
                }
                for mention in _mentions_for_entity(session, entity.id)
            ],
        }
    )
    return payload


def _alias_payload(alias: EntityAlias) -> dict[str, object]:
    return {
        "id": alias.id,
        "alias": alias.alias,
        "normalized_alias": alias.normalized_alias,
        "locale": alias.locale,
        "domain_id": alias.domain_id,
        "ontology_type": alias.ontology_type,
        "lifecycle_status": alias.lifecycle_status,
        "source_provenance": alias.source_provenance or {},
    }


def _decision_payload(decision: EntityResolutionDecision) -> dict[str, object]:
    return {
        "id": decision.id,
        "mention_id": decision.mention_id,
        "entity_id": decision.entity_id,
        "outcome": decision.outcome,
        "method": decision.method,
        "confidence": decision.confidence,
        "candidate_entity_ids": decision.candidate_entity_ids,
        "rationale": decision.rationale,
        "status": decision.status,
        "ontology_version": decision.ontology_version,
        "decided_by": decision.decided_by,
        "is_current": decision.is_current,
        "created_at": decision.created_at.isoformat() if decision.created_at else None,
        "reviewed_at": decision.reviewed_at.isoformat() if decision.reviewed_at else None,
    }


def _current_revision_id(session: Session, idea_block_id: str) -> str | None:
    block = session.get(IdeaBlock, idea_block_id)
    return block.current_revision_id if block is not None else None


def _mentions_for_entity(session: Session, entity_id: str) -> list[EntityMention]:
    return list(
        session.scalars(
            select(EntityMention)
            .join(EntityResolutionDecision, EntityResolutionDecision.mention_id == EntityMention.id)
            .where(EntityResolutionDecision.entity_id == entity_id, EntityResolutionDecision.is_current.is_(True))
            .order_by(EntityMention.created_at, EntityMention.id)
        ).all()
    )


def _unresolved_mentions(
    session: Session, request: Request, *, domain_id: str | None, limit: int
) -> list[dict[str, object]]:
    statement = (
        select(EntityMention, EntityResolutionDecision)
        .join(EntityResolutionDecision, EntityResolutionDecision.mention_id == EntityMention.id)
        .where(EntityResolutionDecision.is_current.is_(True), EntityResolutionDecision.status == "review_required")
        .order_by(EntityMention.created_at, EntityMention.id)
    )
    if domain_id is not None:
        statement = statement.where(EntityMention.domain_id == domain_id)
    return [
        _mention_payload(mention, decision)
        for mention, decision in session.execute(statement.limit(limit)).all()
        if _can_access_domain(request, mention.domain_id, "review")
    ]


def _mention_payload(mention: EntityMention, decision: EntityResolutionDecision) -> dict[str, object]:
    return {
        "id": mention.id,
        "text": mention.text,
        "proposed_type": mention.proposed_type,
        "domain_id": mention.domain_id,
        "source_element_id": mention.source_element_id,
        "idea_block_revision_id": mention.idea_block_revision_id,
        "candidate_entity_ids": mention.candidate_entity_ids,
        "ontology_version": mention.ontology_version,
        "decision": _decision_payload(decision),
    }


def _governance_payload(session: Session, entity_id: str) -> list[dict[str, str]]:
    return [
        {"record_id": record.id, "record_type": record.record_type, "role": link.role}
        for record, link in session.execute(
            select(GovernanceRecord, GovernanceRecordEntity)
            .join(GovernanceRecordEntity, GovernanceRecordEntity.governance_record_id == GovernanceRecord.id)
            .where(GovernanceRecordEntity.entity_id == entity_id)
            .order_by(GovernanceRecord.record_type, GovernanceRecord.id)
        ).all()
    ]


def _actor(request: Request) -> str:
    principal_id = getattr(request.state, "principal_id", None)
    return principal_id if isinstance(principal_id, str) and principal_id else "system"


def _commit_and_enqueue(request: Request, session: Session) -> tuple[str, ...]:
    job_ids = tuple(sorted(session.info.get("entity_reresolution_job_ids", set())))
    session.commit()
    for job_id in job_ids:
        try:
            enqueue = getattr(request.app.state, "enqueue_entity_reresolution", None)
            if callable(enqueue):
                enqueue(job_id)
            else:
                reresolve_affected_entity_catalog.delay(job_id)
        except Exception as exc:  # queue recovery remains possible from the queued durable job
            logger.exception("unable to dispatch entity re-resolution job %s", job_id)
            job = session.get(EntityReresolutionJob, job_id)
            if job is not None:
                job.metadata_ = {**(job.metadata_ or {}), "dispatch_error": str(exc)}
                session.commit()
    return job_ids
