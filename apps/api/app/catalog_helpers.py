from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import and_, or_, select, true
from sqlalchemy.orm import aliased

from app.authz import has_permission, require_permission
from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord, GovernanceRecordRevision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRelation, IdeaBlockRevision
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage


def source_document_dict(row: SourceDocument) -> dict[str, object]:
    return {
        "id": row.id,
        "title": row.title,
        "source_uri": row.source_uri,
        "object_key": row.object_key,
        "media_type": row.media_type,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "domain_id": row.domain_id,
        "category": row.category,
        "sensitivity": row.sensitivity,
        "status": row.status,
        "owner": row.owner,
        "steward": row.steward,
        "metadata": row.metadata_ or {},
        "dublin_core": row.dublin_core or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def page_dict(row: SourcePage) -> dict[str, object]:
    return {
        "id": row.id,
        "source_version_id": row.source_version_id,
        "page_number": row.page_number,
        "content_checksum": row.content_checksum,
        "classification": row.classification,
        "metadata": row.metadata_ or {},
    }


def element_dict(row: SourceElement) -> dict[str, object]:
    return {
        "id": row.id,
        "source_version_id": row.source_version_id,
        "source_page_id": row.source_page_id,
        "parent_element_id": row.parent_element_id,
        "native_element_id": row.native_element_id,
        "kind": row.kind,
        "element_type": row.kind,
        "ordinal": row.ordinal,
        "text": row.text,
        "checksum": row.checksum,
        "page_number": row.page_number,
        "char_start": row.char_start,
        "char_end": row.char_end,
        "bounding_box": row.bounding_box,
        "asset_key": row.asset_key,
        "converter_method": row.converter_method,
        "converter_version": row.converter_version,
        "confidence": row.confidence,
    }


def idea_block_dict(session: Any, request: Request, row: IdeaBlock) -> dict[str, object]:
    revisions = session.scalars(
        select(IdeaBlockRevision)
        .where(IdeaBlockRevision.idea_block_id == row.id)
        .order_by(IdeaBlockRevision.revision_number)
    ).all()
    current_revision = next((revision for revision in revisions if revision.id == row.current_revision_id), None)
    if current_revision is None:
        raise ValueError(f"IdeaBlock {row.id} has no current revision")
    evidence = session.scalars(
        select(ClaimEvidence)
        .where(
            ClaimEvidence.idea_block_id == row.id,
            ClaimEvidence.idea_block_revision_id == current_revision.id,
            ClaimEvidence.status == "active",
        )
        .order_by(ClaimEvidence.created_at, ClaimEvidence.id)
    ).all()
    element_ids = {item.source_element_id for item in evidence if item.source_element_id}
    source_rows = (
        session.execute(
            select(SourceElement, SourceDocumentVersion, SourceDocument)
            .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
            .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
            .join(
                Entity,
                (Entity.id == SourceDocument.category_id)
                & (Entity.ontology_type == "category")
                & (Entity.lifecycle_status == "active"),
            )
            .where(
                SourceElement.id.in_(element_ids),
                SourceDocumentVersion.status == "active",
                SourceDocument.domain_id == row.domain_id,
            )
        ).all()
        if element_ids
        else []
    )
    source_by_element = {element.id: (element, version, source) for element, version, source in source_rows}
    governance_revision_ids = {
        item.governance_record_revision_id for item in evidence if item.governance_record_revision_id
    }
    governance_rows = (
        session.execute(
            select(GovernanceRecordRevision, GovernanceRecord)
            .join(GovernanceRecord, GovernanceRecord.id == GovernanceRecordRevision.governance_record_id)
            .where(
                GovernanceRecordRevision.id.in_(governance_revision_ids),
                GovernanceRecord.current_revision_id == GovernanceRecordRevision.id,
                GovernanceRecord.domain_id == row.domain_id,
            )
        ).all()
        if governance_revision_ids
        else []
    )
    governance_by_revision = {revision.id: record for revision, record in governance_rows}
    evidence = [
        item
        for item in evidence
        if (item.source_element_id is not None and item.source_element_id in source_by_element)
        or (
            item.governance_record_revision_id is not None
            and item.governance_record_revision_id in governance_by_revision
        )
    ]
    relations = session.scalars(
        select(IdeaBlockRelation)
        .where(
            (IdeaBlockRelation.source_idea_block_id == row.id) | (IdeaBlockRelation.target_idea_block_id == row.id),
            IdeaBlockRelation.status == "verified",
            IdeaBlockRelation.decision_provenance != {},
            _current_verified_idea_block(IdeaBlockRelation.source_idea_block_id, domain_id=row.domain_id),
            _current_verified_idea_block(IdeaBlockRelation.target_idea_block_id, domain_id=row.domain_id),
        )
        .order_by(IdeaBlockRelation.created_at, IdeaBlockRelation.id)
    ).all()
    relation_block_ids = {
        block_id
        for relation in relations
        for block_id in (relation.source_idea_block_id, relation.target_idea_block_id)
    }
    relation_blocks = (
        session.scalars(select(IdeaBlock).where(IdeaBlock.id.in_(relation_block_ids))).all()
        if relation_block_ids
        else []
    )
    readable_relation_block_ids = readable_idea_block_ids(session, request, relation_blocks)
    relations = [
        relation
        for relation in relations
        if relation.source_idea_block_id in readable_relation_block_ids
        and relation.target_idea_block_id in readable_relation_block_ids
    ]
    return {
        "id": row.id,
        "domain_id": row.domain_id,
        "current_revision_id": row.current_revision_id,
        "statement": current_revision.statement,
        "category": (current_revision.metadata_ or {}).get("category", ""),
        "type": current_revision.type,
        "confidence": current_revision.confidence,
        "status": current_revision.publication_status,
        "revision_number": current_revision.revision_number,
        "publication_status": current_revision.publication_status,
        "polarity": current_revision.polarity,
        "qualifiers": current_revision.qualifiers,
        "valid_from": current_revision.valid_from.isoformat() if current_revision.valid_from else None,
        "valid_to": current_revision.valid_to.isoformat() if current_revision.valid_to else None,
        "revisions": [
            {
                "id": revision.id,
                "revision_number": revision.revision_number,
                "statement": revision.statement,
                "normalized_statement": revision.normalized_statement,
                "type": revision.type,
                "confidence": revision.confidence,
                "polarity": revision.polarity,
                "qualifiers": revision.qualifiers,
                "valid_from": revision.valid_from.isoformat() if revision.valid_from else None,
                "valid_to": revision.valid_to.isoformat() if revision.valid_to else None,
                "publication_status": revision.publication_status,
                "ontology_version": revision.ontology_version,
                "actor": revision.actor,
                "process": revision.process,
                "change_reason": revision.change_reason,
                "created_at": revision.created_at.isoformat() if revision.created_at else None,
            }
            for revision in revisions
        ],
        "evidence": [_evidence_dict(item, source_by_element, governance_by_revision, request) for item in evidence],
        "relations": [
            {
                "id": relation.id,
                "source_idea_block_id": relation.source_idea_block_id,
                "target_idea_block_id": relation.target_idea_block_id,
                "relation_type": relation.relation_type,
                "direction": relation.direction,
                "status": relation.status,
                "valid_from": relation.valid_from.isoformat() if relation.valid_from else None,
                "valid_to": relation.valid_to.isoformat() if relation.valid_to else None,
                "confidence": float(relation.confidence),
                "decision_provenance": relation.decision_provenance or {},
            }
            for relation in relations
        ],
        "metadata": current_revision.metadata_ or {},
    }


def _evidence_dict(
    item: ClaimEvidence,
    source_by_element: dict[str, tuple[SourceElement, SourceDocumentVersion, SourceDocument]],
    governance_by_revision: dict[str, GovernanceRecord],
    request: Request,
) -> dict[str, object]:
    source = source_by_element.get(item.source_element_id) if item.source_element_id else None
    if source is not None:
        element, version, document = source
        if version.status != "active":
            return _redacted_evidence_dict(item, "source_version_inactive")
        if can_read_source(request, document):
            return {
                "id": item.id,
                "idea_block_revision_id": item.idea_block_revision_id,
                "source_element_id": element.id,
                "source_page_id": element.source_page_id,
                "source_quote": item.source_quote,
                "exact_quote_start": item.quote_char_start,
                "exact_quote_end": item.quote_char_end,
                "evidence_kind": item.evidence_kind,
                "polarity": item.polarity,
                "status": item.status,
                "redacted": False,
                "redaction_reason": None,
            }
        return _redacted_evidence_dict(item, "source_access_restricted")

    governance_record = (
        governance_by_revision.get(item.governance_record_revision_id) if item.governance_record_revision_id else None
    )
    if governance_record is not None and has_permission(
        permissions=request.state.permissions,
        domain_id=governance_record.domain_id,
        category=f"governance:{governance_record.record_type}",
        sensitivity=None,
        action="read",
    ):
        if governance_record.current_revision_id != item.governance_record_revision_id:
            return _redacted_evidence_dict(item, "governance_revision_stale")
        return {
            "id": item.id,
            "idea_block_revision_id": item.idea_block_revision_id,
            "source_element_id": None,
            "source_page_id": None,
            "source_quote": item.source_quote,
            "exact_quote_start": item.quote_char_start,
            "exact_quote_end": item.quote_char_end,
            "evidence_kind": item.evidence_kind,
            "polarity": item.polarity,
            "status": item.status,
            "redacted": False,
            "redaction_reason": None,
        }
    return _redacted_evidence_dict(item, "governance_access_restricted")


def _redacted_evidence_dict(item: ClaimEvidence, reason: str) -> dict[str, object]:
    return {
        "id": item.id,
        "idea_block_revision_id": item.idea_block_revision_id,
        "source_element_id": None,
        "source_page_id": None,
        "source_quote": None,
        "exact_quote_start": None,
        "exact_quote_end": None,
        "evidence_kind": item.evidence_kind,
        "polarity": item.polarity,
        "status": item.status,
        "redacted": True,
        "redaction_reason": reason,
    }


def _current_verified_idea_block(endpoint_column: Any, *, domain_id: str | None = None) -> Any:
    """Require relation endpoints to resolve to current verified revisions."""

    query = (
        select(IdeaBlock.id)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .where(IdeaBlock.id == endpoint_column)
        .where(
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
            IdeaBlockRevision.publication_status == "verified",
        )
    )
    if domain_id is not None:
        query = query.where(IdeaBlock.domain_id == domain_id)
    return query.exists()


def require_source(session: Any, source_document_id: str) -> SourceDocument:
    source = session.get(SourceDocument, source_document_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source document not found")
    return source


def require_idea_block(session: Any, idea_block_id: str) -> IdeaBlock:
    block = session.get(IdeaBlock, idea_block_id)
    if block is None:
        raise HTTPException(status_code=404, detail="IdeaBlock not found")
    return block


def can_read_source(request: Request, source: SourceDocument, session: Any | None = None) -> bool:
    category = source.category
    if session is not None:
        category_entity = session.get(Entity, source.category_id)
        if (
            category_entity is None
            or category_entity.ontology_type != "category"
            or category_entity.lifecycle_status != "active"
        ):
            return False
        category = category_entity.preferred_label
    return has_permission(
        permissions=request.state.permissions,
        domain_id=source.domain_id,
        category=category,
        sensitivity=source.sensitivity,
        action="read",
    )


def require_source_read(request: Request, source: SourceDocument, session: Any | None = None) -> None:
    category = source.category
    if session is not None:
        category_entity = session.get(Entity, source.category_id)
        if (
            category_entity is None
            or category_entity.ontology_type != "category"
            or category_entity.lifecycle_status != "active"
        ):
            raise HTTPException(status_code=409, detail="source document has an invalid category reference")
        category = category_entity.preferred_label
    require_permission(
        permissions=request.state.permissions,
        domain_id=source.domain_id,
        category=category,
        sensitivity=source.sensitivity,
        action="read",
    )


def can_read_entity(session: Any, request: Request, entity: Entity) -> bool:
    return has_permission(
        permissions=request.state.permissions,
        domain_id=entity.domain_id,
        category=None,
        sensitivity=None,
        action="read",
    )


def readable_entity_ids(session: Any, request: Request, entity_ids: set[str]) -> set[str]:
    if not entity_ids:
        return set()

    entities = session.scalars(
        select(Entity).where(Entity.id.in_(entity_ids), Entity.lifecycle_status == "active")
    ).all()
    return {entity.id for entity in entities if can_read_entity(session, request, entity)}


def can_read_idea_block(session: Any, request: Request, block: IdeaBlock) -> bool:
    if block.merged_into_id is not None or block.tombstoned_at is not None or block.archived_at is not None:
        return False
    sources = session.scalars(
        select(SourceDocument)
        .join(SourceDocumentVersion, SourceDocumentVersion.source_document_id == SourceDocument.id)
        .join(SourceElement, SourceElement.source_version_id == SourceDocumentVersion.id)
        .join(ClaimEvidence, ClaimEvidence.source_element_id == SourceElement.id)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == block.current_revision_id) & (IdeaBlockRevision.idea_block_id == block.id),
        )
        .join(
            Entity,
            (Entity.id == SourceDocument.category_id)
            & (Entity.ontology_type == "category")
            & (Entity.lifecycle_status == "active"),
        )
        .where(
            ClaimEvidence.idea_block_id == block.id,
            ClaimEvidence.idea_block_revision_id == block.current_revision_id,
            ClaimEvidence.status == "active",
            SourceDocumentVersion.status == "active",
            SourceDocument.domain_id == block.domain_id,
        )
        .distinct()
    ).all()
    if sources:
        return any(can_read_source(request, source, session) for source in sources)
    linked_entities = linked_entities_for_idea_block(session, block.id)
    if linked_entities:
        return all(can_read_entity(session, request, entity) for entity in linked_entities)
    return False


