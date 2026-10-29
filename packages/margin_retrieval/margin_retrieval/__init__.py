"""Authorized retrieval and assistant orchestration for Margin."""

from margin_retrieval.adapters import (
    AuthorizedRankingSet,
    CrossEncoderRerankerProvider,
    RankedEvidence,
    validate_ranked_evidence,
)
from margin_retrieval.answering import CitationSufficiencyChecker
from margin_retrieval.interfaces import (
    AnswerEnvelope,
    Citation,
    EvidenceItem,
    ExpansionEnvelope,
    RetrievalExpansion,
    RetrievalScope,
    SearchResult,
)
from margin_retrieval.policy import CitationSufficiencyError
from margin_retrieval.service import RetrievalPermissionError, RetrievalService

__all__ = [
    "AuthorizedRankingSet",
    "AnswerEnvelope",
    "Citation",
    "CitationSufficiencyChecker",
    "CitationSufficiencyError",
    "CrossEncoderRerankerProvider",
    "EvidenceItem",
    "ExpansionEnvelope",
    "RankedEvidence",
    "RetrievalPermissionError",
    "RetrievalService",
    "RetrievalExpansion",
    "RetrievalScope",
    "SearchResult",
    "validate_ranked_evidence",
]
