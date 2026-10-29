from __future__ import annotations

import re
from datetime import date
from enum import Enum
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from margin_schema.mappings import CONFIDENCE_STATES
from margin_schema.ontology import (
    ENTITY_KINDS,
    ENTITY_RELATIONSHIP_TYPES,
    RELATIONSHIP_REGISTRY,
    RelationPersistence,
    normalize_entity_kind,
    normalize_relationship_type,
)
from margin_schema.text_normalization import normalize_entity_name

ENTITY_ROLES = {"subject", "object", "context", "owner", "producer", "consumer"}
EXTRACTION_METHODS = {
    "deterministic_key_value",
    "deterministic_markdown",
    "deterministic_principle",
    "deterministic_relationship",
    "deterministic_table",
    "deterministic_contract",
    "deterministic_diagram",
    "deterministic",
    "openai_compatible_inference",
    "manual",
}


class CandidateRejectionReason(str, Enum):
    """Stable reasons for keeping extraction output out of knowledge."""

    HEADING = "heading"
    NAVIGATION = "navigation"
    BOILERPLATE = "boilerplate"
    FRAGMENT = "fragment"
    CAPTION_ONLY = "caption_only"
    COMPOUND_CLAIM = "compound_claim"
    UNSUPPORTED_INFERENCE = "unsupported_inference"
    INVALID_EVIDENCE = "invalid_evidence"
    INVALID_SEMANTIC_FIELDS = "invalid_semantic_fields"
    INVALID_ENDPOINT_KINDS = "invalid_endpoint_kinds"
    UNKNOWN_RELATIONSHIP_TYPE = "unknown_relationship_type"
    REFERENCES_UNMAPPED_ENTITY = "references_unmapped_entity"


SUPPORTED_CANDIDATE_TYPES = frozenset(
    {
        "api_operation",
        "api_operation_security",
        "architecture_principle",
        "attribute",
        "code_dependency",
        "code_structure",
        "c4_entity",
        "c4_relationship",
        "consequence",
        "context",
        "decision",
        "decision_record",
        "diagram_reference",
        "diagram_source",
        "diagram_relationship",
        "fact",
        "interface_matrix_row",
        "owner",
        "relationship",
        "responsibility_row",
        "risk",
        "risk_register_row",
        "standard",
        "status",
        "statement",
        "structured_key_value",
        "structured_table_row",
        "table_review",
        "technology_choice",
        "technology_version",
        "version",
    }
)


def is_supported_candidate_type(value: str) -> bool:
    normalized = value.strip().casefold()
    return (
        normalized in SUPPORTED_CANDIDATE_TYPES
        or normalized.startswith(("contract_", "event_", "frontmatter_", "schema_", "security_"))
        or normalized.endswith("_contract")
    )


def candidate_rejection_reason(candidate: ExtractedIdeaBlock) -> CandidateRejectionReason | None:
    """Return the stable rejection reason for an obviously invalid candidate."""

    statement = candidate.statement.strip()
    lowered = statement.casefold()
    if not statement:
        return CandidateRejectionReason.FRAGMENT
    if lowered.startswith(("#", "##", "###")):
        return CandidateRejectionReason.HEADING
    if candidate.type.casefold() in {"heading", "caption", "table_caption", "figure_caption"}:
        return (
            CandidateRejectionReason.HEADING
            if candidate.type.casefold() == "heading"
            else CandidateRejectionReason.CAPTION_ONLY
        )
    if lowered.startswith(("table of contents", "contents", "previous", "next", "see also", "click here")):
        return CandidateRejectionReason.NAVIGATION
    if lowered in {"introduction", "overview", "references", "architecture", "document metadata"}:
        return CandidateRejectionReason.BOILERPLATE
    if len(statement.split()) < 3 or not any(character.isalpha() for character in statement):
        return CandidateRejectionReason.FRAGMENT
    if len(_statement_sentences(statement)) > 1:
        return CandidateRejectionReason.COMPOUND_CLAIM
    if not is_supported_candidate_type(candidate.type):
        return CandidateRejectionReason.INVALID_SEMANTIC_FIELDS
    return None


