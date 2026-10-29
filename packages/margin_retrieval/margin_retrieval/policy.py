from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

import margin_retrieval.support as retrieval_support
from margin_auth.permissions import PermissionSnapshot
from margin_db.entity_models import Entity
from margin_db.governance_types import LIFECYCLE_STATES, RECORD_TYPES
from margin_db.seed import IDEA_BLOCK_TYPE_TERMS
from margin_db.source_models import SourceDocument, SourceDocumentVersion
from margin_retrieval.interfaces import Citation, RetrievalExpansion, RetrievalScope
from margin_schema.ontology import (
    ENTITY_RELATIONSHIP_TYPES,
    IDEA_BLOCK_RELATIONSHIP_TYPES,
    normalize_entity_kind,
    normalize_relationship_type,
)
from margin_schema.text_normalization import normalize_canonical_text

_allowed_keys = retrieval_support.allowed_keys


class RetrievalPermissionError(ValueError):
    """Raised when a requested retrieval scope exceeds caller permissions."""


class CitationSufficiencyError(ValueError):
    """Raised when answer text has material claims without retrieved evidence."""


GOVERNANCE_RECORD_TYPES = ("adr", "risk", "waiver", "technical_debt", "architecture_backlog")

RETRIEVAL_CANDIDATE_LIMIT = 80
RETRIEVAL_RERANK_CANDIDATE_LIMIT = 32
RECIPROCAL_RANK_FUSION_K = 60
RETRIEVAL_EXPANSION_LIMIT = 20
RETRIEVAL_EXPANSION_MAX_DEPTH = 2
RETRIEVAL_EXPANSION_RELATION_TYPE_LIMIT = 8
RETRIEVAL_EXPANSION_DEADLINE_MS = 250
IDEA_BLOCK_EXPANSION_RELATION_TYPES = IDEA_BLOCK_RELATIONSHIP_TYPES


def normalize_query(value: str) -> str:
    return normalize_canonical_text(value)


def validate_expansion(expansion: RetrievalExpansion | None) -> RetrievalExpansion:
    """Normalize and bound explicit post-retrieval relation traversal."""

    value = expansion or RetrievalExpansion()
    if value.depth < 0 or value.depth > RETRIEVAL_EXPANSION_MAX_DEPTH:
        raise RetrievalPermissionError(f"expansion depth must be between 0 and {RETRIEVAL_EXPANSION_MAX_DEPTH}")
    if value.limit < 0 or value.limit > RETRIEVAL_EXPANSION_LIMIT:
        raise RetrievalPermissionError(f"expansion limit must be between 0 and {RETRIEVAL_EXPANSION_LIMIT}")
    if value.depth == 0:
        if value.relation_types or value.limit:
            raise RetrievalPermissionError("expansion relation types and limit require a positive depth")
        return RetrievalExpansion()
    if not value.relation_types or value.limit == 0:
        raise RetrievalPermissionError("expansion requires relation types and a positive limit")
    if len(value.relation_types) > RETRIEVAL_EXPANSION_RELATION_TYPE_LIMIT:
        raise RetrievalPermissionError(
            "expansion includes too many relation types: " f"{RETRIEVAL_EXPANSION_RELATION_TYPE_LIMIT} maximum"
        )
    normalized: list[str] = []
    for relation_type in value.relation_types:
        entity_type = normalize_relationship_type(relation_type)
        block_type = relation_type.strip().casefold().replace("-", "_").replace(" ", "_")
        if entity_type is not None and entity_type in ENTITY_RELATIONSHIP_TYPES:
            normalized.append(entity_type)
        elif block_type in IDEA_BLOCK_EXPANSION_RELATION_TYPES:
            normalized.append(block_type)
        else:
            raise RetrievalPermissionError(f"query scope includes an unknown expansion relation type: {relation_type}")
    return RetrievalExpansion(relation_types=tuple(dict.fromkeys(normalized)), depth=value.depth, limit=value.limit)


def _has_cited_evidence_chain(citation: Citation | None) -> bool:
    return bool(
        citation
        and (
            (
                citation.source_document_id
                and citation.source_version_id
                and citation.source_element_id
                and citation.idea_block_id
            )
            or (citation.governance_record_id and citation.governance_record_revision_id)
        )
    )


