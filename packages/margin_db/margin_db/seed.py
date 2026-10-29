from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.entity_catalog import add_alias, create_entity, upsert_entity_relationship
from margin_db.entity_models import Entity
from margin_db.model_catalog_models import ModelConfiguration
from margin_db.user_models import Domain, Group
from margin_schema.text_normalization import normalize_entity_name
from margin_schema.vocabulary import ControlledVocabulary, VocabularyTerm


@dataclass(frozen=True, slots=True)
class SeedDomain:
    code: str
    name: str
    owner: str
    steward: str
    description: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    sub_domains: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SeedResult:
    domains_created: int = 0
    domains_updated: int = 0
    vocabulary_created: int = 0
    vocabulary_updated: int = 0
    groups_created: int = 0
    groups_updated: int = 0
    models_created: int = 0


DEFAULT_DOMAIN_SEEDS: tuple[SeedDomain, ...] = (
    SeedDomain(
        "ALL",
        "All Domains",
        "enterprise-architecture",
        "enterprise-architecture",
        "Cross-domain source classification.",
    ),
    SeedDomain(
        "BSS",
        "BSS",
        "enterprise-architecture",
        "bss-architecture",
        "Business Support Systems architecture domain.",
        aliases=("Business Support Systems",),
        sub_domains=(
            "Product Catalog",
            "CRM",
            "Order Management",
            "Billing",
            "Charging",
            "Revenue Assurance",
            "Partner Management",
            "Customer Care",
        ),
    ),
    SeedDomain(
        "OSS",
        "OSS",
        "enterprise-architecture",
        "oss-architecture",
        "Operations Support Systems architecture domain.",
        aliases=("Operations Support Systems",),
        sub_domains=(
            "Inventory",
            "Fulfillment/Orchestration",
            "Assurance",
            "Network Provisioning",
            "Fault Mgmt",
            "Performance Mgmt",
            "Service Quality",
            "Field Ops",
        ),
    ),
    SeedDomain(
        "NSS",
        "NSS",
        "enterprise-architecture",
        "network-architecture",
        "Network Support Systems architecture domain.",
        aliases=("Network Support Systems",),
        sub_domains=("5GC/IMS", "Policy/Charging", "Signaling", "Network Slicing", "Subscriber Data Management"),
    ),
    SeedDomain(
        "DATA_AI",
        "Data & AI",
        "data-architecture",
        "data-ai-architecture",
        "Data platforms, analytics, and AI architecture domain.",
        aliases=("Data and AI", "Analytics"),
    ),
    SeedDomain(
        "SECURITY",
        "Security",
        "security-architecture",
        "security-architecture",
        "Security architecture domain covering cyber and privacy controls.",
        aliases=("Cybersecurity", "Information Security"),
    ),
)


@dataclass(frozen=True, slots=True)
class SeedGroup:
    id: str
    name: str
    description: str
    status: str = "active"


@dataclass(frozen=True, slots=True)
class SeedModel:
    id: str
    display_name: str
    purpose: str
    runtime: str
    model_name: str
    endpoint_url: str
    secret_reference: str | None
    capabilities: dict[str, object]
    context_length: int
    max_tokens: int
    temperature_default: float
    license: str
    hardware_profile: str
    default_scopes: tuple[str, ...]
    system_prompt: str


DEFAULT_GROUP_SEEDS: tuple[SeedGroup, ...] = (
    SeedGroup("margin-admin", "margin-admin", "Super admin group with full Margin platform access."),
    SeedGroup("architecture-admins", "architecture-admins", "Architecture governance administrators."),
    SeedGroup("architecture-reviewers", "architecture-reviewers", "Architecture reviewers and auditors."),
)


