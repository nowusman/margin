from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

EMBEDDING_DIMENSIONS = 384


@dataclass(frozen=True)
class EmbeddingIndexConfig:
    model_id: str
    index_version: str
    dimensions: int = EMBEDDING_DIMENSIONS

    def __post_init__(self) -> None:
        if self.dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(f"Margin embeddings require exactly {EMBEDDING_DIMENSIONS} dimensions")


class EmbeddingProvider(Protocol):
    provider_id: str
    model_id: str
    index_version: str
    dimensions: int

    def embed(self, texts: tuple[str, ...]) -> tuple[list[float], ...]:
        """Return one fixed-dimension vector for each supplied text."""


def validate_embedding_batch(
    vectors: tuple[list[float], ...], *, expected_count: int, declared_dimensions: int
) -> None:
    """Reject incomplete or non-approved provider output before persistence."""
    if declared_dimensions != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"embedding provider declared {declared_dimensions} dimensions; " f"Margin requires {EMBEDDING_DIMENSIONS}"
        )
    if len(vectors) != expected_count:
        raise ValueError(f"embedding provider returned {len(vectors)} vectors for {expected_count} texts")
    invalid = next((index for index, vector in enumerate(vectors) if len(vector) != EMBEDDING_DIMENSIONS), None)
    if invalid is not None:
        raise ValueError(
            f"embedding provider returned {len(vectors[invalid])} dimensions for item {invalid}; "
            f"Margin requires {EMBEDDING_DIMENSIONS}"
        )
