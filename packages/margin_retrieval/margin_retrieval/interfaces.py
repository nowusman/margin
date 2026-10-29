from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol


@dataclass(frozen=True)
class RetrievalScope:
    domain_ids: tuple[str, ...] = ()
    all_domain_ids_authorized: bool = False
    category_ids: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    entity_terms: tuple[str, ...] = ()
    entity_types: tuple[str, ...] = ()
    relationship_types: tuple[str, ...] = ()
    idea_block_types: tuple[str, ...] = ()
    source_document_ids: tuple[str, ...] = ()
    sensitivities: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ("verified",)
    date_from: date | None = None
    date_to: date | None = None
    governance_record_types: tuple[str, ...] = ()
    governance_statuses: tuple[str, ...] = ()
    architecture_backlog_states: tuple[str, ...] = ()
    all_category_ids_authorized: bool = False


@dataclass(frozen=True)
class RetrievalExpansion:
    """Explicit, bounded post-retrieval traversal request."""

    relation_types: tuple[str, ...] = ()
    depth: int = 0
    limit: int = 0


@dataclass(frozen=True)
class Citation:
    citation_id: str
    idea_block_id: str | None = None
    idea_block_revision_id: str | None = None
    source_document_id: str | None = None
    source_document_title: str | None = None
    source_version_id: str | None = None
    source_version_sha256: str | None = None
    source_page_id: str | None = None
    source_element_id: str | None = None
    page_number: int | None = None
    bounding_box: dict[str, object] | None = None
    quote: str | None = None
    quote_char_start: int | None = None
    quote_char_end: int | None = None
    element_type: str | None = None
    polarity: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    governance_record_id: str | None = None
    governance_record_revision_id: str | None = None
    governance_record_type: str | None = None
    asset_key: str | None = None
    redacted: bool = False
    redaction_reason: str | None = None
    entity_id: str | None = None
    entity_name: str | None = None
    confidence: str | None = None


@dataclass(frozen=True)
class EvidenceItem:
    item_id: str
    kind: str
    stage: str
    score: float
    text: str
    citations: tuple[Citation, ...] = ()
    entity_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AnswerEnvelope:
    """Generated-answer state kept separate from retrieval mode."""

    status: Literal["not_requested", "generated", "unavailable"] = "not_requested"
    markdown: str | None = None
    unavailable_reason: str | None = None
    model: dict[str, object] | None = None
    latency_ms: int | None = None
    validation: str | None = None
    citation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExpansionEnvelope:
    """Authorized adjacent evidence returned separately from ranked results."""

    status: Literal["not_requested", "completed", "deadline_exceeded"] = "not_requested"
    relation_types: tuple[str, ...] = ()
    depth: int = 0
    results: tuple[EvidenceItem, ...] = ()


@dataclass(frozen=True)
class SearchResult:
    """The versioned shared result envelope for Search and Ask Margin."""

    query: str
    results: tuple[EvidenceItem, ...]
    response_mode: Literal["hybrid", "lexical_only", "search_only"]
    applied_scope: RetrievalScope
    stage_metrics: dict[str, dict[str, object]]
    warnings: tuple[str, ...] = ()
    ambiguity: tuple[dict[str, object], ...] = ()
    answer: AnswerEnvelope = field(default_factory=AnswerEnvelope)
    expansion: ExpansionEnvelope = field(default_factory=ExpansionEnvelope)


@dataclass(frozen=True)
class RerankCandidate:
    evidence_id: str
    stage: str
    text: str
    base_score: float


@dataclass(frozen=True)
class RankedEvidenceResult:
    evidence_id: str
    score: float


class RerankerProvider(Protocol):
    provider_id: str
    model_id: str

    def rerank(
        self, *, query: str, scope: RetrievalScope, candidates: tuple[RerankCandidate, ...], limit: int
    ) -> tuple[RankedEvidenceResult, ...]:
        """Rank only the supplied authorized candidates and return evidence ids with scores."""