DEFAULT_MODEL_SEEDS: tuple[SeedModel, ...] = (
    SeedModel(
        id="inference-narrator",
        display_name="Primary Minimax M3",
        purpose="extraction",
        runtime="Ollama",
        model_name="minimax-m3:cloud",
        endpoint_url="http://host.docker.internal:11434/v1",
        secret_reference=None,
        capabilities={"text_input": True, "structured_output": True},
        context_length=0,
        max_tokens=0,
        temperature_default=0.0,
        license="model-specific",
        hardware_profile="local-ollama",
        default_scopes=("internal",),
        system_prompt="Margin governed architecture extraction engine",
    ),
    SeedModel(
        id="vision-gemini-3-8-flash",
        display_name="Gemini 3.8 Flash Vision/OCR",
        purpose="vision_ocr",
        runtime="Gemini",
        model_name="gemini-3.8-flash",
        endpoint_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        secret_reference="env:GEMINI_API_KEY",
        capabilities={"text_input": True, "image_input": True, "structured_output": True},
        context_length=1_048_576,
        max_tokens=65_536,
        temperature_default=0.0,
        license="model-specific",
        hardware_profile="provider-managed",
        default_scopes=("internal",),
        system_prompt="Margin governed vision, OCR, and VL extraction engine",
    ),
)


IDEA_BLOCK_TYPE_TERMS: tuple[str, ...] = (
    "capability",
    "target_state",
    "reference_architecture",
    "architecture_principle",
    "roadmap",
    "standard_adoption",
    "integration",
    "interface_contract",
    "data_model",
    "deployment",
    "operational_process",
    "tmf_oda_mapping",
    "tmf_open_api",
    "3gpp_interface",
    "network_function",
    "network_slice",
    "charging_flow",
    "decision",
    "exception",
    "risk_control",
    "debt_driver",
    "compliance_gap",
    "service_artifact",
    "api_specification",
    "protocol_binding",
    "capacity_plan",
    "sla_definition",
    "topology_view",
    "migration_plan",
    "technology_roadmap",
    "cost_model",
    "vendor_assessment",
    "regulatory_compliance",
    "incident_record",
    "change_request",
)
CATEGORY_TERMS: tuple[str, ...] = (
    "ADR",
    "Architecture Backlog",
    "Architecture Principles",
    "Architecture Standards",
    "Design Document",
    "Research Work",
    "Risk",
    "Roadmap Document",
    "Technical Debt",
    "Waiver",
)


def default_domain_vocabulary() -> ControlledVocabulary:
    return ControlledVocabulary(
        terms=tuple(
            VocabularyTerm(preferred_term=domain.name, aliases=domain.aliases, steward=domain.steward)
            for domain in DEFAULT_DOMAIN_SEEDS
        )
    )


def default_idea_block_type_vocabulary() -> ControlledVocabulary:
    return ControlledVocabulary(
        terms=tuple(
            VocabularyTerm(preferred_term=term, steward="enterprise-architecture") for term in IDEA_BLOCK_TYPE_TERMS
        )
    )


def default_category_vocabulary() -> ControlledVocabulary:
    return ControlledVocabulary(
        terms=tuple(VocabularyTerm(preferred_term=term, steward="enterprise-architecture") for term in CATEGORY_TERMS)
    )


def seed_domains_and_vocabulary(
    session: Session,
    domains: tuple[SeedDomain, ...] = DEFAULT_DOMAIN_SEEDS,
    groups: tuple[SeedGroup, ...] = DEFAULT_GROUP_SEEDS,
    vocabulary: ControlledVocabulary | None = None,
    idea_block_type_vocabulary: ControlledVocabulary | None = None,
    category_vocabulary: ControlledVocabulary | None = None,
    *,
    commit: bool = True,
) -> SeedResult:
    """Seed canonical domains, vocabulary, groups, and disabled model catalog entries."""

    domains_created, domains_updated = _seed_domains(session, domains)
    vocabulary_created = 0
    vocabulary_updated = 0
    for ontology_type, document in (
        ("domain", vocabulary or default_domain_vocabulary()),
        ("idea_block_type", idea_block_type_vocabulary or default_idea_block_type_vocabulary()),
        ("category", category_vocabulary or default_category_vocabulary()),
    ):
        created, updated = _seed_terms(session, ontology_type, document)
        vocabulary_created += created
        vocabulary_updated += updated
    groups_created, groups_updated = _seed_groups(session, groups)
    models_created = _seed_models(session, DEFAULT_MODEL_SEEDS)
    if commit:
        session.commit()
    return SeedResult(
        domains_created=domains_created,
        domains_updated=domains_updated,
        vocabulary_created=vocabulary_created,
        vocabulary_updated=vocabulary_updated,
        groups_created=groups_created,
        groups_updated=groups_updated,
        models_created=models_created,
    )


