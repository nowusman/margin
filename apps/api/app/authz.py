from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_auth.permissions import PermissionSnapshot
from margin_db.entity_models import Entity
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_schema.ontology import GOVERNANCE_RECORD_TYPES


def require_permission(
    *,
    permissions: PermissionSnapshot,
    domain_id: str | None,
    category: str | None,
    sensitivity: str | None,
    action: str,
) -> None:
    if domain_id and not permissions.allows_domain(domain_id, action):
        raise HTTPException(status_code=403, detail=f"missing {action} access for domain {domain_id}")
    if category and not permissions.allows_category(category, action):
        raise HTTPException(status_code=403, detail=f"missing {action} access for category {category}")
    if sensitivity and not permissions.allows_sensitivity(sensitivity, action):
        raise HTTPException(status_code=403, detail=f"missing {action} access for sensitivity {sensitivity}")


def has_permission(
    *,
    permissions: PermissionSnapshot,
    domain_id: str | None,
    category: str | None,
    sensitivity: str | None,
    action: str,
) -> bool:
    if domain_id and not permissions.allows_domain(domain_id, action):
        return False
    if category and not permissions.allows_category(category, action):
        return False
    if sensitivity and not permissions.allows_sensitivity(sensitivity, action):
        return False
    return True


def require_any_domain_permission(
    *, permissions: PermissionSnapshot, action: str, detail: str = "missing required domain permission"
) -> None:
    if not any(action in grants for grants in permissions.domain_permissions.values()):
        raise HTTPException(status_code=403, detail=detail)


def require_governance_permission(
    *, permissions: PermissionSnapshot, domain_id: str, record_type: str, operation: str
) -> None:
    """Map governance operations onto the read/write/review grants.

    Record types are category-scoped so a principal can be allowed to manage a
    risk without also receiving write access to ADRs in the same domain.
    """

    actions = {"read": "read", "create": "write", "manage": "write", "approve": "review", "transition": "review"}
    action = actions.get(operation)
    if action is None:
        raise HTTPException(status_code=500, detail=f"unknown governance operation: {operation}")
    require_permission(
        permissions=permissions,
        domain_id=domain_id,
        category=f"governance:{record_type}",
        sensitivity=None,
        action=action,
    )


def permitted_governance_record_types(permissions: PermissionSnapshot, operation: str) -> frozenset[str]:
    """Return record types authorized before the governance list query runs."""

    actions = {"read": "read", "create": "write", "manage": "write", "approve": "review", "transition": "review"}
    action = actions.get(operation)
    if action is None:
        return frozenset()
    return frozenset(
        record_type
        for record_type in GOVERNANCE_RECORD_TYPES
        if permissions.allows_category(f"governance:{record_type}", action)
    )


def permitted_governance_domains(permissions: PermissionSnapshot, operation: str) -> frozenset[str] | None:
    """Return domain IDs authorized before a governance list query runs.

    ``None`` is the explicit wildcard scope, never an implicit all-domain read.
    """

    actions = {"read": "read", "create": "write", "manage": "write", "approve": "review", "transition": "review"}
    action = actions.get(operation)
    if action is None:
        return frozenset()
    if permissions.allows_domain("*", action):
        return None
    return frozenset(
        domain_id for domain_id in permissions.domain_permissions if permissions.allows_domain(domain_id, action)
    )


def require_entity_permission(
    session: Session, entity_id: str, *, permissions: PermissionSnapshot, action: str
) -> Entity:
    entity = session.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")
    if entity.lifecycle_status != "active":
        raise HTTPException(status_code=403, detail="entity is not active")
    sources = _entity_sources(session, entity.id)
    if not sources:
        require_permission(
            permissions=permissions, domain_id=entity.domain_id, category=None, sensitivity=None, action=action
        )
        return entity
    if not any(
        has_permission(
            permissions=permissions,
            domain_id=source.domain_id,
            category=source.category,
            sensitivity=source.sensitivity,
            action=action,
        )
        for source in sources
    ):
        raise HTTPException(status_code=403, detail=f"missing {action} access for entity")
    return entity


def require_source_permission(
    session: Session, source_document_id: str, *, permissions: PermissionSnapshot, action: str
) -> SourceDocument:
    source = session.get(SourceDocument, source_document_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source document not found")
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
    return source


def permitted_source_element_ids(
    session: Session, source_element_ids: Iterable[str], *, permissions: PermissionSnapshot, action: str
) -> frozenset[str]:
    """Return only source elements whose parent source remains readable."""

    element_ids = frozenset(source_element_ids)
    if not element_ids:
        return frozenset()
    rows = session.execute(
        select(SourceElement.id, SourceDocument.domain_id, Entity.preferred_label, SourceDocument.sensitivity)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .join(Entity, (Entity.id == SourceDocument.category_id) & (Entity.ontology_type == "category"))
        .where(
            SourceElement.id.in_(element_ids),
            SourceDocumentVersion.status == "active",
            Entity.lifecycle_status == "active",
        )
    ).all()
    return frozenset(
        element_id
        for element_id, domain_id, category, sensitivity in rows
        if has_permission(
            permissions=permissions, domain_id=domain_id, category=category, sensitivity=sensitivity, action=action
        )
    )


def _entity_sources(session: Session, entity_id: str) -> list[SourceDocument]:
    return list(
        session.execute(
            select(SourceDocument)
            .join(SourceDocumentVersion, SourceDocumentVersion.source_document_id == SourceDocument.id)
            .join(SourceElement, SourceElement.source_version_id == SourceDocumentVersion.id)
            .join(ClaimEvidence, ClaimEvidence.source_element_id == SourceElement.id)
            .join(IdeaBlock, IdeaBlock.id == ClaimEvidence.idea_block_id)
            .join(IdeaBlockEntity, IdeaBlockEntity.idea_block_id == IdeaBlock.id)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .join(Entity, (Entity.id == SourceDocument.category_id) & (Entity.ontology_type == "category"))
            .where(
                IdeaBlockEntity.entity_id == entity_id,
                ClaimEvidence.status == "active",
                ClaimEvidence.idea_block_revision_id == IdeaBlock.current_revision_id,
                IdeaBlock.merged_into_id.is_(None),
                IdeaBlock.tombstoned_at.is_(None),
                IdeaBlock.archived_at.is_(None),
                SourceDocument.domain_id == IdeaBlock.domain_id,
                SourceDocumentVersion.status == "active",
                Entity.lifecycle_status == "active",
            )
            .distinct()
        ).scalars()
    )