def readable_idea_block_ids(session: Any, request: Request, blocks: list[IdeaBlock]) -> set[str]:
    block_ids = [block.id for block in blocks]
    if not block_ids:
        return set()

    source_rows = session.execute(
        select(IdeaBlock.id, SourceDocument.domain_id, Entity.preferred_label, SourceDocument.sensitivity)
        .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
        .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .join(
            Entity,
            (Entity.id == SourceDocument.category_id)
            & (Entity.ontology_type == "category")
            & (Entity.lifecycle_status == "active"),
        )
        .where(
            IdeaBlock.id.in_(block_ids),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
            ClaimEvidence.idea_block_revision_id == IdeaBlock.current_revision_id,
            ClaimEvidence.status == "active",
            SourceDocumentVersion.status == "active",
            SourceDocument.domain_id == IdeaBlock.domain_id,
        )
    ).all()
    source_permissions: dict[str, list[bool]] = {}
    for block_id, domain_id, category, sensitivity in source_rows:
        source_permissions.setdefault(block_id, []).append(
            has_permission(
                permissions=request.state.permissions,
                domain_id=domain_id,
                category=category,
                sensitivity=sensitivity,
                action="read",
            )
        )

    linked_entities: dict[str, list[Entity]] = {}
    linked_entity_ids: set[str] = set()
    entity_rows = session.execute(
        select(IdeaBlockEntity.idea_block_id, Entity)
        .join(Entity, Entity.id == IdeaBlockEntity.entity_id)
        .join(IdeaBlock, IdeaBlock.id == IdeaBlockEntity.idea_block_id)
        .where(
            IdeaBlockEntity.idea_block_id.in_(block_ids),
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
        )
        .where(Entity.lifecycle_status == "active")
        .where(or_(Entity.domain_id.is_(None), Entity.domain_id == IdeaBlock.domain_id))
    ).all()
    for block_id, entity in entity_rows:
        linked_entities.setdefault(block_id, []).append(entity)
        linked_entity_ids.add(entity.id)
    readable_entities = readable_entity_ids(session, request, linked_entity_ids)

    readable: set[str] = set()
    for block in blocks:
        if block.id in source_permissions:
            if any(source_permissions[block.id]):
                readable.add(block.id)
            continue
        entities = linked_entities.get(block.id, [])
        if entities:
            if all(entity.id in readable_entities for entity in entities):
                readable.add(block.id)
            continue
    return readable


