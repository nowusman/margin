from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol, TypedDict, cast

from margin_retrieval.interfaces import RankedEvidenceResult, RerankCandidate, RetrievalScope


class _CrossEncoderOptions(TypedDict, total=False):
    tokenizer_args: dict[str, str]
    automodel_args: dict[str, str]
    max_length: int


class _CrossEncoderModel(Protocol):
    def predict(self, sentences: list[tuple[str, str]]) -> Iterable[float]: ...


@dataclass(frozen=True)
class AuthorizedRankingSet:
    query: str
    scope: RetrievalScope
    candidate_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class RankedEvidence:
    evidence_id: str
    score: float


@dataclass
class CrossEncoderRerankerProvider:
    provider_id: str
    model_id: str
    model_cache_dir: str | None = None
    max_length: int | None = None
    _model: _CrossEncoderModel = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError("sentence-transformers is required for local reranking") from exc
        kwargs: _CrossEncoderOptions = {}
        if self.model_cache_dir:
            kwargs["tokenizer_args"] = {"cache_dir": self.model_cache_dir}
            kwargs["automodel_args"] = {"cache_dir": self.model_cache_dir}
        if self.max_length is not None:
            kwargs["max_length"] = self.max_length
        try:
            self._model = cast(_CrossEncoderModel, CrossEncoder(self.model_id, **kwargs))
        except Exception as exc:
            raise RuntimeError(f"failed to load local reranker model '{self.model_id}'") from exc

    def rerank(
        self, *, query: str, scope: RetrievalScope, candidates: tuple[RerankCandidate, ...], limit: int
    ) -> tuple[RankedEvidenceResult, ...]:
        del scope
        if not candidates or limit <= 0:
            return ()
        pairs = [(query, candidate.text) for candidate in candidates]
        try:
            raw_scores = self._model.predict(pairs)
            scores = [float(score) for score in raw_scores]
            if len(scores) != len(candidates):
                raise ValueError("reranker score count did not match candidates")
        except Exception as exc:
            raise RuntimeError(f"failed to run local reranker model '{self.model_id}'") from exc
        ranked = tuple(
            RankedEvidenceResult(evidence_id=candidate.evidence_id, score=round(_sigmoid(score), 6))
            for candidate, score in zip(candidates, scores, strict=True)
        )
        return tuple(sorted(ranked, key=lambda item: (-item.score, item.evidence_id))[:limit])


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


def validate_ranked_evidence(request: AuthorizedRankingSet, ranked: tuple[RankedEvidence, ...]) -> None:
    candidate_ids = set(request.candidate_evidence_ids)
    unauthorized = [item.evidence_id for item in ranked if item.evidence_id not in candidate_ids]
    if unauthorized:
        raise ValueError("retrieval ranking returned evidence outside authorized candidates: " f"{unauthorized[0]}")
    seen: set[str] = set()
    for item in ranked:
        if item.evidence_id in seen:
            raise ValueError(f"retrieval ranking returned duplicate candidate: {item.evidence_id}")
        if not math.isfinite(item.score):
            raise ValueError(f"retrieval ranking returned a non-finite score: {item.evidence_id}")
        seen.add(item.evidence_id)
