from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import delete, false, or_, select
from sqlalchemy.orm import Session

from margin_db.entity_models import EntityMention, EntityResolutionDecision
from margin_db.entity_resolution import EntityResolver
from margin_db.evidence_mapping import validate_claim_evidence
from margin_db.extraction_cleanup import delete_element_accounting
from margin_db.extraction_relationship_persistence import _persist_relationship_occurrences
from margin_db.idea_block_resolution import resolve_exact_idea_block_candidate
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
from margin_db.source_coverage import source_element_coverage
from margin_db.source_models import (
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourceElementOutcome,
    UnmappedExtraction,
)
from margin_schema.extraction import (
    CandidateRejectionReason,
    ExtractedClaimEvidence,
    ExtractedIdeaBlock,
    ExtractorOutput,
    UnmappedExtractionCandidate,
    candidate_rejection_reason,
    primary_claim_evidence,
)
from margin_schema.text_normalization import normalize_entity_name, normalize_statement, tokenize_search

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionQualityReport:
    element_count: int
    idea_block_count: int
    entity_count: int
    relationship_count: int
    failed_element_count: int
    review_task_count: int
    covered_element_count: int = 0
    unaccounted_element_count: int = 0
    quarantined_element_count: int = 0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_count": self.element_count,
            "idea_block_count": self.idea_block_count,
            "entity_count": self.entity_count,
            "relationship_count": self.relationship_count,
            "failed_element_count": self.failed_element_count,
            "review_task_count": self.review_task_count,
            "covered_element_count": self.covered_element_count,
            "unaccounted_element_count": self.unaccounted_element_count,
            "quarantined_element_count": self.quarantined_element_count,
            "warnings": list(self.warnings),
        }


DETERMINISTIC_AUTO_VERIFY_METHODS = frozenset(
    {
        "deterministic_key_value",
        "deterministic_principle",
        "deterministic_relationship",
        "deterministic_table",
        "deterministic_contract",
        "deterministic_diagram",
    }
)


def can_auto_verify_extraction(
    *, method: str, confidence: str, evidence_chain_complete: bool, candidate: ExtractedIdeaBlock | None = None
) -> bool:
    if method == "manual":
        return (
            confidence == "extracted"
            and evidence_chain_complete
            and candidate is not None
            and candidate.metadata.get("verification_policy") == "manual_authorized"
        )
    if confidence != "extracted" or method not in DETERMINISTIC_AUTO_VERIFY_METHODS or not evidence_chain_complete:
        return False
    if candidate is None:
        return False
    policy = candidate.metadata.get("verification_policy")
    if method == "deterministic_key_value":
        return (
            candidate.type == "structured_key_value" or candidate.type.startswith("frontmatter_")
        ) and policy == "structured_key_value"
    if method == "deterministic_principle":
        return candidate.type == "architecture_principle" and policy == "validated_principle"
    if method == "deterministic_relationship":
        return candidate.type == "relationship" and policy == "validated_relationship"
    if method == "deterministic_table":
        return (
            candidate.type
            in {
                "interface_matrix_row",
                "owner",
                "responsibility_row",
                "risk_register_row",
                "structured_table_row",
                "technology_choice",
                "technology_version",
            }
            and policy == "normalized_table_assertion"
        )
    if method == "deterministic_contract":
        return (
            candidate.type.startswith(("api_", "contract_", "event_", "schema_", "security_"))
            and policy == "validated_contract"
        )
    return (
        candidate.type in {"c4_entity", "c4_relationship", "diagram_reference", "diagram_source"}
        and policy == "validated_diagram"
        and candidate.metadata.get("diagram_validated") is True
    )