def _statement_sentences(statement: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", statement) if part.strip()]


class ExtractedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    role: str = "context"
    canonical_id: str | None = None
    unresolved: bool = True
    external_id: str | None = None

    @field_validator("kind")
    @classmethod
    def kind_must_be_known(cls, value: str) -> str:
        normalized = normalize_entity_kind(value)
        if normalized is None:
            raise ValueError(f"entity kind must be one of: {', '.join(sorted(ENTITY_KINDS))}")
        return normalized

    @field_validator("role")
    @classmethod
    def role_must_be_known(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in ENTITY_ROLES:
            raise ValueError(f"entity role must be one of: {', '.join(sorted(ENTITY_ROLES))}")
        return normalized

    @field_validator("external_id")
    @classmethod
    def external_id_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("external_id cannot be blank")
        return normalized

    @field_validator("canonical_id")
    @classmethod
    def canonical_id_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("canonical_id cannot be blank")
        return normalized

    @model_validator(mode="after")
    def canonical_id_or_unresolved_mention_is_required(self) -> ExtractedEntity:
        if self.canonical_id is not None and self.unresolved:
            raise ValueError("canonical_id and unresolved cannot both be set")
        if self.canonical_id is None and not self.unresolved:
            raise ValueError("an entity without canonical_id must be an unresolved mention")
        return self


class ExtractedRelationshipEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_element_id: str = Field(pattern=r"^elem_[A-Za-z0-9_-]+$")
    source_quote: str = Field(min_length=1)
    polarity: Literal["supports", "refutes"] = "supports"
    qualifiers: dict[str, JsonValue] = Field(default_factory=dict)
    valid_from: date | None = None
    valid_to: date | None = None
    extraction_method: str | None = None

    @model_validator(mode="after")
    def valid_range_must_be_ordered(self) -> ExtractedRelationshipEvidence:
        if self.valid_from is not None and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("relationship evidence valid_to must be on or after valid_from")
        return self


class ExtractedRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    target: str = Field(min_length=1)
    confidence: str = "extracted"
    polarity: Literal["supports", "refutes"] = "supports"
    raw_predicate: str | None = None
    qualifiers: dict[str, JsonValue] = Field(default_factory=dict)
    evidence: list[ExtractedRelationshipEvidence] = Field(default_factory=list)

    @field_validator("relation_type")
    @classmethod
    def relation_type_must_be_known(cls, value: str) -> str:
        normalized = normalize_relationship_type(value)
        if normalized is None or normalized not in ENTITY_RELATIONSHIP_TYPES:
            raise ValueError(
                "relationship type must be an EntityRelationship: " f"{', '.join(sorted(ENTITY_RELATIONSHIP_TYPES))}"
            )
        return normalized

    @field_validator("confidence")
    @classmethod
    def confidence_must_be_known(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in CONFIDENCE_STATES:
            raise ValueError(f"confidence must be one of: {', '.join(sorted(CONFIDENCE_STATES))}")
        return normalized


class EvidenceRegion(BaseModel):
    """Immutable reference to the visual asset region supporting a claim."""

    model_config = ConfigDict(extra="forbid")

    asset_checksum: str = Field(min_length=1)
    bounding_box: tuple[float, float, float, float]

    @field_validator("asset_checksum")
    @classmethod
    def asset_checksum_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("asset_checksum cannot be blank")
        return normalized

    @field_validator("bounding_box")
    @classmethod
    def bounding_box_must_be_finite_and_ordered(
        cls, value: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        if not all(isfinite(coordinate) for coordinate in value):
            raise ValueError("bounding_box coordinates must be finite")
        x0, y0, x1, y1 = value
        if x1 < x0 or y1 < y0:
            raise ValueError("bounding_box coordinates must be ordered")
        return value


class ExtractedClaimEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_element_id: str = Field(pattern=r"^elem_[A-Za-z0-9_-]+$")
    source_quote: str = Field(min_length=1)
    quote_char_start: int | None = None
    quote_char_end: int | None = None
    evidence_kind: Literal["verbatim", "derived_visual"] = "verbatim"
    region: EvidenceRegion | None = None
    polarity: Literal["supports", "refutes"] = "supports"
    qualifiers: dict[str, JsonValue] = Field(default_factory=dict)
    valid_from: date | None = None
    valid_to: date | None = None

    @field_validator("source_quote")
    @classmethod
    def source_quote_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_quote cannot be blank")
        return value

    @model_validator(mode="after")
    def valid_range_must_be_ordered(self) -> ExtractedClaimEvidence:
        if self.valid_from is not None and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("evidence valid_to must be on or after valid_from")
        if (self.quote_char_start is None) != (self.quote_char_end is None):
            raise ValueError("quote_char_start and quote_char_end must be provided together")
        if self.quote_char_start is not None and self.quote_char_end is not None:
            if self.quote_char_start < 0 or self.quote_char_end <= self.quote_char_start:
                raise ValueError("evidence quote offsets must be a positive ordered range")
        return self


class UnmappedExtractionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["claim", "entity", "relationship", "field"]
    reason: CandidateRejectionReason
    raw_payload: dict[str, JsonValue] = Field(default_factory=dict)


class ExtractedIdeaBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    type: str = Field(min_length=1)
    confidence: str = Field(min_length=1)
    polarity: Literal["supports", "refutes"] = "supports"
    qualifiers: dict[str, JsonValue] = Field(default_factory=dict)
    valid_from: date | None = None
    valid_to: date | None = None
    evidence: list[ExtractedClaimEvidence] = Field(min_length=1)
    extraction_method: str = "deterministic_markdown"
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    unmapped: list[UnmappedExtractionCandidate] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def type_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("type cannot be blank")
        return normalized

    @field_validator("qualifiers")
    @classmethod
    def qualifier_keys_must_be_stable(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        for key in value:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
                raise ValueError("qualifier keys must use lowercase snake_case")
        return value

    @field_validator("confidence")
    @classmethod
    def confidence_must_be_known(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in CONFIDENCE_STATES:
            raise ValueError(f"confidence must be one of: {', '.join(sorted(CONFIDENCE_STATES))}")
        return normalized

    @field_validator("extraction_method")
    @classmethod
    def extraction_method_must_be_known(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in EXTRACTION_METHODS:
            raise ValueError("extraction_method must be one of: " f"{', '.join(sorted(EXTRACTION_METHODS))}")
        return normalized

    @model_validator(mode="after")
    def valid_range_must_be_ordered(self) -> ExtractedIdeaBlock:
        if self.valid_from is not None and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("claim valid_to must be on or after valid_from")
        return self

    @model_validator(mode="after")
    def relationships_must_reference_entities(self) -> ExtractedIdeaBlock:
        entities_by_name = {normalize_entity_name(entity.name): entity for entity in self.entities}
        missing = [
            relationship
            for relationship in self.relationships
            if normalize_entity_name(relationship.source) not in entities_by_name
            or normalize_entity_name(relationship.target) not in entities_by_name
        ]
        if missing:
            raise ValueError("relationships must reference entities declared on the IdeaBlock")
        for relationship in self.relationships:
            definition = RELATIONSHIP_REGISTRY[relationship.relation_type]
            if definition.persistence_class is not RelationPersistence.ENTITY:
                raise ValueError(f"{relationship.relation_type} is not an EntityRelationship")
            source = entities_by_name[normalize_entity_name(relationship.source)]
            target = entities_by_name[normalize_entity_name(relationship.target)]
            if source.kind not in definition.source_kinds or target.kind not in definition.target_kinds:
                raise ValueError(f"{relationship.relation_type} does not permit {source.kind} -> {target.kind}")
            for occurrence in relationship.evidence:
                if not any(parent.source_element_id == occurrence.source_element_id for parent in self.evidence):
                    raise ValueError("relationship evidence must be a subset of the IdeaBlock evidence occurrences")
        return self


class ExtractedGovernanceRecordCandidate(BaseModel):
    """A source-supported proposal to review a governance record.

    This is deliberately separate from an ``ExtractedIdeaBlock``.  It carries
    no authority to create or change a governance record; ingestion only uses
    it to create a review task with exact source provenance.
    """

    model_config = ConfigDict(extra="forbid")

    business_key: str | None = None
    record_type: Literal["adr", "risk", "waiver", "technical_debt", "architecture_backlog"]
    title: str = Field(min_length=1)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    evidence: list[ExtractedClaimEvidence] = Field(min_length=1)
    proposed_lifecycle_action: (
        Literal["accept", "approve", "extend_expiry", "close", "supersede", "resolve", "complete"] | None
    ) = None

    @field_validator("business_key")
    @classmethod
    def business_key_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("business_key cannot be blank when provided")
        return normalized

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized


class ExtractorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea_blocks: list[ExtractedIdeaBlock] = Field(default_factory=list)
    governance_records: list[ExtractedGovernanceRecordCandidate] = Field(default_factory=list)

    @classmethod
    def from_json_text(cls, text: str) -> ExtractorOutput:
        return cls.model_validate_json(text)


def single_claim_evidence(
    *,
    source_element_id: str,
    source_quote: str,
    polarity: Literal["supports", "refutes"] = "supports",
    quote_char_start: int | None = None,
    quote_char_end: int | None = None,
    evidence_kind: Literal["verbatim", "derived_visual"] = "verbatim",
) -> list[ExtractedClaimEvidence]:
    return [
        ExtractedClaimEvidence(
            source_element_id=source_element_id,
            source_quote=source_quote,
            polarity=polarity,
            quote_char_start=quote_char_start,
            quote_char_end=quote_char_end,
            evidence_kind=evidence_kind,
        )
    ]


def primary_claim_evidence(block: ExtractedIdeaBlock) -> ExtractedClaimEvidence:
    return block.evidence[0]
