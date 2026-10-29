"""Health findings for persisted explicit relationships only."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.entity_models import Entity
from margin_db.governance_models import (
    GovernanceRecord,
    GovernanceRecordEntity,
    GovernanceRecordIdeaBlock,
    GovernanceRecordRelation,
)
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRelation, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.relationship_queries import relationship_provenance_scope
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_schema.findings import HygieneFinding
from margin_schema.ontology import GOVERNANCE_RELATIONSHIP_REGISTRY, RELATIONSHIP_REGISTRY, RelationPersistence


def run_relationship_consistency_lint(session: Session, domain_id: str | None = None) -> list[HygieneFinding]:
    """Report corruption that cannot be repaired by relationship maintenance."""

    entities = {entity.id: entity for entity in session.scalars(select(Entity)).all()}
    blocks = {block.id: block for block in session.scalars(select(IdeaBlock)).all()}
    records = {record.id: record for record in session.scalars(select(GovernanceRecord)).all()}
    relationships = [
        relationship
        for relationship in session.scalars(select(EntityRelationship)).all()
        if _entity_relationship_in_domain(relationship, entities, domain_id)
    ]
    relationship_ids = {relationship.id for relationship in relationships}
    findings = _entity_relationship_findings(relationships, entities)
    findings.extend(_relationship_provenance_findings(session, relationship_ids, blocks))
    findings.extend(_idea_block_entity_findings(session, blocks, entities, domain_id))
    findings.extend(_idea_block_relation_findings(session, blocks, domain_id))
    findings.extend(_governance_link_findings(session, entities, blocks, records, domain_id))
    return findings


def _entity_relationship_findings(
    relationships: list[EntityRelationship], entities: dict[str, Entity]
) -> list[HygieneFinding]:
    findings: list[HygieneFinding] = []
    for relationship in relationships:
        if relationship.status not in {"candidate", "verified"}:
            continue
        definition = RELATIONSHIP_REGISTRY.get(relationship.relation_type)
        if definition is None or definition.persistence_class != RelationPersistence.ENTITY:
            findings.append(
                _finding(
                    "relationship_invalid_ontology_type",
                    relationship.id,
                    "Entity relationship type "
                    f"{relationship.relation_type!r} is not registered for Entity relationships.",
                )
            )
            continue
        source = entities.get(relationship.source_entity_id)
        target = entities.get(relationship.target_entity_id)
        if (
            source is None
            or target is None
            or source.lifecycle_status != "active"
            or target.lifecycle_status != "active"
        ):
            findings.append(
                _finding(
                    "relationship_dangling_entity",
                    relationship.id,
                    "Entity relationship has a missing or inactive endpoint.",
                )
            )
        elif source.domain_id != target.domain_id:
            findings.append(
                _finding(
                    "relationship_cross_domain",
                    relationship.id,
                    "Entity relationship endpoints must belong to the same domain.",
                )
            )
        if (
            source is not None
            and target is not None
            and (
                source.ontology_type not in definition.source_kinds
                or target.ontology_type not in definition.target_kinds
            )
        ):
            findings.append(
                _finding(
                    "relationship_invalid_ontology_direction",
                    relationship.id,
                    f"{relationship.relation_type!r} cannot connect {source.ontology_type} to {target.ontology_type}.",
                )
            )
        if relationship.valid_from and relationship.valid_to and relationship.valid_to < relationship.valid_from:
            findings.append(
                _finding(
                    "relationship_temporal_inconsistency", relationship.id, "Relationship validity range is inverted."
                )
            )
        if relationship.status == "verified" and (relationship.metadata_ or {}).get("has_contradiction") is True:
            findings.append(
                _finding(
                    "relationship_active_contradiction",
                    relationship.id,
                    "Verified relationship is marked as contradicted and requires review.",
                )
            )
    return findings


def _relationship_provenance_findings(
    session: Session, relationship_ids: set[str], blocks: dict[str, IdeaBlock]
) -> list[HygieneFinding]:
    if not relationship_ids:
        return []
    findings: list[HygieneFinding] = []
    provenance_relationship_ids, provenance_sources = relationship_provenance_scope(session, relationship_ids)
    relationships = {
        relationship.id: relationship
        for relationship in session.scalars(
            select(EntityRelationship).where(EntityRelationship.id.in_(provenance_relationship_ids))
        ).all()
    }
    active_provenance: dict[str, list[RelationshipProvenance]] = defaultdict(list)
    provenance_rows = session.scalars(
        select(RelationshipProvenance)
        .where(RelationshipProvenance.relationship_id.in_(provenance_relationship_ids))
        .where(RelationshipProvenance.status.in_(("candidate", "verified")))
    ).all()
    element_ids = {row.source_element_id for row in provenance_rows}
    elements = {
        element.id: element
        for element in session.scalars(select(SourceElement).where(SourceElement.id.in_(element_ids))).all()
    }
    version_ids = {element.source_version_id for element in elements.values()}
    versions = {
        version.id: version
        for version in session.scalars(
            select(SourceDocumentVersion).where(SourceDocumentVersion.id.in_(version_ids))
        ).all()
    }
    document_ids = {version.source_document_id for version in versions.values()}
    documents = {
        document.id: document
        for document in session.scalars(select(SourceDocument).where(SourceDocument.id.in_(document_ids))).all()
    }
    current_revisions = _current_idea_block_revisions(session, blocks)
    claim_evidence = defaultdict(list)
    for evidence in session.scalars(select(ClaimEvidence).where(ClaimEvidence.status == "active")).all():
        block = blocks.get(evidence.idea_block_id)
        current_revision = current_revisions.get(block.current_revision_id) if block is not None else None
        if (
            block is None
            or current_revision is None
            or current_revision.idea_block_id != block.id
            or evidence.idea_block_revision_id != current_revision.id
        ):
            continue
        if current_revision.publication_status not in {"candidate", "verified"}:
            continue
        claim_evidence[(evidence.idea_block_id, evidence.source_element_id, evidence.polarity)].append(evidence)

    for provenance in provenance_rows:
        active_provenance[provenance.relationship_id].append(provenance)
        missing = []
        if provenance.relationship_id not in relationships:
            missing.append("relationship")
        block = blocks.get(provenance.idea_block_id)
        current_revision = current_revisions.get(block.current_revision_id) if block is not None else None
        if block is None or current_revision is None or current_revision.idea_block_id != block.id:
            missing.append("IdeaBlock")
        elif block.merged_into_id is not None or block.tombstoned_at is not None or block.archived_at is not None:
            missing.append("active IdeaBlock")
        elif provenance.status == "verified" and current_revision.publication_status != "verified":
            missing.append("verified IdeaBlock revision")
        element = elements.get(provenance.source_element_id)
        version = versions.get(element.source_version_id) if element else None
        if element is None or version is None or version.status != "active":
            missing.append("source evidence")
        document = documents.get(version.source_document_id) if version is not None else None
        if block is not None and document is None:
            missing.append("source document")
        elif block is not None:
            assert document is not None
            if document.domain_id != block.domain_id:
                missing.append("source domain")
        matches = claim_evidence.get((provenance.idea_block_id, provenance.source_element_id, provenance.polarity), [])
        if not matches or not any(
            evidence.source_quote.strip() == (provenance.source_quote or "").strip() for evidence in matches
        ):
            missing.append("exact ClaimEvidence")
        if missing:
            findings.append(
                _finding(
                    "relationship_missing_provenance",
                    provenance.id,
                    "Relationship provenance is missing " + ", ".join(missing) + ".",
                    target_type="relationship_provenance",
                )
            )
        if provenance.valid_from and provenance.valid_to and provenance.valid_to < provenance.valid_from:
            findings.append(
                _finding(
                    "relationship_temporal_inconsistency",
                    provenance.id,
                    "Relationship provenance validity range is inverted.",
                    target_type="relationship_provenance",
                )
            )
    for derived_id, original_id in provenance_sources.items():
        active_provenance[derived_id].extend(active_provenance.get(original_id, ()))
    for relationship in relationships.values():
        if relationship.status != "verified":
            continue
        relationship_provenance = active_provenance.get(relationship.id, [])
        if not relationship_provenance:
            message = "Verified relationship has no active provenance citation."
        elif not any(item.status == "verified" for item in relationship_provenance):
            message = "Verified relationship has no verified active provenance citation."
        else:
            continue
        findings.append(_finding("relationship_missing_provenance", relationship.id, message))
    return findings


def _current_idea_block_revisions(session: Session, blocks: dict[str, IdeaBlock]) -> dict[str, IdeaBlockRevision]:
    revision_ids = {block.current_revision_id for block in blocks.values() if block.current_revision_id is not None}
    if not revision_ids:
        return {}
    return {
        revision.id: revision
        for revision in session.scalars(
            select(IdeaBlockRevision)
            .join(
                IdeaBlock,
                (IdeaBlock.current_revision_id == IdeaBlockRevision.id)
                & (IdeaBlock.id == IdeaBlockRevision.idea_block_id),
            )
            .where(IdeaBlockRevision.id.in_(revision_ids))
        ).all()
    }


def _idea_block_entity_findings(
    session: Session, blocks: dict[str, IdeaBlock], entities: dict[str, Entity], domain_id: str | None
) -> list[HygieneFinding]:
    findings: list[HygieneFinding] = []
    current_revisions = _current_idea_block_revisions(session, blocks)
    for link in session.scalars(select(IdeaBlockEntity)).all():
        block = blocks.get(link.idea_block_id)
        entity = entities.get(link.entity_id)
        if domain_id is not None and block is not None and block.domain_id != domain_id:
            continue
        target_id = f"{link.idea_block_id}:{link.entity_id}:{link.role}"
        current_revision = current_revisions.get(block.current_revision_id) if block is not None else None
        if block is None or current_revision is None or current_revision.idea_block_id != block.id:
            findings.append(
                _finding(
                    "idea_block_entity_dangling_idea_block",
                    target_id,
                    "IdeaBlockEntity link has a missing or invalid current IdeaBlock revision.",
                    target_type="idea_block_entity",
                )
            )
            continue
        if block.merged_into_id is not None or block.tombstoned_at is not None or block.archived_at is not None:
            findings.append(
                _finding(
                    "idea_block_entity_inactive_idea_block",
                    target_id,
                    "IdeaBlockEntity link references an inactive IdeaBlock.",
                    target_type="idea_block_entity",
                )
            )
        if entity is None or entity.lifecycle_status != "active":
            findings.append(
                _finding(
                    "idea_block_entity_dangling_entity",
                    target_id,
                    "IdeaBlockEntity link has a missing or inactive Entity.",
                    target_type="idea_block_entity",
                )
            )
        elif entity.domain_id not in {None, block.domain_id}:
            findings.append(
                _finding(
                    "idea_block_entity_cross_domain",
                    target_id,
                    "IdeaBlockEntity Entity must be global or belong to the IdeaBlock domain.",
                    target_type="idea_block_entity",
                )
            )
    return findings


def _idea_block_relation_findings(
    session: Session, blocks: dict[str, IdeaBlock], domain_id: str | None
) -> list[HygieneFinding]:
    findings: list[HygieneFinding] = []
    current_revisions = _current_idea_block_revisions(session, blocks)
    for relation in session.scalars(select(IdeaBlockRelation)).all():
        source = blocks.get(relation.source_idea_block_id)
        target = blocks.get(relation.target_idea_block_id)
        if domain_id is not None and not (
            {source.domain_id if source else None, target.domain_id if target else None} & {domain_id}
        ):
            continue
        if relation.status not in {"candidate", "verified"}:
            continue
        definition = RELATIONSHIP_REGISTRY.get(relation.relation_type)
        if definition is None or definition.persistence_class != RelationPersistence.IDEA_BLOCK:
            findings.append(
                _finding(
                    "idea_block_relation_invalid_ontology_type", relation.id, "IdeaBlock relation type is not valid."
                )
            )
        elif (definition.symmetric and relation.direction != "symmetric") or (
            not definition.symmetric and relation.direction != "directed"
        ):
            findings.append(
                _finding(
                    "idea_block_relation_invalid_ontology_direction",
                    relation.id,
                    "IdeaBlock relation direction does not match its ontology definition.",
                )
            )
        source_revision = current_revisions.get(source.current_revision_id) if source is not None else None
        target_revision = current_revisions.get(target.current_revision_id) if target is not None else None
        if (
            source is None
            or target is None
            or source_revision is None
            or target_revision is None
            or source_revision.idea_block_id != source.id
            or target_revision.idea_block_id != target.id
        ):
            findings.append(
                _finding(
                    "idea_block_relation_dangling_idea_block", relation.id, "IdeaBlock relation has a missing endpoint."
                )
            )
        elif source.domain_id != target.domain_id:
            findings.append(
                _finding(
                    "idea_block_relation_cross_domain",
                    relation.id,
                    "IdeaBlock relation endpoints must belong to the same domain.",
                )
            )
        elif (
            source.merged_into_id is not None
            or target.merged_into_id is not None
            or source.tombstoned_at is not None
            or source.archived_at is not None
            or target.tombstoned_at is not None
            or target.archived_at is not None
        ):
            findings.append(
                _finding(
                    "idea_block_relation_dangling_idea_block",
                    relation.id,
                    "IdeaBlock relation references an inactive endpoint.",
                )
            )
        elif source_revision.publication_status != "verified" or target_revision.publication_status != "verified":
            findings.append(
                _finding(
                    "idea_block_relation_dangling_idea_block",
                    relation.id,
                    "IdeaBlock relation references an unverified endpoint.",
                )
            )
        if relation.status == "verified" and not relation.decision_provenance:
            findings.append(
                _finding(
                    "relationship_missing_provenance",
                    relation.id,
                    "Verified IdeaBlock relation has no decision provenance.",
                    target_type="idea_block_relation",
                )
            )
        if relation.valid_from and relation.valid_to and relation.valid_to < relation.valid_from:
            findings.append(
                _finding(
                    "relationship_temporal_inconsistency", relation.id, "IdeaBlock relation validity range is inverted."
                )
            )
    return findings


def _governance_link_findings(
    session: Session,
    entities: dict[str, Entity],
    blocks: dict[str, IdeaBlock],
    records: dict[str, GovernanceRecord],
    domain_id: str | None,
) -> list[HygieneFinding]:
    findings: list[HygieneFinding] = []
    current_revisions = _current_idea_block_revisions(session, blocks)
    for link in session.scalars(select(GovernanceRecordEntity)).all():
        record = records.get(link.governance_record_id)
        if domain_id is not None and record is not None and record.domain_id != domain_id:
            continue
        entity = entities.get(link.entity_id)
        if record is None or entity is None or entity.lifecycle_status != "active":
            findings.append(
                _finding(
                    "governance_link_dangling_entity",
                    f"{link.governance_record_id}:{link.entity_id}",
                    "Governance link has a missing or inactive Entity.",
                )
            )
        elif entity.domain_id not in {None, record.domain_id}:
            findings.append(
                _finding(
                    "governance_link_cross_domain",
                    f"{link.governance_record_id}:{link.entity_id}",
                    "Governance Entity link must target a global Entity or one in the record domain.",
                    target_type="governance_record_entity",
                )
            )
    for link in session.scalars(select(GovernanceRecordIdeaBlock)).all():
        record = records.get(link.governance_record_id)
        if domain_id is not None and record is not None and record.domain_id != domain_id:
            continue
        block = blocks.get(link.idea_block_id)
        current_revision = current_revisions.get(block.current_revision_id) if block is not None else None
        if record is None or block is None or current_revision is None or current_revision.idea_block_id != block.id:
            findings.append(
                _finding(
                    "governance_link_dangling_idea_block",
                    f"{link.governance_record_id}:{link.idea_block_id}",
                    "Governance link has a missing IdeaBlock.",
                )
            )
        elif block.domain_id != record.domain_id:
            findings.append(
                _finding(
                    "governance_link_cross_domain",
                    f"{link.governance_record_id}:{link.idea_block_id}",
                    "Governance IdeaBlock link must target a block in the record domain.",
                    target_type="governance_record_idea_block",
                )
            )
        elif block.merged_into_id is not None or block.tombstoned_at is not None or block.archived_at is not None:
            findings.append(
                _finding(
                    "governance_link_inactive_idea_block",
                    f"{link.governance_record_id}:{link.idea_block_id}",
                    "Governance link references an inactive IdeaBlock.",
                    target_type="governance_record_idea_block",
                )
            )
        elif current_revision.publication_status != "verified":
            findings.append(
                _finding(
                    "governance_link_non_current_idea_block",
                    f"{link.governance_record_id}:{link.idea_block_id}",
                    "Governance link references an IdeaBlock without a verified current revision.",
                    target_type="governance_record_idea_block",
                )
            )
    for relation in session.scalars(select(GovernanceRecordRelation)).all():
        source = records.get(relation.source_record_id)
        target = records.get(relation.target_record_id)
        if domain_id is not None and not (
            {source.domain_id if source else None, target.domain_id if target else None} & {domain_id}
        ):
            continue
        definition = GOVERNANCE_RELATIONSHIP_REGISTRY.get(relation.relation_type)
        if definition is None or definition.persistence_class != RelationPersistence.GOVERNANCE:
            findings.append(
                _finding(
                    "governance_relation_invalid_ontology_type", relation.id, "Governance relation type is not valid."
                )
            )
        if source is None or target is None:
            findings.append(
                _finding(
                    "governance_relation_dangling_record",
                    relation.id,
                    "Governance relation has a missing record endpoint.",
                )
            )
        elif definition is not None and definition.persistence_class == RelationPersistence.GOVERNANCE:
            if source.record_type not in definition.source_kinds or target.record_type not in definition.target_kinds:
                findings.append(
                    _finding(
                        "governance_relation_invalid_ontology_direction",
                        relation.id,
                        "Governance relation endpoints do not match its ontology definition.",
                    )
                )
            if source.domain_id != target.domain_id:
                findings.append(
                    _finding(
                        "governance_relation_cross_domain",
                        relation.id,
                        "Governance relation endpoints must belong to the same domain.",
                    )
                )
        if relation.status == "verified" and not relation.provenance:
            findings.append(
                _finding(
                    "relationship_missing_provenance",
                    relation.id,
                    "Verified governance relation has no provenance.",
                    target_type="governance_record_relation",
                )
            )
        if relation.valid_from and relation.valid_to and relation.valid_to < relation.valid_from:
            findings.append(
                _finding(
                    "relationship_temporal_inconsistency",
                    relation.id,
                    "Governance relation validity range is inverted.",
                )
            )
    return findings


def _entity_relationship_in_domain(
    relationship: EntityRelationship, entities: dict[str, Entity], domain_id: str | None
) -> bool:
    if domain_id is None:
        return True
    return any(
        entity is not None and entity.domain_id == domain_id
        for entity in (entities.get(relationship.source_entity_id), entities.get(relationship.target_entity_id))
    )


def _finding(
    rule_id: str, target_id: str, message: str, *, target_type: str = "entity_relationship", severity: str = "error"
) -> HygieneFinding:
    return HygieneFinding(
        rule_id=rule_id,
        severity=severity,
        target_type=target_type,
        target_id=target_id,
        message=message,
        recommendation="Correct the persisted relationship through its governed review workflow.",
    )