def purge_deleted_source_extractions(session: Session, *, old_version_ids: list[str]) -> list[str]:
    if not old_version_ids:
        return []

    old_element_ids = list(
        session.scalars(select(SourceElement.id).where(SourceElement.source_version_id.in_(old_version_ids))).all()
    )
    old_idea_block_ids = _idea_block_ids_for_source_index(session, old_element_ids=old_element_ids)
    old_relationship_ids = _relationship_ids_for_source_index(
        session, old_idea_block_ids=old_idea_block_ids, old_element_ids=old_element_ids
    )
    old_entity_ids = _entity_ids_for_idea_blocks(session, old_idea_block_ids)
    old_review_task_ids = _review_task_ids_for_deleted_extractions(
        session,
        old_idea_block_ids=old_idea_block_ids,
        old_relationship_ids=old_relationship_ids,
        old_element_ids=old_element_ids,
    )
    delete_element_accounting(session, source_version_ids=old_version_ids, source_element_ids=old_element_ids)

    _delete_embedding_targets(session, idea_block_ids=old_idea_block_ids, source_element_ids=old_element_ids)
    _delete_review_tasks(session, old_review_task_ids)
    if old_relationship_ids or old_idea_block_ids or old_element_ids:
        _delete_relationship_provenance(
            session,
            old_relationship_ids=old_relationship_ids,
            old_idea_block_ids=old_idea_block_ids,
            old_element_ids=old_element_ids,
        )
    if old_relationship_ids:
        session.execute(delete(EntityRelationship).where(EntityRelationship.id.in_(old_relationship_ids)))
    if old_element_ids or old_idea_block_ids:
        mention_ids = list(
            session.scalars(
                select(EntityMention.id).where(
                    or_(
                        EntityMention.source_element_id.in_(old_element_ids),
                        EntityMention.idea_block_revision_id.in_(
                            select(IdeaBlockRevision.id).where(IdeaBlockRevision.idea_block_id.in_(old_idea_block_ids))
                        ),
                    )
                )
            ).all()
        )
        if mention_ids:
            session.execute(
                delete(EntityResolutionDecision).where(EntityResolutionDecision.mention_id.in_(mention_ids))
            )
            session.execute(delete(EntityMention).where(EntityMention.id.in_(mention_ids)))
    if old_idea_block_ids:
        _delete_entity_mentions_for_idea_blocks(session, old_idea_block_ids)
        session.execute(delete(ClaimEvidence).where(ClaimEvidence.idea_block_id.in_(old_idea_block_ids)))
        session.execute(delete(IdeaBlockEntity).where(IdeaBlockEntity.idea_block_id.in_(old_idea_block_ids)))
        session.execute(delete(IdeaBlock).where(IdeaBlock.id.in_(old_idea_block_ids)))
    session.flush()
    return old_entity_ids


def _delete_entity_mentions_for_idea_blocks(session: Session, idea_block_ids: list[str]) -> None:
    if not idea_block_ids:
        return
    mention_ids = list(
        session.scalars(
            select(EntityMention.id)
            .join(IdeaBlockRevision, IdeaBlockRevision.id == EntityMention.idea_block_revision_id)
            .where(IdeaBlockRevision.idea_block_id.in_(idea_block_ids))
        ).all()
    )
    if not mention_ids:
        return
    session.execute(delete(EntityResolutionDecision).where(EntityResolutionDecision.mention_id.in_(mention_ids)))
    session.execute(delete(EntityMention).where(EntityMention.id.in_(mention_ids)))


def _delete_embedding_targets(session: Session, *, idea_block_ids: list[str], source_element_ids: list[str]) -> None:
    record_filters = []
    job_filters = []
    if idea_block_ids:
        revision_ids = list(
            session.scalars(
                select(IdeaBlockRevision.id).where(IdeaBlockRevision.idea_block_id.in_(idea_block_ids))
            ).all()
        )
    else:
        revision_ids = []
    if revision_ids:
        record_filters.append(
            (EmbeddingRecord.target_type == "idea_block_revision") & EmbeddingRecord.target_id.in_(revision_ids)
        )
        job_filters.append(
            (EmbeddingJob.target_type == "idea_block_revision") & EmbeddingJob.target_id.in_(revision_ids)
        )
    if source_element_ids:
        record_filters.append(
            (EmbeddingRecord.target_type == "source_element") & EmbeddingRecord.target_id.in_(source_element_ids)
        )
        job_filters.append(
            (EmbeddingJob.target_type == "source_element") & EmbeddingJob.target_id.in_(source_element_ids)
        )
    if record_filters:
        session.execute(delete(EmbeddingRecord).where(or_(*record_filters)))
    if job_filters:
        session.execute(delete(EmbeddingJob).where(or_(*job_filters)))