class ScopeAuthorizer:
    BASELINE_STATUSES = frozenset({"verified", "candidate"})
    BASELINE_SENSITIVITIES = frozenset({"public", "internal", "confidential", "regulated", "security-sensitive"})

    def authorize(self, session: Session, permissions: PermissionSnapshot, scope: RetrievalScope) -> RetrievalScope:
        allowed_domains = _allowed_keys(permissions.domain_permissions, "read")
        allowed_categories = _allowed_keys(permissions.category_permissions, "read")
        if not allowed_domains:
            raise RetrievalPermissionError("caller has no readable Margin domains")
        if not allowed_categories:
            raise RetrievalPermissionError("caller has no readable Margin categories")

        if scope.all_domain_ids_authorized and "*" not in allowed_domains:
            raise RetrievalPermissionError("caller no longer has all-domain retrieval access")
        if scope.all_category_ids_authorized and "*" not in allowed_categories:
            raise RetrievalPermissionError("caller no longer has all-category retrieval access")
        domain_ids = self._authorized_values(scope.domain_ids, allowed_domains)
        category_ids, all_category_ids_authorized = self._authorized_category_ids(
            session, requested_category_ids=scope.category_ids, allowed_category_labels=allowed_categories
        )
        statuses = scope.statuses or ("verified",)
        allowed_sensitivities = _allowed_keys(permissions.sensitivity_permissions, "read")
        if "*" in allowed_sensitivities:
            allowed_sensitivities = set(self.BASELINE_SENSITIVITIES)
        if not allowed_sensitivities:
            allowed_sensitivities = {"public", "internal"}
        sensitivities = scope.sensitivities or tuple(sorted(allowed_sensitivities))

        denied_domains = self._denied_values(domain_ids, allowed_domains)
        if denied_domains:
            raise RetrievalPermissionError(
                f"query scope includes domains outside caller permissions: {denied_domains[0]}"
            )

        denied_statuses = [value for value in statuses if value not in self.BASELINE_STATUSES]
        if denied_statuses:
            raise RetrievalPermissionError(f"query scope includes unsupported knowledge status: {denied_statuses[0]}")
        if "candidate" in statuses:
            denied_review_domains = _denied_permission_values(permissions.domain_permissions, domain_ids, "review")
            if denied_review_domains:
                raise RetrievalPermissionError(
                    "candidate knowledge requires review permission for domain: " f"{denied_review_domains[0]}"
                )
            self._require_category_review_access(
                session,
                category_ids=category_ids,
                all_categories_authorized=all_category_ids_authorized,
                permissions=permissions,
            )

        denied_sensitivities = [
            value
            for value in sensitivities
            if value not in self.BASELINE_SENSITIVITIES or value not in allowed_sensitivities
        ]
        if denied_sensitivities:
            raise RetrievalPermissionError(
                "query scope includes sensitivity outside baseline read policy: " f"{denied_sensitivities[0]}"
            )
        source_document_ids = self._authorized_source_document_ids(
            session,
            requested_source_document_ids=scope.source_document_ids,
            allowed_domain_ids=allowed_domains,
            allowed_category_labels=allowed_categories,
            allowed_sensitivities=allowed_sensitivities,
        )
        if "candidate" in statuses:
            denied_review_sensitivities = _denied_permission_values(
                permissions.sensitivity_permissions, sensitivities, "review", default_allowed={"public", "internal"}
            )
            if denied_review_sensitivities:
                raise RetrievalPermissionError(
                    "candidate knowledge requires review permission for sensitivity: "
                    f"{denied_review_sensitivities[0]}"
                )

        entity_types = _normalized_entity_types(scope.entity_types)
        relationship_types = _normalized_relationship_types(scope.relationship_types)
        entity_ids = self._authorized_entity_ids(
            session, requested_entity_ids=scope.entity_ids, domain_ids=domain_ids, allowed_domain_ids=allowed_domains
        )
        idea_block_types = _validated_values(
            scope.idea_block_types, allowed=frozenset(IDEA_BLOCK_TYPE_TERMS), label="Idea Block type"
        )
        governance_record_types = _authorized_governance_types(
            requested=scope.governance_record_types, permissions=permissions
        )
        governance_statuses = _validated_values(
            scope.governance_statuses,
            allowed=frozenset().union(*LIFECYCLE_STATES.values()),
            label="governance lifecycle status",
        )
        if scope.governance_statuses and not scope.governance_record_types:
            raise RetrievalPermissionError("governance lifecycle status requires a governance record type")
        if governance_record_types and governance_statuses:
            allowed_governance_statuses = frozenset().union(
                *(LIFECYCLE_STATES[record_type] for record_type in governance_record_types)
            )
            unknown_for_type = next(
                (status for status in governance_statuses if status not in allowed_governance_statuses), None
            )
            if unknown_for_type:
                raise RetrievalPermissionError(
                    "governance lifecycle status is not valid for the selected record type: " f"{unknown_for_type}"
                )
        architecture_backlog_states = _validated_values(
            scope.architecture_backlog_states,
            allowed=LIFECYCLE_STATES["architecture_backlog"],
            label="Architecture Backlog state",
        )
        if scope.architecture_backlog_states and "architecture_backlog" not in scope.governance_record_types:
            raise RetrievalPermissionError(
                "Architecture Backlog state requires the architecture_backlog governance record type"
            )
        return RetrievalScope(
            domain_ids=tuple(domain_ids),
            all_domain_ids_authorized="*" in allowed_domains,
            category_ids=tuple(category_ids),
            entity_ids=entity_ids,
            entity_terms=scope.entity_terms,
            entity_types=entity_types,
            relationship_types=relationship_types,
            idea_block_types=idea_block_types,
            source_document_ids=source_document_ids,
            sensitivities=tuple(sensitivities),
            statuses=tuple(statuses),
            date_from=scope.date_from,
            date_to=scope.date_to,
            governance_record_types=governance_record_types,
            governance_statuses=governance_statuses,
            architecture_backlog_states=architecture_backlog_states,
            all_category_ids_authorized=all_category_ids_authorized,
        )

    @staticmethod
    def _authorized_category_ids(
        session: Session, *, requested_category_ids: tuple[str, ...], allowed_category_labels: set[str]
    ) -> tuple[tuple[str, ...], bool]:
        if "*" in allowed_category_labels:
            if requested_category_ids:
                known_ids = set(
                    session.scalars(
                        select(Entity.id).where(
                            Entity.id.in_(requested_category_ids),
                            Entity.ontology_type == "category",
                            Entity.lifecycle_status == "active",
                        )
                    )
                )
                unknown = next((value for value in requested_category_ids if value not in known_ids), None)
                if unknown:
                    raise RetrievalPermissionError(f"query scope includes unknown category ID: {unknown}")
                return tuple(requested_category_ids), False
            return (), True

        category_query = select(Entity.id, Entity.preferred_label).where(
            Entity.ontology_type == "category", Entity.lifecycle_status == "active"
        )
        if requested_category_ids:
            category_query = category_query.where(Entity.id.in_(requested_category_ids))
        rows = session.execute(category_query).all()
        labels_by_id = {entity_id: label for entity_id, label in rows}
        if requested_category_ids:
            unknown = next((value for value in requested_category_ids if value not in labels_by_id), None)
            if unknown:
                raise RetrievalPermissionError(f"query scope includes unknown category ID: {unknown}")
            denied = next(
                (value for value in requested_category_ids if labels_by_id[value] not in allowed_category_labels), None
            )
            if denied:
                raise RetrievalPermissionError(f"query scope includes category outside caller permissions: {denied}")
            return tuple(requested_category_ids), False
        return tuple(sorted(entity_id for entity_id, label in rows if label in allowed_category_labels)), False

    @staticmethod
    def _authorized_source_document_ids(
        session: Session,
        *,
        requested_source_document_ids: tuple[str, ...],
        allowed_domain_ids: set[str],
        allowed_category_labels: set[str],
        allowed_sensitivities: set[str],
    ) -> tuple[str, ...]:
        """Validate explicit source filters before they reach retrieval SQL."""

        ids = tuple(dict.fromkeys(requested_source_document_ids))
        if not ids:
            return ()
        rows = session.execute(
            select(SourceDocument, Entity.preferred_label, Entity.lifecycle_status)
            .outerjoin(Entity, (Entity.id == SourceDocument.category_id) & (Entity.ontology_type == "category"))
            .where(SourceDocument.id.in_(ids))
        ).all()
        documents_by_id = {
            document.id: (document, category_label, category_status)
            for document, category_label, category_status in rows
        }
        unknown = next((source_id for source_id in ids if source_id not in documents_by_id), None)
        if unknown:
            raise RetrievalPermissionError(f"query scope includes unknown source document ID: {unknown}")
        active_version_document_ids = set(
            session.scalars(
                select(SourceDocumentVersion.source_document_id).where(
                    SourceDocumentVersion.source_document_id.in_(ids), SourceDocumentVersion.status == "active"
                )
            )
        )
        for source_id in ids:
            document, category_label, category_status = documents_by_id[source_id]
            if (
                category_status != "active"
                or not isinstance(category_label, str)
                or not category_label.strip()
                or ("*" not in allowed_domain_ids and document.domain_id not in allowed_domain_ids)
                or ("*" not in allowed_category_labels and category_label not in allowed_category_labels)
                or document.sensitivity not in allowed_sensitivities
            ):
                raise RetrievalPermissionError(
                    f"query scope includes source document outside caller permissions: {source_id}"
                )
            if source_id not in active_version_document_ids:
                raise RetrievalPermissionError(
                    f"query scope includes source document without an active current version: {source_id}"
                )
        return ids

    @staticmethod
    def _authorized_entity_ids(
        session: Session,
        *,
        requested_entity_ids: tuple[str, ...],
        domain_ids: tuple[str, ...],
        allowed_domain_ids: set[str],
    ) -> tuple[str, ...]:
        """Validate explicit entity filters before they reach candidate SQL."""

        ids = tuple(dict.fromkeys(requested_entity_ids))
        if not ids:
            return ()
        rows = session.scalars(select(Entity).where(Entity.id.in_(ids), Entity.lifecycle_status == "active")).all()
        entities_by_id = {entity.id: entity for entity in rows}
        unknown = next((entity_id for entity_id in ids if entity_id not in entities_by_id), None)
        if unknown:
            raise RetrievalPermissionError(f"query scope includes unknown entity ID: {unknown}")
        restricted = next(
            (
                entity
                for entity in rows
                if entity.domain_id is not None
                and (entity.domain_id not in domain_ids if domain_ids else "*" not in allowed_domain_ids)
            ),
            None,
        )
        if restricted is not None:
            raise RetrievalPermissionError(f"query scope includes entity outside caller permissions: {restricted.id}")
        return ids

    @staticmethod
    def _require_category_review_access(
        session: Session,
        *,
        category_ids: tuple[str, ...],
        all_categories_authorized: bool,
        permissions: PermissionSnapshot,
    ) -> None:
        if all_categories_authorized:
            if permissions.allows_category("*", "review"):
                return
            raise RetrievalPermissionError("candidate knowledge requires review permission for all readable categories")
        labels_by_id = {
            entity_id: label
            for entity_id, label in session.execute(
                select(Entity.id, Entity.preferred_label).where(Entity.id.in_(category_ids))
            ).tuples()
        }
        denied = next(
            (
                category_id
                for category_id in category_ids
                if not permissions.allows_category(labels_by_id[category_id], "review")
            ),
            None,
        )
        if denied:
            raise RetrievalPermissionError(f"candidate knowledge requires review permission for category: {denied}")

    @staticmethod
    def _authorized_values(requested: tuple[str, ...], allowed_values: set[str]) -> tuple[str, ...]:
        if requested:
            return requested
        if "*" in allowed_values:
            return ()
        return tuple(sorted(allowed_values))

    @staticmethod
    def _denied_values(values: tuple[str, ...], allowed_values: set[str]) -> list[str]:
        if "*" in allowed_values:
            return []
        return [value for value in values if value not in allowed_values]