def _seed_domains(session: Session, domains: tuple[SeedDomain, ...]) -> tuple[int, int]:
    created = 0
    updated = 0
    for seed in domains:
        record = session.get(Domain, seed.code)
        if record is None:
            record = Domain(id=seed.code, name=seed.name)
            session.add(record)
            created += 1
        else:
            updated += 1
        record.name = seed.name
        record.owner = seed.owner
        record.steward = seed.steward
        record.description = seed.description
        record.metadata_ = {
            **(record.metadata_ or {}),
            "aliases": list(seed.aliases),
            "sub_domains": list(seed.sub_domains),
        }
    session.flush()
    return created, updated


def _seed_terms(session: Session, ontology_type: str, vocabulary: ControlledVocabulary) -> tuple[int, int]:
    created = 0
    updated = 0
    records: list[tuple[VocabularyTerm, Entity]] = []
    for term in vocabulary.terms:
        normalized = normalize_entity_name(term.preferred_term)
        entity = session.scalar(
            select(Entity).where(
                Entity.ontology_type == ontology_type,
                Entity.domain_id.is_(None),
                Entity.locale == "en",
                Entity.normalized_label == normalized,
            )
        )
        metadata = {"seed_vocabulary_version": vocabulary.version, "steward": term.steward}
        if entity is None:
            entity = create_entity(
                session,
                preferred_label=term.preferred_term,
                ontology_type=ontology_type,
                domain_id=None,
                lifecycle_status="active",
                metadata=metadata,
                reason="canonical catalog seed",
            )
            created += 1
        else:
            entity.preferred_label = term.preferred_term
            entity.lifecycle_status = "active"
            entity.metadata_ = {**(entity.metadata_ or {}), **metadata}
            entity.catalog_version += 1
            updated += 1
        records.append((term, entity))

    by_label = {entity.normalized_label: entity for _term, entity in records}
    for term, entity in records:
        for alias in term.aliases:
            add_alias(
                session,
                entity=entity,
                alias=alias,
                source_provenance={"kind": "seed_vocabulary", "version": vocabulary.version},
                actor_id=None,
                reason="canonical catalog seed",
                schedule=False,
            )
        for alias in term.deprecated_terms:
            add_alias(
                session,
                entity=entity,
                alias=alias,
                source_provenance={"kind": "seed_vocabulary", "version": vocabulary.version},
                actor_id=None,
                reason="canonical catalog seed",
                lifecycle_status="deprecated",
                schedule=False,
            )
        for relation_type, labels in (
            ("broader", term.broader_terms),
            ("narrower", term.narrower_terms),
            ("related", term.related_terms),
        ):
            for label in labels:
                target = by_label.get(normalize_entity_name(label))
                if target is None:
                    raise ValueError(f"seed vocabulary relation references missing term: {label}")
                upsert_entity_relationship(session, source=entity, target=target, relation_type=relation_type)
    return created, updated


def _seed_groups(session: Session, groups: tuple[SeedGroup, ...]) -> tuple[int, int]:
    created = 0
    updated = 0
    for seed in groups:
        record = session.get(Group, seed.id)
        if record is None:
            record = Group(id=seed.id, name=seed.name)
            session.add(record)
            created += 1
        else:
            updated += 1
        record.name = seed.name
        record.description = seed.description
        record.status = seed.status
    return created, updated


def _seed_models(session: Session, models: tuple[SeedModel, ...]) -> int:
    created = 0
    for seed in models:
        if session.get(ModelConfiguration, seed.id) is not None:
            continue
        session.add(
            ModelConfiguration(
                id=seed.id,
                display_name=seed.display_name,
                purpose=seed.purpose,
                runtime=seed.runtime,
                model_name=seed.model_name,
                endpoint_url=seed.endpoint_url,
                secret_reference=seed.secret_reference,
                capabilities=dict(seed.capabilities),
                context_length=seed.context_length,
                max_tokens=seed.max_tokens,
                temperature_default=seed.temperature_default,
                license=seed.license,
                hardware_profile=seed.hardware_profile,
                default_scopes=list(seed.default_scopes),
                system_prompt=seed.system_prompt,
                status="disabled",
            )
        )
        created += 1
    session.flush()
    return created