def _idea_block_ids_for_source_index(session: Session, *, old_element_ids: list[str]) -> list[str]:
    if not old_element_ids:
        return []
    return list(
        session.scalars(
            select(ClaimEvidence.idea_block_id).where(ClaimEvidence.source_element_id.in_(old_element_ids)).distinct()
        ).all()
    )


def _entity_ids_for_idea_blocks(session: Session, idea_block_ids: list[str]) -> list[str]:
    if not idea_block_ids:
        return []
    return sorted(
        set(session.scalars(select(IdeaBlockEntity.entity_id).where(IdeaBlockEntity.idea_block_id.in_(idea_block_ids))))
    )


def _relationship_ids_for_source_index(
    session: Session, *, old_idea_block_ids: list[str], old_element_ids: list[str]
) -> list[str]:
    relationship_ids: set[str] = set()
    if old_idea_block_ids:
        relationship_ids.update(
            session.scalars(
                select(RelationshipProvenance.relationship_id).where(
                    RelationshipProvenance.idea_block_id.in_(old_idea_block_ids)
                )
            ).all()
        )
    if old_element_ids:
        relationship_ids.update(
            session.scalars(
                select(RelationshipProvenance.relationship_id).where(
                    RelationshipProvenance.source_element_id.in_(old_element_ids)
                )
            ).all()
        )
    return sorted(relationship_ids)


def _review_task_ids_for_deleted_extractions(
    session: Session, *, old_idea_block_ids: list[str], old_relationship_ids: list[str], old_element_ids: list[str]
) -> list[str]:
    filters = []
    if old_element_ids:
        filters.append((ReviewTask.target_type == "source_element") & ReviewTask.target_id.in_(old_element_ids))
    if old_idea_block_ids:
        filters.append((ReviewTask.target_type == "idea_block") & ReviewTask.target_id.in_(old_idea_block_ids))
    if old_relationship_ids:
        filters.append(
            (ReviewTask.target_type == "entity_relationship") & ReviewTask.target_id.in_(old_relationship_ids)
        )
    if not filters:
        return []
    return list(session.scalars(select(ReviewTask.id).where(or_(*filters))).all())


def _delete_review_tasks(session: Session, review_task_ids: list[str]) -> None:
    if not review_task_ids:
        return
    session.execute(delete(ReviewTaskComment).where(ReviewTaskComment.review_task_id.in_(review_task_ids)))
    session.execute(delete(ReviewTask).where(ReviewTask.id.in_(review_task_ids)))


def _delete_relationship_provenance(
    session: Session, *, old_relationship_ids: list[str], old_idea_block_ids: list[str], old_element_ids: list[str]
) -> None:
    filters = []
    if old_relationship_ids:
        filters.append(RelationshipProvenance.relationship_id.in_(old_relationship_ids))
    if old_idea_block_ids:
        filters.append(RelationshipProvenance.idea_block_id.in_(old_idea_block_ids))
    if old_element_ids:
        filters.append(RelationshipProvenance.source_element_id.in_(old_element_ids))
    if filters:
        session.execute(delete(RelationshipProvenance).where(or_(*filters)))


def _elements_for_source(session: Session, source_document_id: str | None = None) -> list[SourceElement]:
    statement = (
        select(SourceElement)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .order_by(SourceElement.source_version_id, SourceElement.ordinal, SourceElement.id)
    )
    if source_document_id is not None:
        statement = statement.where(SourceDocumentVersion.source_document_id == source_document_id)
    return list(session.scalars(statement).all())


