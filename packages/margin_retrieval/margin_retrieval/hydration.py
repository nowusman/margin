"""Post-ranking Idea Block result and citation hydration."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import and_, false, or_, select, true
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord, GovernanceRecordIdeaBlock, GovernanceRecordRevision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRelation, IdeaBlockRevision
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_retrieval.candidates import (
    FusedCandidate,
    SourceDiscoveryCandidate,
    authorized_current_revisions_for_block_ids,
)
from margin_retrieval.interfaces import Citation, EvidenceItem, RetrievalScope
from margin_retrieval.support import exclusive_date_to


@dataclass(frozen=True)
class _HydratedRevision:
    block: IdeaBlock
    revision: IdeaBlockRevision


def hydrate_results(
    session: Session, *, candidates: Iterable[FusedCandidate], scope: RetrievalScope
) -> tuple[EvidenceItem, ...]:
    """Hydrate selected revisions after their final rank is fixed."""

    ordered_candidates = _unique_candidates(candidates)
    revision_ids = tuple(candidate.revision_id for candidate in ordered_candidates)
    if not revision_ids:
        return ()

    revisions = _revisions_by_id(session, revision_ids)
    readable_revisions = authorized_current_revisions_for_block_ids(
        session, block_ids=tuple(revision.block.id for revision in revisions.values()), scope=scope
    )
    readable_revision_ids = set(readable_revisions.values())
    ordered_candidates = tuple(
        candidate for candidate in ordered_candidates if candidate.revision_id in readable_revision_ids
    )
    if not ordered_candidates:
        return ()
    revision_ids = tuple(candidate.revision_id for candidate in ordered_candidates)
    block_ids = tuple(
        dict.fromkeys(
            revisions[candidate.revision_id].block.id
            for candidate in ordered_candidates
            if candidate.revision_id in revisions
        )
    )
    citations_by_revision = _citations_by_revision(session, revision_ids, scope)
    entities_by_block = _entities_by_block(session, block_ids, scope)
    governance_by_block = _governance_by_block(session, block_ids, scope)
    relations_by_block = _relations_by_block(session, block_ids, scope)

    results: list[EvidenceItem] = []
    for final_rank, candidate in enumerate(ordered_candidates, start=1):
        hydrated = revisions.get(candidate.revision_id)
        if hydrated is None:
            continue
        block = hydrated.block
        revision = hydrated.revision
        entities = entities_by_block.get(block.id, ())
        results.append(
            EvidenceItem(
                item_id=f"idea_block:{block.id}:{revision.id}",
                kind="idea_block",
                stage="expansion" if "expansion" in candidate.channel_ranks else "rrf",
                score=candidate.score,
                text=revision.statement,
                citations=citations_by_revision.get(revision.id, ()),
                entity_id=entities[0]["id"] if entities else None,
                metadata={
                    "idea_block_id": block.id,
                    "idea_block_revision_id": revision.id,
                    "revision_number": revision.revision_number,
                    "final_rank": final_rank,
                    "channel_ranks": candidate.channel_ranks,
                    "type": revision.type,
                    "publication_status": revision.publication_status,
                    "confidence": revision.confidence,
                    "polarity": revision.polarity,
                    "valid_from": revision.valid_from.isoformat() if revision.valid_from else None,
                    "valid_to": revision.valid_to.isoformat() if revision.valid_to else None,
                    "entities": list(entities),
                    "governance": list(governance_by_block.get(block.id, ())),
                    "relations": list(relations_by_block.get(block.id, ())),
                },
            )
        )
    return tuple(results)


def revision_texts(session: Session, revision_ids: Iterable[str]) -> dict[str, str]:
    """Load only current revision statements for bounded optional reranking."""

    ids = tuple(revision_ids)
    if not ids:
        return {}
    return {
        revision_id: statement
        for revision_id, statement in session.execute(
            select(IdeaBlockRevision.id, IdeaBlockRevision.statement).where(IdeaBlockRevision.id.in_(ids))
        ).tuples()
    }


def hydrate_source_discovery(
    session: Session, *, candidates: Iterable[SourceDiscoveryCandidate], scope: RetrievalScope
) -> tuple[EvidenceItem, ...]:
    """Hydrate explicit source-discovery candidates without mixing them into Idea Block ranking."""

    ordered = tuple(candidates)
    ids = tuple(candidate.source_element_id for candidate in ordered)
    if not ids:
        return ()
    category_entity = aliased(Entity)
    rows = session.execute(
        select(SourceElement, SourcePage, SourceDocumentVersion, SourceDocument)
        .outerjoin(
            SourcePage,
            and_(
                SourcePage.id == SourceElement.source_page_id,
                SourcePage.source_version_id == SourceElement.source_version_id,
            ),
        )
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .join(
            category_entity,
            (category_entity.id == SourceDocument.category_id) & (category_entity.ontology_type == "category"),
        )
        .where(SourceElement.id.in_(ids))
        .where(SourceDocumentVersion.status == "active", category_entity.lifecycle_status == "active")
    ).all()
    sources = {element.id: (element, page, version, document) for element, page, version, document in rows}
    values: list[EvidenceItem] = []
    for candidate in ordered:
        source = sources.get(candidate.source_element_id)
        if source is None:
            continue
        element, page, version, document = source
        if not _source_is_readable(document, scope):
            continue
        values.append(
            EvidenceItem(
                item_id=f"source_element:{element.id}",
                kind="source_element",
                stage="source_discovery",
                score=candidate.score,
                text=element.text or "",
                citations=(
                    Citation(
                        citation_id=f"source_element:{element.id}",
                        source_document_id=document.id,
                        source_document_title=document.title,
                        source_version_id=version.id,
                        source_version_sha256=version.version_sha256,
                        source_page_id=page.id if page else element.source_page_id,
                        source_element_id=element.id,
                        page_number=(
                            element.page_number
                            if element.page_number is not None
                            else (page.page_number if page else None)
                        ),
                        bounding_box=element.bounding_box,
                        quote=element.text,
                        element_type=element.kind,
                        asset_key=element.asset_key,
                    ),
                ),
                metadata={"final_rank": candidate.rank, "source_discovery": True},
            )
        )
    return tuple(values)


def _revisions_by_id(session: Session, revision_ids: tuple[str, ...]) -> dict[str, _HydratedRevision]:
    rows = session.execute(
        select(IdeaBlock, IdeaBlockRevision)
        .join(IdeaBlockRevision, IdeaBlock.current_revision_id == IdeaBlockRevision.id)
        .where(
            IdeaBlockRevision.id.in_(revision_ids),
            IdeaBlockRevision.idea_block_id == IdeaBlock.id,
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
        )
    ).all()
    return {revision.id: _HydratedRevision(block=block, revision=revision) for block, revision in rows}


def _unique_candidates(candidates: Iterable[FusedCandidate]) -> tuple[FusedCandidate, ...]:
    unique: dict[str, FusedCandidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.revision_id, candidate)
    return tuple(unique.values())


def _citations_by_revision(
    session: Session, revision_ids: tuple[str, ...], scope: RetrievalScope
) -> dict[str, tuple[Citation, ...]]:
    category_entity = aliased(Entity)
    source_origin = and_(
        ClaimEvidence.source_element_id.is_not(None),
        ClaimEvidence.governance_record_revision_id.is_(None),
        ClaimEvidence.idea_block_id == IdeaBlock.id,
        IdeaBlock.current_revision_id == ClaimEvidence.idea_block_revision_id,
        IdeaBlock.merged_into_id.is_(None),
        IdeaBlock.tombstoned_at.is_(None),
        IdeaBlock.archived_at.is_(None),
        SourceDocumentVersion.status == "active",
        category_entity.lifecycle_status == "active",
        SourceDocument.domain_id == IdeaBlock.domain_id,
        _source_scope_filter(scope),
    )
    governance_origin = and_(
        ClaimEvidence.governance_record_revision_id.is_not(None),
        ClaimEvidence.source_element_id.is_(None),
        ClaimEvidence.idea_block_id == IdeaBlock.id,
        IdeaBlock.current_revision_id == ClaimEvidence.idea_block_revision_id,
        IdeaBlock.merged_into_id.is_(None),
        IdeaBlock.tombstoned_at.is_(None),
        IdeaBlock.archived_at.is_(None),
        GovernanceRecord.current_revision_id == GovernanceRecordRevision.id,
        GovernanceRecordRevision.governance_record_id == GovernanceRecord.id,
        GovernanceRecord.domain_id == IdeaBlock.domain_id,
        GovernanceRecord.record_type.in_(scope.governance_record_types),
        _governance_scope_filter(scope),
    )
    query = (
        select(
            ClaimEvidence,
            SourceElement,
            SourcePage,
            SourceDocumentVersion,
            SourceDocument,
            GovernanceRecordRevision,
            GovernanceRecord,
        )
        .join(IdeaBlock, IdeaBlock.id == ClaimEvidence.idea_block_id)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == ClaimEvidence.idea_block_revision_id)
            & (IdeaBlockRevision.idea_block_id == IdeaBlock.id)
            & (IdeaBlockRevision.id == IdeaBlock.current_revision_id),
        )
        .outerjoin(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
        .outerjoin(
            SourcePage,
            and_(
                SourcePage.id == SourceElement.source_page_id,
                SourcePage.source_version_id == SourceElement.source_version_id,
            ),
        )
        .outerjoin(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .outerjoin(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .outerjoin(GovernanceRecordRevision, GovernanceRecordRevision.id == ClaimEvidence.governance_record_revision_id)
        .outerjoin(GovernanceRecord, GovernanceRecord.id == GovernanceRecordRevision.governance_record_id)
        .outerjoin(
            category_entity,
            (category_entity.id == SourceDocument.category_id) & (category_entity.ontology_type == "category"),
        )
        .where(ClaimEvidence.idea_block_revision_id.in_(revision_ids))
        .where(ClaimEvidence.status == "active")
        .where(or_(source_origin, governance_origin))
    )
    if scope.date_from:
        query = query.where(or_(ClaimEvidence.valid_to.is_(None), ClaimEvidence.valid_to >= scope.date_from))
    if scope.date_to:
        query = query.where(
            or_(ClaimEvidence.valid_from.is_(None), ClaimEvidence.valid_from < exclusive_date_to(scope.date_to))
        )
    rows = session.execute(query).all()
    values: dict[str, list[Citation]] = defaultdict(list)
    seen: set[tuple[object, ...]] = set()
    for evidence, element, page, version, document, governance_revision, governance_record in rows:
        citation = _citation(
            evidence=evidence,
            element=element,
            page=page,
            version=version,
            document=document,
            governance_revision=governance_revision,
            governance_record=governance_record,
            scope=scope,
        )
        key = (evidence.idea_block_revision_id, *_citation_identity(citation))
        if key in seen:
            continue
        seen.add(key)
        values[evidence.idea_block_revision_id].append(citation)
    restricted_source_query = (
        select(
            ClaimEvidence.id, ClaimEvidence.idea_block_id, ClaimEvidence.idea_block_revision_id, ClaimEvidence.polarity
        )
        .join(IdeaBlock, IdeaBlock.id == ClaimEvidence.idea_block_id)
        .join(
            IdeaBlockRevision,
            (IdeaBlockRevision.id == ClaimEvidence.idea_block_revision_id)
            & (IdeaBlockRevision.idea_block_id == IdeaBlock.id)
            & (IdeaBlockRevision.id == IdeaBlock.current_revision_id),
        )
        .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .join(
            category_entity,
            (category_entity.id == SourceDocument.category_id) & (category_entity.ontology_type == "category"),
        )
        .where(
            ClaimEvidence.idea_block_revision_id.in_(revision_ids),
            ClaimEvidence.idea_block_id == IdeaBlock.id,
            IdeaBlock.current_revision_id == ClaimEvidence.idea_block_revision_id,
            IdeaBlock.merged_into_id.is_(None),
            IdeaBlock.tombstoned_at.is_(None),
            IdeaBlock.archived_at.is_(None),
            ClaimEvidence.status == "active",
            ClaimEvidence.source_element_id.is_not(None),
            SourceDocumentVersion.status == "active",
            category_entity.lifecycle_status == "active",
            SourceDocument.domain_id == IdeaBlock.domain_id,
            ~_source_scope_filter(scope),
        )
    )
    if scope.date_from:
        restricted_source_query = restricted_source_query.where(
            or_(ClaimEvidence.valid_to.is_(None), ClaimEvidence.valid_to >= scope.date_from)
        )
    if scope.date_to:
        restricted_source_query = restricted_source_query.where(
            or_(ClaimEvidence.valid_from.is_(None), ClaimEvidence.valid_from < exclusive_date_to(scope.date_to))
        )
    restricted_rows = session.execute(restricted_source_query).all()
    for evidence_id, idea_block_id, revision_id, polarity in restricted_rows:
        citation = Citation(
            citation_id=evidence_id,
            idea_block_id=idea_block_id,
            idea_block_revision_id=revision_id,
            polarity=polarity,
            redacted=True,
            redaction_reason="source_access_restricted",
        )
        key = (revision_id, *_citation_identity(citation))
        if key in seen:
            continue
        seen.add(key)
        values[revision_id].append(citation)
    return {revision_id: tuple(sorted(citations, key=_citation_order)) for revision_id, citations in values.items()}


def _citation(
    *,
    evidence: ClaimEvidence,
    element: SourceElement | None,
    page: SourcePage | None,
    version: SourceDocumentVersion | None,
    document: SourceDocument | None,
    governance_revision: GovernanceRecordRevision | None,
    governance_record: GovernanceRecord | None,
    scope: RetrievalScope,
) -> Citation:
    if element is not None and version is not None and document is not None:
        if not _source_is_readable(document, scope):
            return Citation(
                citation_id=evidence.id,
                idea_block_id=evidence.idea_block_id,
                idea_block_revision_id=evidence.idea_block_revision_id,
                polarity=evidence.polarity,
                redacted=True,
                redaction_reason="source_access_restricted",
            )
        return Citation(
            citation_id=evidence.id,
            idea_block_id=evidence.idea_block_id,
            idea_block_revision_id=evidence.idea_block_revision_id,
            source_document_id=document.id,
            source_document_title=document.title,
            source_version_id=version.id,
            source_version_sha256=version.version_sha256,
            source_page_id=page.id if page is not None else element.source_page_id,
            source_element_id=element.id,
            page_number=(
                element.page_number if element.page_number is not None else (page.page_number if page else None)
            ),
            bounding_box=element.bounding_box,
            quote=evidence.source_quote,
            quote_char_start=evidence.quote_char_start,
            quote_char_end=evidence.quote_char_end,
            element_type=element.kind,
            polarity=evidence.polarity,
            valid_from=evidence.valid_from,
            valid_to=evidence.valid_to,
            asset_key=element.asset_key,
            confidence=evidence.confidence,
        )
    if governance_revision is not None and governance_record is not None:
        if not _governance_is_readable(governance_record, scope):
            return Citation(
                citation_id=evidence.id,
                idea_block_id=evidence.idea_block_id,
                idea_block_revision_id=evidence.idea_block_revision_id,
                polarity=evidence.polarity,
                redacted=True,
                redaction_reason="governance_access_restricted",
            )
        return Citation(
            citation_id=evidence.id,
            idea_block_id=evidence.idea_block_id,
            idea_block_revision_id=evidence.idea_block_revision_id,
            quote=evidence.source_quote,
            polarity=evidence.polarity,
            valid_from=evidence.valid_from,
            valid_to=evidence.valid_to,
            governance_record_id=governance_record.id,
            governance_record_revision_id=governance_revision.id,
            governance_record_type=governance_record.record_type,
            confidence=evidence.confidence,
        )
    return Citation(
        citation_id=evidence.id,
        idea_block_id=evidence.idea_block_id,
        idea_block_revision_id=evidence.idea_block_revision_id,
        polarity=evidence.polarity,
        redacted=True,
        redaction_reason="origin_unavailable",
    )


def _citation_identity(citation: Citation) -> tuple[object, ...]:
    if citation.source_version_id:
        return (
            "source",
            citation.source_version_id,
            citation.source_element_id,
            citation.quote_char_start,
            citation.quote_char_end,
            citation.polarity,
        )
    if citation.governance_record_revision_id:
        return ("governance", citation.governance_record_revision_id, citation.polarity)
    return ("redacted", citation.citation_id)


def _citation_order(citation: Citation) -> tuple[object, ...]:
    return (
        citation.redacted,
        0 if citation.source_version_id else 1 if citation.governance_record_revision_id else 2,
        citation.source_version_id or "",
        citation.page_number if citation.page_number is not None else -1,
        citation.source_element_id or "",
        citation.quote_char_start if citation.quote_char_start is not None else -1,
        citation.quote_char_end if citation.quote_char_end is not None else -1,
        citation.governance_record_revision_id or "",
        citation.polarity or "",
        citation.citation_id,
    )


def _source_is_readable(document: SourceDocument, scope: RetrievalScope) -> bool:
    if scope.domain_ids and document.domain_id not in scope.domain_ids:
        return False
    if not scope.all_category_ids_authorized and (
        not scope.category_ids or document.category_id not in scope.category_ids
    ):
        return False
    if scope.source_document_ids and document.id not in scope.source_document_ids:
        return False
    return not scope.sensitivities or document.sensitivity in scope.sensitivities


def _source_scope_filter(scope: RetrievalScope) -> ColumnElement[bool]:
    """Build the source authorization predicate used before evidence hydration."""

    filters: list[ColumnElement[bool]] = []
    if scope.domain_ids:
        filters.append(SourceDocument.domain_id.in_(scope.domain_ids))
    if not scope.all_category_ids_authorized:
        if not scope.category_ids:
            return false()
        filters.append(SourceDocument.category_id.in_(scope.category_ids))
    if scope.sensitivities:
        filters.append(SourceDocument.sensitivity.in_(scope.sensitivities))
    if scope.source_document_ids:
        filters.append(SourceDocument.id.in_(scope.source_document_ids))
    return and_(*filters) if filters else true()


def _governance_scope_filter(scope: RetrievalScope) -> ColumnElement[bool]:
    filters: list[ColumnElement[bool]] = []
    if scope.domain_ids:
        filters.append(GovernanceRecord.domain_id.in_(scope.domain_ids))
    if scope.governance_statuses:
        filters.append(GovernanceRecord.lifecycle_status.in_(scope.governance_statuses))
    if scope.architecture_backlog_states:
        filters.extend(
            (
                GovernanceRecord.record_type == "architecture_backlog",
                GovernanceRecord.lifecycle_status.in_(scope.architecture_backlog_states),
            )
        )
    if not scope.governance_record_types:
        return false()
    return and_(*filters) if filters else true()


def _governance_is_readable(record: GovernanceRecord, scope: RetrievalScope) -> bool:
    if scope.domain_ids and record.domain_id not in scope.domain_ids:
        return False
    if record.record_type not in scope.governance_record_types:
        return False
    if scope.governance_statuses and record.lifecycle_status not in scope.governance_statuses:
        return False
    return not (
        scope.architecture_backlog_states
        and (
            record.record_type != "architecture_backlog"
            or record.lifecycle_status not in scope.architecture_backlog_states
        )
    )


def _entities_by_block(
    session: Session, block_ids: tuple[str, ...], scope: RetrievalScope
) -> dict[str, tuple[dict[str, str], ...]]:
    if not block_ids:
        return {}
    query = (
        select(IdeaBlockEntity.idea_block_id, IdeaBlockEntity.role, Entity)
        .join(Entity, Entity.id == IdeaBlockEntity.entity_id)
        .join(IdeaBlock, IdeaBlock.id == IdeaBlockEntity.idea_block_id)
        .where(IdeaBlockEntity.idea_block_id.in_(block_ids))
        .where(Entity.lifecycle_status == "active")
        .where(or_(Entity.domain_id.is_(None), Entity.domain_id == IdeaBlock.domain_id))
    )
    if scope.domain_ids:
        query = query.where(or_(Entity.domain_id.is_(None), Entity.domain_id.in_(scope.domain_ids)))
    rows = session.execute(
        query.order_by(IdeaBlockEntity.idea_block_id.asc(), Entity.preferred_label.asc(), Entity.id.asc())
    ).all()
    values: dict[str, list[dict[str, str]]] = defaultdict(list)
    for block_id, role, entity in rows:
        values[block_id].append(
            {"id": entity.id, "label": entity.preferred_label, "type": entity.ontology_type, "role": role}
        )
    return {block_id: tuple(entities) for block_id, entities in values.items()}


def _governance_by_block(
    session: Session, block_ids: tuple[str, ...], scope: RetrievalScope
) -> dict[str, tuple[dict[str, str], ...]]:
    if not block_ids or not scope.governance_record_types:
        return {}
    query = (
        select(GovernanceRecordIdeaBlock, GovernanceRecord, GovernanceRecordRevision)
        .join(IdeaBlock, IdeaBlock.id == GovernanceRecordIdeaBlock.idea_block_id)
        .join(GovernanceRecord, GovernanceRecord.id == GovernanceRecordIdeaBlock.governance_record_id)
        .join(
            GovernanceRecordRevision,
            and_(
                GovernanceRecordRevision.id == GovernanceRecord.current_revision_id,
                GovernanceRecordRevision.governance_record_id == GovernanceRecord.id,
            ),
        )
        .where(GovernanceRecordIdeaBlock.idea_block_id.in_(block_ids))
        .where(GovernanceRecord.record_type.in_(scope.governance_record_types))
        .where(GovernanceRecord.domain_id == IdeaBlock.domain_id)
        .where(IdeaBlock.merged_into_id.is_(None), IdeaBlock.tombstoned_at.is_(None), IdeaBlock.archived_at.is_(None))
    )
    if scope.domain_ids:
        query = query.where(GovernanceRecord.domain_id.in_(scope.domain_ids))
    if scope.governance_statuses:
        query = query.where(GovernanceRecord.lifecycle_status.in_(scope.governance_statuses))
    if scope.architecture_backlog_states:
        query = query.where(GovernanceRecord.record_type == "architecture_backlog").where(
            GovernanceRecord.lifecycle_status.in_(scope.architecture_backlog_states)
        )
    rows = session.execute(query.order_by(GovernanceRecordIdeaBlock.idea_block_id, GovernanceRecord.id)).all()
    values: dict[str, list[dict[str, str]]] = defaultdict(list)
    for link, record, revision in rows:
        values[link.idea_block_id].append(
            {
                "id": record.id,
                "revision_id": revision.id,
                "type": record.record_type,
                "status": record.lifecycle_status,
                "title": record.title,
                "role": link.role,
            }
        )
    return {block_id: tuple(records) for block_id, records in values.items()}


def _relations_by_block(
    session: Session, block_ids: tuple[str, ...], scope: RetrievalScope
) -> dict[str, tuple[dict[str, str], ...]]:
    if not block_ids:
        return {}
    query = (
        select(IdeaBlockRelation)
        .where(IdeaBlockRelation.status == "verified")
        .where(IdeaBlockRelation.decision_provenance != {})
        .where(_verified_idea_block_endpoint(IdeaBlockRelation.source_idea_block_id))
        .where(_verified_idea_block_endpoint(IdeaBlockRelation.target_idea_block_id))
        .where(
            or_(
                IdeaBlockRelation.source_idea_block_id.in_(block_ids),
                IdeaBlockRelation.target_idea_block_id.in_(block_ids),
            )
        )
        .order_by(IdeaBlockRelation.source_idea_block_id, IdeaBlockRelation.target_idea_block_id, IdeaBlockRelation.id)
    )
    if scope.date_from:
        query = query.where(or_(IdeaBlockRelation.valid_to.is_(None), IdeaBlockRelation.valid_to >= scope.date_from))
    if scope.date_to:
        query = query.where(
            or_(IdeaBlockRelation.valid_from.is_(None), IdeaBlockRelation.valid_from < exclusive_date_to(scope.date_to))
        )
    rows = session.scalars(query).all()
    related_block_ids = {relation.source_idea_block_id for relation in rows} | {
        relation.target_idea_block_id for relation in rows
    }
    readable_revisions = authorized_current_revisions_for_block_ids(session, block_ids=related_block_ids, scope=scope)
    readable_block_ids = set(readable_revisions)
    values: dict[str, list[dict[str, str]]] = defaultdict(list)
    for relation in rows:
        if (
            relation.source_idea_block_id not in readable_block_ids
            or relation.target_idea_block_id not in readable_block_ids
        ):
            continue
        payload = {
            "id": relation.id,
            "source_idea_block_id": relation.source_idea_block_id,
            "target_idea_block_id": relation.target_idea_block_id,
            "type": relation.relation_type,
            "status": relation.status,
        }
        values[relation.source_idea_block_id].append(payload)
        if relation.target_idea_block_id != relation.source_idea_block_id:
            values[relation.target_idea_block_id].append(payload)
    return {block_id: tuple(relations) for block_id, relations in values.items()}


def _verified_idea_block_endpoint(endpoint_column: ColumnElement[str]) -> ColumnElement[bool]:
    """Keep hydrated relations on current, verified Idea Block revisions only."""

    return (
        select(IdeaBlock.id)
        .join(IdeaBlockRevision, IdeaBlockRevision.id == IdeaBlock.current_revision_id)
        .where(IdeaBlock.id == endpoint_column)
        .where(IdeaBlockRevision.idea_block_id == IdeaBlock.id)
        .where(IdeaBlockRevision.publication_status == "verified")
        .where(IdeaBlock.merged_into_id.is_(None), IdeaBlock.tombstoned_at.is_(None), IdeaBlock.archived_at.is_(None))
        .exists()
    )
