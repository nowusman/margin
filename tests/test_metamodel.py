from __future__ import annotations

from pathlib import Path

import pytest

from margin_schema import CONFIDENCE_STATES
from margin_schema.ontology import (
    ACTIVE_ONTOLOGY,
    ENTITY_KINDS,
    GOVERNANCE_RELATIONSHIP_REGISTRY,
    ONTOLOGY_VERSION,
    ONTOLOGY_VERSIONS,
    RELATIONSHIP_REGISTRY,
    VOCABULARY_RELATIONSHIP_TYPES,
    RelationPersistence,
    TypeEvidenceSource,
    TypeObservation,
    choose_canonical_type,
    validate_governance_relationship_endpoints,
    validate_ontology,
    validate_relationship_endpoints,
)


def test_runtime_ontology_has_one_deterministic_active_contract() -> None:
    validate_ontology()

    assert ACTIVE_ONTOLOGY.version == ONTOLOGY_VERSION
    assert tuple(version for version in ONTOLOGY_VERSIONS if version.lifecycle == "active") == (ACTIVE_ONTOLOGY,)


def test_pseudo_entities_are_not_entity_kinds() -> None:
    forbidden = {"source_document", "idea_block", "adr", "risk", "waiver", "technical_debt", "architecture_backlog"}

    assert forbidden.isdisjoint(ENTITY_KINDS)


def test_relationships_are_assigned_to_one_valid_persistence_class() -> None:
    assert RELATIONSHIP_REGISTRY["depends_on"].persistence_class is RelationPersistence.ENTITY
    assert RELATIONSHIP_REGISTRY["owned_by"].persistence_class is RelationPersistence.ENTITY
    assert RELATIONSHIP_REGISTRY["controlled_by"].persistence_class is RelationPersistence.ENTITY
    assert RELATIONSHIP_REGISTRY["secured_by"].persistence_class is RelationPersistence.ENTITY
    assert RELATIONSHIP_REGISTRY["produces"].persistence_class is RelationPersistence.ENTITY
    assert RELATIONSHIP_REGISTRY["consumes"].persistence_class is RelationPersistence.ENTITY
    assert RELATIONSHIP_REGISTRY["supersedes_entity"].persistence_class is RelationPersistence.ENTITY
    assert RELATIONSHIP_REGISTRY["mitigates"].persistence_class is RelationPersistence.GOVERNANCE
    assert RELATIONSHIP_REGISTRY["waives"].persistence_class is RelationPersistence.GOVERNANCE
    assert RELATIONSHIP_REGISTRY["corrects"].persistence_class is RelationPersistence.GOVERNANCE
    assert RELATIONSHIP_REGISTRY["supersedes"].persistence_class is RelationPersistence.IDEA_BLOCK
    assert set(GOVERNANCE_RELATIONSHIP_REGISTRY) == {
        "supersedes",
        "mitigates",
        "waives",
        "incurs",
        "resolves",
        "corrects",
    }

    validate_relationship_endpoints("depends_on", "api", "service")
    validate_relationship_endpoints("secured_by", "api", "security_scheme")
    validate_relationship_endpoints("produces", "api", "event")
    validate_relationship_endpoints("consumes", "api", "event")
    validate_governance_relationship_endpoints("mitigates", "risk", "adr")
    validate_governance_relationship_endpoints("waives", "waiver", "risk")
    validate_governance_relationship_endpoints("incurs", "adr", "risk")
    validate_governance_relationship_endpoints("resolves", "risk", "waiver")
    validate_governance_relationship_endpoints("corrects", "architecture_backlog", "technical_debt")
    validate_relationship_endpoints("supersedes", "idea_block", "idea_block")
    with pytest.raises(ValueError, match="does not allow"):
        validate_governance_relationship_endpoints("mitigates", "risk", "system")


def test_vocabulary_relations_share_the_entity_relationship_registry() -> None:
    assert VOCABULARY_RELATIONSHIP_TYPES == {"broader", "narrower", "related"}
    for relation_type in VOCABULARY_RELATIONSHIP_TYPES:
        assert RELATIONSHIP_REGISTRY[relation_type].persistence_class is RelationPersistence.ENTITY
        validate_relationship_endpoints(relation_type, "concept", "concept")


def test_curated_binary_relationships_are_registered_with_their_stated_direction() -> None:
    curated_relationships = {
        "contains",
        "supports",
        "uses",
        "exposes",
        "performs",
        "hosts",
        "connects_to",
        "integrates_with",
        "realizes",
        "used_in",
        "guides",
        "owned_by",
        "has_custodian",
        "operates",
        "maintains",
        "member_of",
        "reports_to",
        "works_on",
        "tracks",
        "tags",
        "applies_to",
        "evidences",
        "stores",
        "manages",
    }

    assert curated_relationships <= RELATIONSHIP_REGISTRY.keys()
    assert not RELATIONSHIP_REGISTRY["connects_to"].symmetric
    assert not RELATIONSHIP_REGISTRY["integrates_with"].symmetric


def test_architecture_ontology_document_tracks_the_runtime_contract() -> None:
    documentation = Path("docs/architecture/ontology.md").read_text(encoding="utf-8")

    assert not Path("ontology").exists()
    assert all(f"| `{kind}` " in documentation for kind in ENTITY_KINDS)
    assert all(f"| `{relation}` " in documentation for relation in RELATIONSHIP_REGISTRY)
    assert all(
        any(f"| `{relation}`" in line and "GovernanceRecordRelation" in line for line in documentation.splitlines())
        for relation in GOVERNANCE_RELATIONSHIP_REGISTRY
    )
    assert Path("docs/architecture/ontology-source/Architecture_Dictionary_Compact.md").exists()
    assert Path("docs/architecture/ontology-source/Architecture_Glossary_Compact.md").exists()
    assert Path("docs/architecture/ontology-source/Technology_Assets_Glossary.md").exists()


def test_type_precedence_keeps_equal_authority_conflicts_unresolved() -> None:
    assert (
        choose_canonical_type(
            (
                TypeObservation("system", TypeEvidenceSource.WEAK_CONTEXT, "elem_1"),
                TypeObservation("api", TypeEvidenceSource.EXPLICIT_TYPE, "elem_2"),
            )
        )
        == "api"
    )
    assert (
        choose_canonical_type(
            (
                TypeObservation("system", TypeEvidenceSource.EXPLICIT_TYPE, "elem_1"),
                TypeObservation("api", TypeEvidenceSource.EXPLICIT_TYPE, "elem_2"),
            )
        )
        is None
    )


def test_review_sensitive_confidence_states_are_registered() -> None:
    assert {"inferred", "ambiguous", "contradicted"} <= CONFIDENCE_STATES