class ExtractionPersistenceService:
    def __init__(self, resolver: EntityResolver | None = None) -> None:
        self.resolver = resolver or EntityResolver()

    def persist_output(
        self,
        session: Session,
        output: ExtractorOutput | dict[str, Any],
        *,
        source_document_id: str | None = None,
        force_candidate: bool = False,
        extraction_run_id: str | None = None,
        model_id: str | None = None,
        prompt_version: str | None = None,
        candidate_set_ids_by_block_key: dict[tuple[str, str], tuple[str, ...]] | None = None,
    ) -> ExtractionQualityReport:
        start = datetime.now(UTC)
        validated = output if isinstance(output, ExtractorOutput) else ExtractorOutput(**output)
        request_payload = {
            "source_document_id": source_document_id,
            "candidate_count": len(validated.idea_blocks),
            "force_candidate": force_candidate,
        }
        elements = _elements_for_source(session, source_document_id)
        elements_by_id = {element.id: element for element in elements}

        entity_ids: set[str] = set()
        relationship_ids: set[str] = set()
        changed_idea_block_revision_ids: list[str] = []
        accepted_idea_blocks = 0
        review_count = 0
        failed_elements: set[str] = set()
        warnings: list[str] = []
        candidate_sets = candidate_set_ids_by_block_key or {}

        for candidate in validated.idea_blocks:
            rejection_reason = candidate_rejection_reason(candidate)
            if rejection_reason is None:
                rejection_reason = _candidate_semantic_rejection_reason(candidate)
            if rejection_reason is not None:
                failed_element_id = primary_claim_evidence(candidate).source_element_id
                failure = f"candidate rejected: {rejection_reason.value}"
                failed_elements.add(failed_element_id)
                warnings.append(failure)
                review_count += _quarantine_candidate(
                    session,
                    candidate=candidate,
                    reason=rejection_reason,
                    elements_by_id=elements_by_id,
                    source_document_id=source_document_id,
                    extraction_run_id=extraction_run_id,
                    model_id=model_id,
                    prompt_version=prompt_version,
                )
                continue
            invalid_entity_names = [
                entity.name for entity in candidate.entities if not normalize_entity_name(entity.name)
            ]
            if invalid_entity_names:
                failed_element_id = primary_claim_evidence(candidate).source_element_id
                reason = CandidateRejectionReason.INVALID_SEMANTIC_FIELDS
                reason_text = "candidate rejected: invalid_semantic_fields"
                failed_elements.add(failed_element_id)
                warnings.append(reason_text)
                review_count += _quarantine_candidate(
                    session,
                    candidate=candidate,
                    reason=reason,
                    elements_by_id=elements_by_id,
                    source_document_id=source_document_id,
                    extraction_run_id=extraction_run_id,
                    model_id=model_id,
                    prompt_version=prompt_version,
                )
                continue
            resolved_evidence: list[tuple[ExtractedClaimEvidence, SourceElement]] = []
            evidence_error: str | None = None
            for evidence in candidate.evidence:
                element = elements_by_id.get(evidence.source_element_id)
                if element is None:
                    evidence_error = f"missing source element for {evidence.source_element_id}"
                    break
                try:
                    validate_claim_evidence(evidence, element)
                except ValueError as exc:
                    evidence_error = str(exc)
                    break
                resolved_evidence.append((evidence, element))
            if evidence_error is not None:
                failed_element_id = primary_claim_evidence(candidate).source_element_id
                failed_elements.add(failed_element_id)
                warnings.append(evidence_error)
                review_count += _quarantine_candidate(
                    session,
                    candidate=candidate,
                    reason=CandidateRejectionReason.INVALID_EVIDENCE,
                    elements_by_id=elements_by_id,
                    source_document_id=source_document_id,
                    extraction_run_id=extraction_run_id,
                    model_id=model_id,
                    prompt_version=prompt_version,
                )
                continue
            _, element = resolved_evidence[0]
            candidate_set_ids = candidate_sets.get((element.id, normalize_statement(candidate.statement)), ())
            version = session.get(SourceDocumentVersion, element.source_version_id)
            if version is None:
                raise ValueError(f"source version not found for element {element.id}")
            source = session.get(SourceDocument, version.source_document_id)
            if source is None:
                raise ValueError(f"source document not found: {version.source_document_id}")
            resolved_entities: list[tuple[Any, Any]] = []
            for entity in candidate.entities:
                resolved_entities.append(
                    (
                        entity,
                        self.resolver.resolve_existing(
                            session,
                            text=entity.name,
                            ontology_type=entity.kind,
                            domain_id=source.domain_id,
                            external_id=entity.external_id,
                            canonical_id=entity.canonical_id,
                            supplied_candidate_ids=candidate_set_ids,
                        ),
                    )
                )
            resolved_entity_ids = tuple(sorted({resolved.id for _entity, resolved in resolved_entities if resolved.id}))
            entities_resolved = all(not resolved.needs_review for _entity, resolved in resolved_entities)
            evidence_chain_complete = _candidate_has_complete_evidence(candidate)
            auto_verified = (
                not force_candidate
                and entities_resolved
                and can_auto_verify_extraction(
                    method=candidate.extraction_method,
                    confidence=candidate.confidence,
                    evidence_chain_complete=evidence_chain_complete,
                    candidate=candidate,
                )
            )
            candidate_metadata = {
                **candidate.metadata,
                "category": source.category,
                "tags": list(candidate.tags),
                "candidate_entity_ids": list(candidate_set_ids),
            }
            if candidate.entities:
                candidate_metadata["entities"] = [entity.name for entity in candidate.entities]
            resolution = resolve_exact_idea_block_candidate(
                session,
                candidate,
                domain_id=source.domain_id,
                resolved_entity_ids=resolved_entity_ids,
                entities_resolved=entities_resolved,
                status="verified" if auto_verified else "candidate",
                actor="extraction",
                process=candidate.extraction_method,
                extraction_run_id=extraction_run_id,
                metadata=candidate_metadata,
            )
            idea_block = resolution.block
            idea_block_revision = resolution.revision
            idea_block_changed = not resolution.attached_existing
            review_count += int(resolution.review_task_created)
            if idea_block_changed:
                changed_idea_block_revision_ids.append(idea_block_revision.id)
            accepted_idea_blocks += 1
            if not auto_verified:
                review_count += _create_review_task(
                    session,
                    task_type="idea_block_review",
                    target_type="idea_block",
                    target_id=idea_block.id,
                    reason=_review_reason(candidate),
                )
            for unmapped in candidate.unmapped:
                review_count += _persist_unmapped_extraction(
                    session,
                    candidate=unmapped,
                    source_document_id=source.id,
                    source_version_id=element.source_version_id,
                    source_element_id=element.id,
                    extraction_run_id=extraction_run_id,
                    model_id=model_id,
                    prompt_version=prompt_version,
                )

            entity_by_name: dict[str, str] = {}
            entity_resolution_review: dict[str, bool] = {}
            for entity, initial_resolution in resolved_entities:
                resolved = self.resolver.resolve_mention(
                    session,
                    text=entity.name,
                    ontology_type=entity.kind,
                    domain_id=source.domain_id,
                    external_id=entity.external_id,
                    source_element_id=element.id,
                    idea_block_revision_id=idea_block_revision.id,
                    context={
                        "source_document_id": source.id,
                        "candidate_type": candidate.type,
                        "initial_match": initial_resolution.matched_by,
                    },
                    supplied_candidate_ids=candidate_set_ids,
                    canonical_id=entity.canonical_id,
                )
                normalized_entity_name = normalize_entity_name(entity.name)
                entity_resolution_review[normalized_entity_name] = resolved.needs_review
                if resolved.needs_review:
                    review_count += 1
                    continue
                if resolved.id is None:
                    raise ValueError("resolved entity must have a canonical ID")
                entity_ids.add(resolved.id)
                entity_by_name[normalized_entity_name] = resolved.id
                _link_idea_block_entity(session, idea_block.id, resolved.id, entity.role)

            for relationship in candidate.relationships:
                source_name = normalize_entity_name(relationship.source)
                target_name = normalize_entity_name(relationship.target)
                if source_name not in entity_by_name or target_name not in entity_by_name:
                    review_count += _create_review_task(
                        session,
                        task_type="relationship_review",
                        target_type="idea_block",
                        target_id=idea_block.id,
                        reason="relationship has an unresolved canonical entity mention",
                    )
                    continue
                rel_ids, rel_reviews = _persist_relationship_occurrences(
                    session,
                    candidate=candidate,
                    relationship=relationship,
                    idea_block_id=idea_block.id,
                    entity_by_name=entity_by_name,
                    entity_resolution_review=entity_resolution_review,
                    resolved_evidence=resolved_evidence,
                    auto_verified=auto_verified,
                )
                relationship_ids.update(rel_ids)
                review_count += rel_reviews

        session.flush()
        covered_element_ids, unaccounted_element_ids = source_element_coverage(
            session, source_document_id=source_document_id
        )
        quarantined_element_count = _quarantined_element_count(session, source_document_id)
        report = ExtractionQualityReport(
            element_count=len(elements),
            idea_block_count=accepted_idea_blocks,
            entity_count=len(entity_ids),
            relationship_count=len(relationship_ids),
            failed_element_count=len(failed_elements),
            review_task_count=review_count,
            covered_element_count=len(covered_element_ids),
            unaccounted_element_count=len(unaccounted_element_ids),
            quarantined_element_count=quarantined_element_count,
            warnings=tuple(warnings),
        )
        _log_extraction_persistence_interaction(
            event="persist_output",
            request_payload={**request_payload, "element_count": len(elements)},
            response_payload={
                "report": report.to_dict(),
                "changed_idea_block_count": len(changed_idea_block_revision_ids),
            },
            start=start,
        )
        return report


