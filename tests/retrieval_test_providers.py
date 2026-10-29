from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

from margin_embedding.interfaces import EMBEDDING_DIMENSIONS
from margin_retrieval.interfaces import RankedEvidenceResult, RerankCandidate, RetrievalScope

TEST_EMBEDDING_PROVIDER_ID = "test-embedding-provider"
TEST_EMBEDDING_MODEL_ID = "test-embedding-model-v1"
TEST_EMBEDDING_INDEX_VERSION = "test-embedding-index-v1"
TEST_RERANKER_PROVIDER_ID = "test-reranker-provider"
TEST_RERANKER_MODEL_ID = "test-reranker-model-v1"


def tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]*", value.casefold())}


@dataclass(frozen=True)
class FakeEmbeddingProvider:
    provider_id: str = TEST_EMBEDDING_PROVIDER_ID
    model_id: str = TEST_EMBEDDING_MODEL_ID
    index_version: str = TEST_EMBEDDING_INDEX_VERSION
    dimensions: int = EMBEDDING_DIMENSIONS

    def embed(self, texts: tuple[str, ...]) -> tuple[list[float], ...]:
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> list[float]:
        tokens = tokenize(text)
        if not tokens:
            return [0.0] * self.dimensions
        vector = [0.0] * self.dimensions
        for token in tokens:
            digest = sha256(token.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:8], "big") % self.dimensions] += 1.0
        norm = sum(value * value for value in vector) ** 0.5
        if norm <= 0:
            return vector
        return [value / norm for value in vector]


@dataclass(frozen=True)
class FakeRerankerProvider:
    provider_id: str = TEST_RERANKER_PROVIDER_ID
    model_id: str = TEST_RERANKER_MODEL_ID

    def rerank(
        self, *, query: str, scope: RetrievalScope, candidates: tuple[RerankCandidate, ...], limit: int
    ) -> tuple[RankedEvidenceResult, ...]:
        del scope
        query_tokens = tokenize(query)
        ranked = tuple(
            RankedEvidenceResult(
                evidence_id=candidate.evidence_id,
                score=round(
                    min(
                        candidate.base_score
                        + _token_overlap_score(query_tokens, tokenize(candidate.text)) * 0.2
                        + _stage_bonus(candidate.stage),
                        1.0,
                    ),
                    4,
                ),
            )
            for candidate in candidates
        )
        return tuple(sorted(ranked, key=lambda item: (-item.score, item.evidence_id))[:limit])


def _stage_bonus(stage: str) -> float:
    return {"lexical": 0.08, "semantic_vector": 0.05, "source_tree": 0.04}.get(stage, 0)


def _token_overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def hybrid_retriever_kwargs() -> dict[str, object]:
    return {"embedding_provider": FakeEmbeddingProvider(), "reranker_provider": FakeRerankerProvider()}