def readable_source_element_ids(session: Any, request: Request, element_ids: set[str]) -> set[str]:
    """Return the source elements the caller may read.

    A statement is only as readable as the source it quotes, so a surface that
    quotes evidence must first know which sources the reader may read.
    """

    if not element_ids:
        return set()
    rows = session.execute(
        select(SourceElement.id, SourceDocument)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .where(SourceElement.id.in_(element_ids))
    ).all()
    readable: set[str] = set()
    for element_id, source in rows:
        if can_read_source(request, source, session):
            readable.add(element_id)
    return readable


def require_idea_block_write(session: Any, request: Request, block: IdeaBlock) -> None:
    source = session.scalar(
        select(SourceDocument)
        .join(SourceDocumentVersion, SourceDocumentVersion.source_document_id == SourceDocument.id)
        .join(SourceElement, SourceElement.source_version_id == SourceDocumentVersion.id)
        .join(ClaimEvidence, ClaimEvidence.source_element_id == SourceElement.id)
        .join(IdeaBlock, IdeaBlock.id == ClaimEvidence.idea_block_id)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == IdeaBlock.current_revision_id) & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
        )
        .join(
            Entity,
            (Entity.id == SourceDocument.category_id)
            & (Entity.ontology_type == "category")
            & (Entity.lifecycle_status == "active"),
        )
        .where(
            ClaimEvidence.idea_block_id == block.id,
            ClaimEvidence.idea_block_revision_id == IdeaBlock.current_revision_id,
            ClaimEvidence.status == "active",
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
            SourceDocumentVersion.status == "active",
            SourceDocument.domain_id == IdeaBlock.domain_id,
        )
        .limit(1)
    )
    if source is not None:
        category = session.scalar(select(Entity.preferred_label).where(Entity.id == source.category_id))
        require_permission(
            permissions=request.state.permissions,
            domain_id=source.domain_id,
            category=category,
            sensitivity=source.sensitivity,
            action="write",
        )
        return
    linked_entities = linked_entities_for_idea_block(session, block.id)
    if linked_entities:
        for entity in linked_entities:
            require_permission(
                permissions=request.state.permissions,
                domain_id=entity.domain_id,
                category=None,
                sensitivity=None,
                action="write",
            )
        return
    raise HTTPException(status_code=403, detail="missing write access for IdeaBlock")