def _log_extraction_persistence_interaction(
    *,
    event: str,
    request_payload: dict[str, object],
    response_payload: dict[str, object] | None,
    start: datetime,
    error: str | None = None,
) -> None:
    end = datetime.now(UTC)
    payload = {
        "event": event,
        "request": request_payload,
        "response": response_payload,
        "error": error,
        "start_timestamp": start.isoformat(),
        "end_timestamp": end.isoformat(),
        "duration_ms": round((end - start).total_seconds() * 1000, 3),
    }
    logger.info(
        "extraction_persistence_interaction %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        extra={"extraction_persistence_interaction": payload},
    )


def extraction_quality_report(session: Session, *, source_document_id: str | None = None) -> ExtractionQualityReport:
    elements = _elements_for_source(session, source_document_id)
    element_ids = {element.id for element in elements}

    idea_blocks = list(
        session.scalars(
            select(IdeaBlock)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .where(ClaimEvidence.source_element_id.in_(element_ids))
            .distinct()
            if element_ids
            else select(IdeaBlock).where(false())
        ).all()
    )
    idea_block_ids = {block.id for block in idea_blocks}
    relationship_ids = (
        set(
            session.scalars(
                select(RelationshipProvenance.relationship_id).where(
                    RelationshipProvenance.idea_block_id.in_(idea_block_ids)
                )
            ).all()
        )
        if idea_block_ids
        else set()
    )
    relationships = list(
        session.scalars(select(EntityRelationship).where(EntityRelationship.id.in_(relationship_ids))).all()
    )
    entity_ids = set(
        session.scalars(
            select(IdeaBlockEntity.entity_id).where(IdeaBlockEntity.idea_block_id.in_(idea_block_ids))
            if idea_block_ids
            else select(IdeaBlockEntity.entity_id).where(false())
        ).all()
    )
    extraction_candidate_tasks = list(
        session.scalars(
            select(ReviewTask).where(ReviewTask.target_type == "source_element", ReviewTask.target_id.in_(element_ids))
            if element_ids
            else select(ReviewTask).where(false())
        ).all()
    )
    unmapped_rows = list(
        session.scalars(
            select(UnmappedExtraction)
            .join(SourceDocumentVersion, SourceDocumentVersion.id == UnmappedExtraction.source_version_id)
            .where(UnmappedExtraction.status == "open", SourceDocumentVersion.source_document_id == source_document_id)
            if source_document_id is not None
            else select(UnmappedExtraction).where(UnmappedExtraction.status == "open")
        ).all()
    )
    unmapped_ids = {row.id for row in unmapped_rows}
    review_tasks = list(
        session.scalars(
            select(ReviewTask).where(
                ReviewTask.target_id.in_([*idea_block_ids, *[rel.id for rel in relationships], *unmapped_ids])
            )
            if idea_block_ids or relationships or unmapped_ids
            else select(ReviewTask).where(false())
        ).all()
    )
    failed_elements = {task.target_id for task in extraction_candidate_tasks if task.target_id}
    failed_elements.update(row.source_element_id for row in unmapped_rows)
    covered_element_ids, unaccounted_element_ids = source_element_coverage(
        session, source_document_id=source_document_id
    )
    quarantined_element_count = _quarantined_element_count(session, source_document_id)
    return ExtractionQualityReport(
        element_count=len(elements),
        idea_block_count=len(idea_blocks),
        entity_count=len(entity_ids),
        relationship_count=len(relationships),
        failed_element_count=len(failed_elements),
        review_task_count=len(review_tasks) + len(extraction_candidate_tasks),
        covered_element_count=len(covered_element_ids),
        unaccounted_element_count=len(unaccounted_element_ids),
        quarantined_element_count=quarantined_element_count,
        warnings=(),
    )


