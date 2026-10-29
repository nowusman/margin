"""Re-evaluation of records when the ontology version changes.

An ontology change is additive: it widens what can be expressed. It must never
silently reinterpret history, so every record written under a superseded
ontology version is re-evaluated against the current registry before it is read
under the new one.

A record that still validates is restamped at the current version, because the
current registry says the same thing about it. A record that no longer
validates is withdrawn and raised for review, because reading it under the new
ontology would be an interpretation nobody made.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.base import utc_now
from margin_db.entity_models import Entity
from margin_db.knowledge_models import IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship
from margin_schema.findings import HygieneFinding
from margin_schema.ontology import (
    ONTOLOGY_VERSION,
    normalize_entity_kind,
    normalize_relationship_type,
    validate_relationship_endpoints,
)

ONTOLOGY_UNSUPPORTED_RULE_ID = "ontology_no_longer_supported"


@dataclass(frozen=True, slots=True)
class OntologyReevaluation:
    """What a re-evaluation changed, and what it could not resolve."""

    restamped: int
    withdrawn: int
    findings: tuple[HygieneFinding, ...]


def reevaluate_ontology_records(session: Session) -> OntologyReevaluation:
    """Re-evaluate every record written under a superseded ontology version.

    Records the current registry still supports are restamped at the current
    version. Records it no longer supports are withdrawn and reported, so that
    an ontology change cannot silently reinterpret them.
    """

    findings: list[HygieneFinding] = []
    restamped = 0
    withdrawn = 0

    entities = session.scalars(select(Entity)).all()
    entities_by_id = {entity.id: entity for entity in entities}

    for entity in entities:
        if entity.ontology_version == ONTOLOGY_VERSION:
            continue
        kind = normalize_entity_kind(entity.ontology_type)
        if kind is None:
            findings.append(
                _unsupported_finding(
                    "entity",
                    entity.id,
                    entity.ontology_version,
                    f"The kind '{entity.ontology_type}' is not part of the current ontology.",
                    {"ontology_type": entity.ontology_type},
                )
            )
            entity.lifecycle_status = "retired"
            entity.retired_at = utc_now()
            withdrawn += 1
            continue
        entity.ontology_type = kind
        entity.ontology_version = ONTOLOGY_VERSION
        restamped += 1

    for relationship in session.scalars(select(EntityRelationship)).all():
        if relationship.ontology_version == ONTOLOGY_VERSION:
            continue
        source = entities_by_id.get(relationship.source_entity_id)
        target = entities_by_id.get(relationship.target_entity_id)
        relation_type = normalize_relationship_type(relationship.relation_type)
        if source is None or target is None or relation_type is None:
            findings.append(
                _unsupported_finding(
                    "entity_relationship",
                    relationship.id,
                    relationship.ontology_version,
                    "The relationship no longer has two readable endpoints in the current ontology.",
                    {
                        "relation_type": relationship.relation_type,
                        "source_entity_id": relationship.source_entity_id,
                        "target_entity_id": relationship.target_entity_id,
                    },
                )
            )
            relationship.status = "stale"
            withdrawn += 1
            continue
        try:
            validate_relationship_endpoints(relation_type, source.ontology_type, target.ontology_type)
        except ValueError as error:
            findings.append(
                _unsupported_finding(
                    "entity_relationship",
                    relationship.id,
                    relationship.ontology_version,
                    str(error),
                    {"relation_type": relation_type, "source_entity_id": source.id, "target_entity_id": target.id},
                )
            )
            relationship.status = "stale"
            withdrawn += 1
            continue
        relationship.relation_type = relation_type
        relationship.ontology_version = ONTOLOGY_VERSION
        restamped += 1

    for revision in session.scalars(select(IdeaBlockRevision)).all():
        if revision.ontology_version == ONTOLOGY_VERSION:
            continue
        revision.ontology_version = ONTOLOGY_VERSION
        restamped += 1

    return OntologyReevaluation(restamped=restamped, withdrawn=withdrawn, findings=tuple(findings))


def _unsupported_finding(
    target_type: str, target_id: str, ontology_version: str, reason: str, evidence: dict[str, str]
) -> HygieneFinding:
    return HygieneFinding(
        rule_id=ONTOLOGY_UNSUPPORTED_RULE_ID,
        severity="error",
        target_type=target_type,
        target_id=target_id,
        message=(
            f"This record was written under ontology version {ontology_version} and is no "
            f"longer supported by the current ontology: {reason}"
        ),
        evidence=(evidence,),
        recommendation=(
            "Confirm how this record should be read under the current ontology, and supersede "
            "it if the current ontology no longer expresses it."
        ),
    )
