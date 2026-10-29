"""The single machine-readable Margin ontology contract.

The curated source guidance is consolidated under ``docs/architecture`` and is
deliberately not read by runtime code. Source documents are the only evidence
for an instance or relationship; this module supplies the governed types,
directions, and resolution policy.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from enum import StrEnum


class OntologyLifecycle(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class RelationPersistence(StrEnum):
    ENTITY = "EntityRelationship"
    IDEA_BLOCK = "IdeaBlockRelation"
    GOVERNANCE = "GovernanceRecordRelation"


class TypeEvidenceSource(StrEnum):
    EXPLICIT_TYPE = "explicit_type"
    STRUCTURED_CLASSIFICATION = "structured_classification"
    STRUCTURAL_CONTEXT = "structural_context"
    RELATIONSHIP_ENDPOINT = "relationship_endpoint"
    DICTIONARY_COMPOUND = "dictionary_compound"
    DEFINITION_CONTEXT = "definition_context"
    WEAK_CONTEXT = "weak_context"


TYPE_EVIDENCE_PRECEDENCE: dict[TypeEvidenceSource, int] = {
    TypeEvidenceSource.EXPLICIT_TYPE: 1,
    TypeEvidenceSource.STRUCTURED_CLASSIFICATION: 2,
    TypeEvidenceSource.STRUCTURAL_CONTEXT: 3,
    TypeEvidenceSource.RELATIONSHIP_ENDPOINT: 4,
    TypeEvidenceSource.DICTIONARY_COMPOUND: 5,
    TypeEvidenceSource.DEFINITION_CONTEXT: 6,
    TypeEvidenceSource.WEAK_CONTEXT: 7,
}


@dataclass(frozen=True, slots=True)
class EntityKindDefinition:
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationshipDefinition:
    name: str
    aliases: tuple[str, ...]
    source_kinds: tuple[str, ...]
    target_kinds: tuple[str, ...]
    persistence_class: RelationPersistence
    inverse: str | None = None
    symmetric: bool = False
    cardinality: str = "many_to_many"
    temporal_behavior: str = "versioned"
    inferable: bool = False
    default_traversal: bool = True


@dataclass(frozen=True, slots=True)
class OntologyDefinition:
    version: str
    lifecycle: OntologyLifecycle
    entity_kinds: tuple[EntityKindDefinition, ...]
    relationships: tuple[RelationshipDefinition, ...]
    vocabulary_relationships: tuple[str, ...]
    governance_relationships: tuple[RelationshipDefinition, ...] = ()


@dataclass(frozen=True, slots=True)
class TypeObservation:
    ontology_type: str
    evidence_source: TypeEvidenceSource
    source_element_id: str


# This covers the curated architecture, technology-asset, organization, and
# vocabulary concepts without treating source artifacts, claims, or governance
# records as entities. More specific dictionary labels are Entity catalog rows
# of one of these types, not extra runtime ontology definitions.
_ENTITY_KIND_DEFINITIONS = (
    EntityKindDefinition("domain", ("business_domain", "operational_domain")),
    EntityKindDefinition("business_capability", ("capability",)),
    EntityKindDefinition("value_stream"),
    EntityKindDefinition("system", ("software_system",)),
    EntityKindDefinition("application", ("app",)),
    EntityKindDefinition("platform"),
    EntityKindDefinition("container"),
    EntityKindDefinition("component", ("module", "application_component")),
    EntityKindDefinition("service", ("application_service", "technology_service")),
    EntityKindDefinition("api", ("interface", "endpoint", "application_interface")),
    EntityKindDefinition("datastore", ("database", "data_store")),
    EntityKindDefinition("data", ("information", "record", "model")),
    EntityKindDefinition("schema", ("data_schema",)),
    EntityKindDefinition("repository", ("architecture_repository",)),
    EntityKindDefinition("technology", ("tool", "framework")),
    EntityKindDefinition("technology_asset", ("asset", "it_asset", "configuration_item")),
    EntityKindDefinition("device"),
    EntityKindDefinition("network"),
    EntityKindDefinition("node"),
    EntityKindDefinition("security_scheme", ("authentication_scheme",)),
    EntityKindDefinition("person"),
    EntityKindDefinition("team", ("group", "organizational_unit")),
    EntityKindDefinition("organization"),
    EntityKindDefinition("role"),
    EntityKindDefinition("event", ("message", "notification")),
    EntityKindDefinition("function"),
    EntityKindDefinition("process"),
    EntityKindDefinition("standard", ("architecture_standard",)),
    EntityKindDefinition("control", ("security_control",)),
    EntityKindDefinition("policy"),
    EntityKindDefinition("requirement"),
    EntityKindDefinition("license"),
    EntityKindDefinition("category"),
    EntityKindDefinition("tag"),
    EntityKindDefinition("idea_block_type"),
    EntityKindDefinition("concept"),
)

ENTITY_KIND_REGISTRY = {definition.name: definition for definition in _ENTITY_KIND_DEFINITIONS}
ALL_ENTITY_KINDS = tuple(definition.name for definition in _ENTITY_KIND_DEFINITIONS)
ENTITY_KINDS = frozenset(ENTITY_KIND_REGISTRY)
GOVERNANCE_RECORD_TYPES = ("adr", "risk", "waiver", "technical_debt", "architecture_backlog")

_ARCHITECTURE_KINDS = (
    "system",
    "application",
    "platform",
    "container",
    "component",
    "service",
    "api",
    "datastore",
    "repository",
    "technology",
    "technology_asset",
    "device",
    "network",
    "node",
    "security_scheme",
)
_ACTOR_KINDS = ("person", "team", "organization", "role")
_MANAGEABLE_KINDS = tuple(kind for kind in ALL_ENTITY_KINDS if kind not in {"category", "tag", "idea_block_type"})
_IDEA_BLOCK_ENDPOINT_TYPES = ("idea_block",)


def _relationship(
    name: str,
    *aliases: str,
    source_kinds: tuple[str, ...] = ALL_ENTITY_KINDS,
    target_kinds: tuple[str, ...] = ALL_ENTITY_KINDS,
    persistence_class: RelationPersistence = RelationPersistence.ENTITY,
    inverse: str | None = None,
    symmetric: bool = False,
    inferable: bool = False,
    default_traversal: bool = True,
    cardinality: str = "many_to_many",
    temporal_behavior: str = "versioned",
) -> RelationshipDefinition:
    return RelationshipDefinition(
        name=name,
        aliases=aliases,
        source_kinds=source_kinds,
        target_kinds=target_kinds,
        persistence_class=persistence_class,
        inverse=inverse,
        symmetric=symmetric,
        inferable=inferable,
        default_traversal=default_traversal,
        cardinality=cardinality,
        temporal_behavior=temporal_behavior,
    )


RELATIONSHIP_REGISTRY = {
    definition.name: definition
    for definition in (
        _relationship("contains", inverse="part_of"),
        _relationship("part_of", "is_part_of", "belongs_to", inverse="contains"),
        _relationship("supports", source_kinds=_MANAGEABLE_KINDS, target_kinds=_MANAGEABLE_KINDS),
        _relationship("uses", source_kinds=_ARCHITECTURE_KINDS, target_kinds=_ARCHITECTURE_KINDS),
        _relationship("depends_on", "depends", source_kinds=_ARCHITECTURE_KINDS, target_kinds=_ARCHITECTURE_KINDS),
        _relationship("produces", source_kinds=_ARCHITECTURE_KINDS, target_kinds=("event",)),
        _relationship("consumes", source_kinds=_ARCHITECTURE_KINDS, target_kinds=("event",)),
        _relationship(
            "exposes",
            "provides",
            "publishes",
            source_kinds=_ARCHITECTURE_KINDS,
            target_kinds=("api", "service", "event", "schema"),
        ),
        _relationship("secured_by", source_kinds=_ARCHITECTURE_KINDS, target_kinds=("security_scheme",)),
        _relationship(
            "performs", source_kinds=(*_ARCHITECTURE_KINDS, *_ACTOR_KINDS), target_kinds=("function", "process")
        ),
        _relationship(
            "hosts",
            source_kinds=("platform", "container", "node", "device", "network"),
            target_kinds=_ARCHITECTURE_KINDS,
        ),
        _relationship("connects_to", source_kinds=_ARCHITECTURE_KINDS, target_kinds=_ARCHITECTURE_KINDS),
        _relationship("calls", source_kinds=_ARCHITECTURE_KINDS, target_kinds=_ARCHITECTURE_KINDS),
        _relationship("integrates_with", source_kinds=_ARCHITECTURE_KINDS, target_kinds=_ARCHITECTURE_KINDS),
        _relationship(
            "realizes",
            source_kinds=_ARCHITECTURE_KINDS,
            target_kinds=("business_capability", "service", "requirement", "function"),
        ),
        _relationship("used_in", source_kinds=("business_capability",), target_kinds=("value_stream",)),
        _relationship(
            "guides",
            source_kinds=("standard", "control", "policy", "requirement", "concept"),
            target_kinds=_MANAGEABLE_KINDS,
        ),
        _relationship("owned_by", source_kinds=_MANAGEABLE_KINDS, target_kinds=_ACTOR_KINDS),
        _relationship("owns", source_kinds=_MANAGEABLE_KINDS, target_kinds=_MANAGEABLE_KINDS),
        _relationship("has_custodian", source_kinds=_MANAGEABLE_KINDS, target_kinds=_ACTOR_KINDS),
        _relationship("operates", source_kinds=_ACTOR_KINDS, target_kinds=_MANAGEABLE_KINDS),
        _relationship("maintains", source_kinds=_ACTOR_KINDS, target_kinds=_MANAGEABLE_KINDS),
        _relationship("member_of", source_kinds=("person",), target_kinds=("team", "organization")),
        _relationship("reports_to", source_kinds=("person",), target_kinds=("person", "role")),
        _relationship("works_on", source_kinds=_ACTOR_KINDS, target_kinds=_MANAGEABLE_KINDS),
        _relationship(
            "tracks",
            source_kinds=("process", "system", "application", "technology"),
            target_kinds=("technology_asset",),
        ),
        _relationship(
            "tags", source_kinds=("process", "system", "application", "technology"), target_kinds=("technology_asset",)
        ),
        _relationship(
            "applies_to", source_kinds=("license", "policy", "standard", "control"), target_kinds=_MANAGEABLE_KINDS
        ),
        _relationship(
            "evidences",
            source_kinds=("license", "data", "repository"),
            target_kinds=("license", "requirement", "control"),
        ),
        _relationship("stores", source_kinds=("datastore", "repository"), target_kinds=("data", "schema")),
        _relationship(
            "manages", source_kinds=(*_ACTOR_KINDS, "process", "system", "application"), target_kinds=_MANAGEABLE_KINDS
        ),
        _relationship(
            "controlled_by",
            "control",
            source_kinds=_MANAGEABLE_KINDS,
            target_kinds=("control", "standard", "policy", "requirement"),
        ),
        _relationship(
            "violates", source_kinds=_MANAGEABLE_KINDS, target_kinds=("control", "standard", "policy", "requirement")
        ),
        _relationship(
            "broader",
            inverse="narrower",
            source_kinds=("concept", "category", "tag"),
            target_kinds=("concept", "category", "tag"),
            default_traversal=False,
        ),
        _relationship(
            "narrower",
            inverse="broader",
            source_kinds=("concept", "category", "tag"),
            target_kinds=("concept", "category", "tag"),
            default_traversal=False,
        ),
        _relationship(
            "related",
            "related_vocabulary",
            symmetric=True,
            source_kinds=("concept", "category", "tag"),
            target_kinds=("concept", "category", "tag"),
            default_traversal=False,
        ),
        _relationship(
            "supersedes_entity",
            "entity_supersedes",
            source_kinds=_MANAGEABLE_KINDS,
            target_kinds=_MANAGEABLE_KINDS,
            inverse="superseded_by_entity",
        ),
        _relationship(
            "superseded_by_entity",
            source_kinds=_MANAGEABLE_KINDS,
            target_kinds=_MANAGEABLE_KINDS,
            inverse="supersedes_entity",
        ),
        _relationship(
            "contradicts",
            symmetric=True,
            source_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            target_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            persistence_class=RelationPersistence.IDEA_BLOCK,
            default_traversal=False,
        ),
        _relationship(
            "refines",
            source_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            target_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            persistence_class=RelationPersistence.IDEA_BLOCK,
            default_traversal=False,
        ),
        _relationship(
            "supersedes",
            source_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            target_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            persistence_class=RelationPersistence.IDEA_BLOCK,
            inverse="superseded_by",
            cardinality="many_to_one",
            default_traversal=False,
        ),
        _relationship(
            "superseded_by",
            source_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            target_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            persistence_class=RelationPersistence.IDEA_BLOCK,
            inverse="supersedes",
            cardinality="many_to_one",
            default_traversal=False,
        ),
        _relationship(
            "semantic_related",
            symmetric=True,
            source_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            target_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            persistence_class=RelationPersistence.IDEA_BLOCK,
            default_traversal=False,
        ),
        _relationship(
            "equivalent",
            symmetric=True,
            source_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            target_kinds=_IDEA_BLOCK_ENDPOINT_TYPES,
            persistence_class=RelationPersistence.IDEA_BLOCK,
            default_traversal=False,
        ),
        _relationship(
            "mitigates",
            persistence_class=RelationPersistence.GOVERNANCE,
            source_kinds=("adr", "risk", "technical_debt", "architecture_backlog"),
            target_kinds=GOVERNANCE_RECORD_TYPES,
        ),
        _relationship(
            "waives",
            persistence_class=RelationPersistence.GOVERNANCE,
            source_kinds=("waiver",),
            target_kinds=GOVERNANCE_RECORD_TYPES,
        ),
        _relationship(
            "incurs",
            persistence_class=RelationPersistence.GOVERNANCE,
            source_kinds=("adr", "architecture_backlog"),
            target_kinds=("risk", "technical_debt"),
        ),
        _relationship(
            "resolves",
            persistence_class=RelationPersistence.GOVERNANCE,
            source_kinds=("risk", "waiver", "technical_debt", "architecture_backlog"),
            target_kinds=GOVERNANCE_RECORD_TYPES,
        ),
        _relationship(
            "corrects",
            persistence_class=RelationPersistence.GOVERNANCE,
            source_kinds=("architecture_backlog", "technical_debt"),
            target_kinds=GOVERNANCE_RECORD_TYPES,
        ),
    )
}

# Governance records use the same relation vocabulary as the rest of the
# ontology, but their endpoints are governance records rather than catalog
# entities. ``supersedes`` is kept separate because IdeaBlockRelation already
# owns that name in the shared registry.
GOVERNANCE_RELATIONSHIP_REGISTRY = {
    name: RELATIONSHIP_REGISTRY[name] for name in ("mitigates", "waives", "incurs", "resolves", "corrects")
}
GOVERNANCE_RELATIONSHIP_REGISTRY["supersedes"] = _relationship(
    "supersedes",
    persistence_class=RelationPersistence.GOVERNANCE,
    source_kinds=("adr",),
    target_kinds=("adr",),
    cardinality="many_to_one",
    default_traversal=False,
)
GOVERNANCE_RELATIONSHIP_TYPES = frozenset(GOVERNANCE_RELATIONSHIP_REGISTRY)

RELATIONSHIP_TYPES = frozenset(set(RELATIONSHIP_REGISTRY) | set(GOVERNANCE_RELATIONSHIP_TYPES))
ENTITY_RELATIONSHIP_TYPES = frozenset(
    name
    for name, definition in RELATIONSHIP_REGISTRY.items()
    if definition.persistence_class is RelationPersistence.ENTITY
)
IDEA_BLOCK_RELATIONSHIP_TYPES = frozenset(
    name
    for name, definition in RELATIONSHIP_REGISTRY.items()
    if definition.persistence_class is RelationPersistence.IDEA_BLOCK
)
DEFAULT_TRAVERSAL_RELATIONSHIP_TYPES = frozenset(
    name
    for name, definition in RELATIONSHIP_REGISTRY.items()
    if definition.persistence_class is RelationPersistence.ENTITY and definition.default_traversal
)
VOCABULARY_RELATIONSHIP_TYPES = frozenset({"broader", "narrower", "related"})


def ontology_payload() -> dict[str, object]:
    return {
        "entity_kinds": [asdict(ENTITY_KIND_REGISTRY[name]) for name in sorted(ENTITY_KIND_REGISTRY)],
        "governance_record_types": list(GOVERNANCE_RECORD_TYPES),
        "relationships": [
            {
                **asdict(RELATIONSHIP_REGISTRY[name]),
                "persistence_class": RELATIONSHIP_REGISTRY[name].persistence_class.value,
            }
            for name in sorted(RELATIONSHIP_REGISTRY)
        ],
        "governance_relationships": [
            {
                **asdict(GOVERNANCE_RELATIONSHIP_REGISTRY[name]),
                "persistence_class": GOVERNANCE_RELATIONSHIP_REGISTRY[name].persistence_class.value,
            }
            for name in sorted(GOVERNANCE_RELATIONSHIP_REGISTRY)
        ],
        "type_evidence_precedence": {source.value: priority for source, priority in TYPE_EVIDENCE_PRECEDENCE.items()},
        "vocabulary_relationships": sorted(VOCABULARY_RELATIONSHIP_TYPES),
    }


def _version_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


ONTOLOGY_VERSION = f"margin-ontology-{_version_hash(ontology_payload())}"
ACTIVE_ONTOLOGY = OntologyDefinition(
    version=ONTOLOGY_VERSION,
    lifecycle=OntologyLifecycle.ACTIVE,
    entity_kinds=tuple(ENTITY_KIND_REGISTRY[name] for name in sorted(ENTITY_KIND_REGISTRY)),
    relationships=tuple(RELATIONSHIP_REGISTRY[name] for name in sorted(RELATIONSHIP_REGISTRY)),
    vocabulary_relationships=tuple(sorted(VOCABULARY_RELATIONSHIP_TYPES)),
    governance_relationships=tuple(
        GOVERNANCE_RELATIONSHIP_REGISTRY[name] for name in sorted(GOVERNANCE_RELATIONSHIP_REGISTRY)
    ),
)
ONTOLOGY_VERSIONS = (ACTIVE_ONTOLOGY,)


def validate_ontology() -> None:
    aliases: dict[str, str] = {}
    if sum(version.lifecycle is OntologyLifecycle.ACTIVE for version in ONTOLOGY_VERSIONS) != 1:
        raise ValueError("the runtime ontology must have exactly one active version")
    for name, definition in RELATIONSHIP_REGISTRY.items():
        if definition.inverse is not None and definition.inverse not in RELATIONSHIP_REGISTRY:
            raise ValueError(f"relationship {name} references unknown inverse {definition.inverse}")
        if definition.persistence_class is RelationPersistence.ENTITY:
            unknown_kinds = (set(definition.source_kinds) | set(definition.target_kinds)) - ENTITY_KINDS
        elif definition.persistence_class is RelationPersistence.IDEA_BLOCK:
            unknown_kinds = (set(definition.source_kinds) | set(definition.target_kinds)) - set(
                _IDEA_BLOCK_ENDPOINT_TYPES
            )
        else:
            unknown_kinds = (set(definition.source_kinds) | set(definition.target_kinds)) - set(GOVERNANCE_RECORD_TYPES)
        if unknown_kinds:
            raise ValueError(f"relationship {name} references unknown endpoint types: {sorted(unknown_kinds)}")
        for alias in (name, *definition.aliases):
            normalized = _normalize(alias)
            owner = aliases.get(normalized)
            if owner is not None and owner != name:
                raise ValueError(f"relationship alias {alias} is shared by {owner} and {name}")
            aliases[normalized] = name
    governance_aliases: dict[str, str] = {}
    for name, definition in GOVERNANCE_RELATIONSHIP_REGISTRY.items():
        if definition.persistence_class is not RelationPersistence.GOVERNANCE:
            raise ValueError(f"governance relationship {name} has the wrong persistence class")
        unknown_kinds = (set(definition.source_kinds) | set(definition.target_kinds)) - set(GOVERNANCE_RECORD_TYPES)
        if unknown_kinds:
            raise ValueError(
                f"governance relationship {name} references unknown endpoint types: {sorted(unknown_kinds)}"
            )
        for alias in (name, *definition.aliases):
            normalized = _normalize(alias)
            owner = governance_aliases.get(normalized)
            if owner is not None and owner != name:
                raise ValueError(f"governance relationship alias {alias} is shared by {owner} and {name}")
            governance_aliases[normalized] = name


def normalize_entity_kind(value: str) -> str | None:
    normalized = _normalize(value)
    if normalized in ENTITY_KIND_REGISTRY:
        return normalized
    for definition in ENTITY_KIND_REGISTRY.values():
        if normalized in definition.aliases:
            return definition.name
    return None


def normalize_relationship_type(value: str) -> str | None:
    normalized = _normalize(value)
    if normalized in RELATIONSHIP_REGISTRY:
        return normalized
    for definition in RELATIONSHIP_REGISTRY.values():
        if normalized in definition.aliases:
            return definition.name
    return None


def relationship_definition(relation_type: str) -> RelationshipDefinition:
    normalized = normalize_relationship_type(relation_type)
    if normalized is None:
        raise ValueError(f"unknown relationship type: {relation_type}")
    return RELATIONSHIP_REGISTRY[normalized]


def governance_relationship_definition(relation_type: str) -> RelationshipDefinition:
    normalized = _normalize(relation_type)
    if normalized not in GOVERNANCE_RELATIONSHIP_REGISTRY:
        for name, definition in GOVERNANCE_RELATIONSHIP_REGISTRY.items():
            if normalized in {_normalize(alias) for alias in definition.aliases}:
                normalized = name
                break
        else:
            raise ValueError(f"unknown governance relationship type: {relation_type}")
    return GOVERNANCE_RELATIONSHIP_REGISTRY[normalized]


def validate_relationship_endpoints(relation_type: str, source_kind: str, target_kind: str) -> None:
    definition = relationship_definition(relation_type)
    if definition.persistence_class is RelationPersistence.ENTITY:
        normalized_source = normalize_entity_kind(source_kind)
        normalized_target = normalize_entity_kind(target_kind)
    elif definition.persistence_class is RelationPersistence.IDEA_BLOCK:
        normalized_source = _normalize_idea_block_type(source_kind)
        normalized_target = _normalize_idea_block_type(target_kind)
    else:
        normalized_source = _normalize_governance_type(source_kind)
        normalized_target = _normalize_governance_type(target_kind)
    if normalized_source not in definition.source_kinds or normalized_target not in definition.target_kinds:
        raise ValueError(
            f"relationship {definition.name} does not allow {normalized_source!r} -> {normalized_target!r}"
        )


def validate_governance_relationship_endpoints(relation_type: str, source_type: str, target_type: str) -> None:
    definition = governance_relationship_definition(relation_type)
    normalized_source = _normalize_governance_type(source_type)
    normalized_target = _normalize_governance_type(target_type)
    if normalized_source not in definition.source_kinds or normalized_target not in definition.target_kinds:
        raise ValueError(
            f"governance relationship {definition.name} does not allow "
            f"{normalized_source!r} -> {normalized_target!r}"
        )


def choose_canonical_type(observations: Iterable[TypeObservation]) -> str | None:
    """Choose only an unambiguous highest-authority source-grounded type."""

    ranked = sorted(
        (
            (TYPE_EVIDENCE_PRECEDENCE[observation.evidence_source], normalize_entity_kind(observation.ontology_type))
            for observation in observations
            if normalize_entity_kind(observation.ontology_type) is not None
        ),
        key=lambda item: (item[0], item[1] or ""),
    )
    if not ranked:
        return None
    highest_priority = ranked[0][0]
    competing = {kind for priority, kind in ranked if priority == highest_priority}
    return next(iter(competing)) if len(competing) == 1 else None


def canonical_relationship_endpoints(relation_type: str, source_id: str, target_id: str) -> tuple[str, str]:
    definition = relationship_definition(relation_type)
    if definition.symmetric and target_id < source_id:
        return target_id, source_id
    return source_id, target_id


def relationship_phrase_map(*, persistence_class: RelationPersistence | None = None) -> tuple[tuple[str, str], ...]:
    phrases = {
        alias.replace("_", " "): definition.name
        for definition in RELATIONSHIP_REGISTRY.values()
        if persistence_class is None or definition.persistence_class is persistence_class
        for alias in (definition.name, *definition.aliases)
    }
    return tuple(sorted(phrases.items(), key=lambda item: (-len(item[0]), item[0])))


def _normalize_governance_type(value: str) -> str | None:
    normalized = _normalize(value)
    return normalized if normalized in GOVERNANCE_RECORD_TYPES else None


def _normalize_idea_block_type(value: str) -> str | None:
    normalized = _normalize(value)
    return normalized if normalized in _IDEA_BLOCK_ENDPOINT_TYPES else None


def _normalize(value: str) -> str:
    return value.strip().casefold().replace(" ", "_").replace("-", "_")


validate_ontology()