def _quarantined_element_count(session: Session, source_document_id: str | None) -> int:
    outcome_statement = (
        select(SourceElementOutcome.source_element_id)
        .join(SourceElement, SourceElement.id == SourceElementOutcome.source_element_id)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .where(SourceElementOutcome.outcome == "quarantined", SourceDocumentVersion.status == "active")
    )
    if source_document_id is not None:
        outcome_statement = outcome_statement.where(SourceDocumentVersion.source_document_id == source_document_id)
    quarantined_ids = set(session.scalars(outcome_statement).all())

    unmapped_statement = (
        select(UnmappedExtraction.source_element_id)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == UnmappedExtraction.source_version_id)
        .where(UnmappedExtraction.status == "open", SourceDocumentVersion.status == "active")
    )
    if source_document_id is not None:
        unmapped_statement = unmapped_statement.where(SourceDocumentVersion.source_document_id == source_document_id)
    quarantined_ids.update(session.scalars(unmapped_statement).all())
    return len(quarantined_ids)


def _persist_unmapped_extraction(
    session: Session,
    *,
    candidate: UnmappedExtractionCandidate,
    source_document_id: str,
    source_version_id: str,
    source_element_id: str,
    extraction_run_id: str | None,
    model_id: str | None,
    prompt_version: str | None,
) -> int:
    payload = {
        "extraction_run_id": extraction_run_id,
        "source_version_id": source_version_id,
        "source_element_id": source_element_id,
        "kind": candidate.kind,
        "reason": candidate.reason.value,
        "raw_payload": candidate.raw_payload,
    }
    unmapped_id = "unmapped_" + sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    row = session.get(UnmappedExtraction, unmapped_id)
    if row is None:
        row = UnmappedExtraction(
            id=unmapped_id,
            source_document_id=source_document_id,
            source_version_id=source_version_id,
            source_element_id=source_element_id,
            extraction_run_id=extraction_run_id,
            kind=candidate.kind,
            reason=candidate.reason.value,
            raw_payload=candidate.raw_payload,
            model_id=model_id,
            prompt_version=prompt_version,
            status="open",
            resolution={},
        )
        session.add(row)
    created = _create_review_task(
        session,
        task_type="unmapped_extraction_review",
        target_type="unmapped_extraction",
        target_id=unmapped_id,
        reason=candidate.reason.value,
        evidence=[candidate.model_dump(mode="json")],
    )
    session.flush()
    task = session.scalar(
        select(ReviewTask).where(
            ReviewTask.task_type == "unmapped_extraction_review",
            ReviewTask.target_type == "unmapped_extraction",
            ReviewTask.target_id == unmapped_id,
            ReviewTask.status == "open",
        )
    )
    row.review_task_id = task.id if task is not None else None
    return created