def entity_source_classification_filter(*, domain_id: str | None = None, category: str | None = None) -> Any:
    clauses: list[Any] = []
    if domain_id is not None:
        clauses.append(Entity.domain_id == domain_id)
    if category is not None:
        category_entity = aliased(Entity)
        evidence_category = (
            select(1)
            .select_from(IdeaBlockEntity)
            .join(IdeaBlock, IdeaBlock.id == IdeaBlockEntity.idea_block_id)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
            .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
            .join(
                category_entity,
                (category_entity.id == SourceDocument.category_id)
                & (category_entity.ontology_type == "category")
                & (category_entity.lifecycle_status == "active"),
            )
            .where(
                IdeaBlockEntity.entity_id == Entity.id,
                SourceDocument.domain_id == IdeaBlock.domain_id,
                IdeaBlockRevision.idea_block_id == IdeaBlock.id,
                IdeaBlock.merged_into_id.is_(None),
                IdeaBlock.tombstoned_at.is_(None),
                IdeaBlock.archived_at.is_(None),
                ClaimEvidence.idea_block_revision_id == IdeaBlockRevision.id,
                ClaimEvidence.status == "active",
                SourceDocumentVersion.status == "active",
                category_entity.preferred_label == category,
            )
            .exists()
        )
        clauses.append(evidence_category)
    return true() if not clauses else clauses[0] if len(clauses) == 1 else and_(*clauses)


def linked_entities_for_idea_block(session: Any, idea_block_id: str) -> list[Entity]:
    return list(
        session.execute(
            select(Entity)
            .join(IdeaBlockEntity, IdeaBlockEntity.entity_id == Entity.id)
            .join(IdeaBlock, IdeaBlock.id == IdeaBlockEntity.idea_block_id)
            .where(
                IdeaBlockEntity.idea_block_id == idea_block_id,
                Entity.lifecycle_status == "active",
                or_(Entity.domain_id.is_(None), Entity.domain_id == IdeaBlock.domain_id),
            )
        ).scalars()
    )


def require_any_read(request: Request) -> None:
    if not has_any_permission(request, "read"):
        raise HTTPException(status_code=403, detail="missing read access")


def require_any_review(request: Request) -> None:
    if not has_any_permission(request, "review"):
        raise HTTPException(status_code=403, detail="missing review access")


def has_any_permission(request: Request, action: str) -> bool:
    permissions = request.state.permissions
    grant_sets = (
        *permissions.domain_permissions.values(),
        *permissions.category_permissions.values(),
        *permissions.sensitivity_permissions.values(),
    )
    return any(action in grants for grants in grant_sets)