def _denied_permission_values(
    permissions_by_value: dict[str, frozenset[str]],
    values: Iterable[str],
    permission: str,
    *,
    default_allowed: set[str] | None = None,
) -> list[str]:
    if permission in permissions_by_value.get("*", frozenset()):
        return []
    denied: list[str] = []
    for value in values:
        explicit_permissions = permissions_by_value.get(value)
        if explicit_permissions is None and default_allowed is not None and value in default_allowed:
            denied.append(value)
        elif permission not in (explicit_permissions or frozenset()):
            denied.append(value)
    return denied


def _normalized_entity_types(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(normalize_entity_kind(value) for value in values))
    if any(value is None for value in normalized):
        raise RetrievalPermissionError("query scope includes an unknown entity type")
    return tuple(value for value in normalized if value is not None)


def _normalized_relationship_types(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(normalize_relationship_type(value) for value in values))
    if any(value is None or value not in ENTITY_RELATIONSHIP_TYPES for value in normalized):
        raise RetrievalPermissionError("query scope includes an unknown ontology relationship type")
    return tuple(value for value in normalized if value is not None)


def _validated_values(values: tuple[str, ...], *, allowed: frozenset[str], label: str) -> tuple[str, ...]:
    unknown = next((value for value in values if value not in allowed), None)
    if unknown:
        raise RetrievalPermissionError(f"query scope includes an unknown {label}: {unknown}")
    return tuple(dict.fromkeys(values))


def _authorized_governance_types(*, requested: tuple[str, ...], permissions: PermissionSnapshot) -> tuple[str, ...]:
    unknown = next((record_type for record_type in requested if record_type not in RECORD_TYPES), None)
    if unknown:
        raise RetrievalPermissionError(f"query scope includes an unknown governance record type: {unknown}")
    allowed = tuple(
        record_type
        for record_type in GOVERNANCE_RECORD_TYPES
        if permissions.allows_category(f"governance:{record_type}", "read")
    )
    if requested:
        denied = next((record_type for record_type in requested if record_type not in allowed), None)
        if denied:
            raise RetrievalPermissionError(
                f"query scope includes a governance type outside caller permissions: {denied}"
            )
        return requested
    return allowed