def _quarantine_candidate(
    session: Session,
    *,
    candidate: ExtractedIdeaBlock,
    reason: CandidateRejectionReason,
    elements_by_id: dict[str, SourceElement],
    source_document_id: str | None,
    extraction_run_id: str | None,
    model_id: str | None,
    prompt_version: str | None,
) -> int:
    primary_element_id = primary_claim_evidence(candidate).source_element_id
    element = elements_by_id.get(primary_element_id)
    if element is not None:
        version = session.get(SourceDocumentVersion, element.source_version_id)
        document_id = source_document_id
        if version is not None:
            document_id = version.source_document_id
        if document_id is not None:
            unmapped = UnmappedExtractionCandidate(
                kind="claim", reason=reason, raw_payload=candidate.model_dump(mode="json")
            )
            return _persist_unmapped_extraction(
                session,
                candidate=unmapped,
                source_document_id=document_id,
                source_version_id=element.source_version_id,
                source_element_id=element.id,
                extraction_run_id=extraction_run_id,
                model_id=model_id,
                prompt_version=prompt_version,
            )
    return _create_review_task(
        session,
        task_type="extraction_candidate_review",
        target_type="source_element",
        target_id=primary_element_id,
        reason=reason.value,
        evidence=[candidate.model_dump(mode="json")],
    )


def _link_idea_block_entity(session: Session, idea_block_id_value: str, entity_id_value: str, role: str) -> None:
    existing = session.get(
        IdeaBlockEntity, {"idea_block_id": idea_block_id_value, "entity_id": entity_id_value, "role": role}
    )
    if existing is None:
        session.add(IdeaBlockEntity(idea_block_id=idea_block_id_value, entity_id=entity_id_value, role=role))
        from margin_db.search_materialization import enqueue_search_materialization_for_entities

        enqueue_search_materialization_for_entities(session, (entity_id_value,), reason="approved_entity_link_changed")


def _create_review_task(
    session: Session,
    *,
    task_type: str,
    target_type: str,
    target_id: str,
    reason: str,
    evidence: list[dict[str, object]] | None = None,
) -> int:
    existing = session.scalar(
        select(ReviewTask).where(
            ReviewTask.task_type == task_type,
            ReviewTask.target_type == target_type,
            ReviewTask.target_id == target_id,
            ReviewTask.status == "open",
        )
    )
    if existing is not None:
        if evidence:
            existing.evidence = evidence
        return 0
    session.add(
        ReviewTask(
            task_type=task_type, target_type=target_type, target_id=target_id, reason=reason, evidence=evidence or []
        )
    )
    return 1


def _candidate_has_complete_evidence(candidate: ExtractedIdeaBlock) -> bool:
    return bool(candidate.evidence) and all(
        evidence.source_element_id and evidence.source_quote for evidence in candidate.evidence
    )


def _candidate_semantic_rejection_reason(candidate: ExtractedIdeaBlock) -> CandidateRejectionReason | None:
    evidence_text = " ".join(evidence.source_quote for evidence in candidate.evidence).casefold()
    statement_text = candidate.statement.casefold()
    normalized_statement = " ".join(tokenize_search(statement_text))
    normalized_evidence = " ".join(tokenize_search(evidence_text))
    for entity in candidate.entities:
        normalized_name = normalize_entity_name(entity.name)
        if not normalized_name:
            return CandidateRejectionReason.INVALID_SEMANTIC_FIELDS
        if (
            normalized_name.casefold() not in statement_text
            and normalized_name not in normalized_statement
            and normalized_name not in normalized_evidence
        ):
            return CandidateRejectionReason.REFERENCES_UNMAPPED_ENTITY
    statement_numbers = set(re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?%?", _asserted_text(candidate)))
    evidence_numbers = set(re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?%?", evidence_text))
    if not statement_numbers.issubset(evidence_numbers):
        return CandidateRejectionReason.UNSUPPORTED_INFERENCE
    return None


def _asserted_text(candidate: ExtractedIdeaBlock) -> str:
    """Return the part of a statement that its evidence has to support.

    A principle record cites its own title, and the title is a reference to the
    source rather than an assertion about it. A numbered title such as
    "ADR-0001" therefore does not make the statement unsupported by the quote
    beneath it.
    """

    statement = candidate.statement
    title = str(candidate.metadata.get("principle_title", "")).strip()
    if title and statement.casefold().startswith(title.casefold()):
        return statement[len(title) :]
    return statement


def _review_reason(candidate: ExtractedIdeaBlock) -> str:
    if not _candidate_has_complete_evidence(candidate):
        return "IdeaBlock has incomplete source evidence and requires human review"
    if candidate.extraction_method not in DETERMINISTIC_AUTO_VERIFY_METHODS:
        return f"{candidate.extraction_method} IdeaBlock requires human review"
    return f"{candidate.confidence} IdeaBlock requires human review"
