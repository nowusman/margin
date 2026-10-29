from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol, TypedDict, cast

from margin_embedding.interfaces import EMBEDDING_DIMENSIONS, validate_embedding_batch


class _SentenceTransformerOptions(TypedDict, total=False):
    cache_folder: str


class _ArrayOutput(Protocol):
    def tolist(self) -> object: ...


class _SentenceTransformerModel(Protocol):
    def encode(self, sentences: list[str], *, normalize_embeddings: bool, convert_to_numpy: bool) -> _ArrayOutput: ...


@dataclass
class SentenceTransformersEmbeddingProvider:
    provider_id: str
    model_id: str
    index_version: str
    model_cache_dir: str | None = None
    dimensions: int = EMBEDDING_DIMENSIONS
    normalize_embeddings: bool = True
    _model: _SentenceTransformerModel = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(f"Margin embeddings require exactly {EMBEDDING_DIMENSIONS} dimensions")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("sentence-transformers is required for local embedding generation") from exc
        kwargs: _SentenceTransformerOptions = {}
        if self.model_cache_dir:
            kwargs["cache_folder"] = self.model_cache_dir
        try:
            self._model = cast(_SentenceTransformerModel, SentenceTransformer(self.model_id, **kwargs))
        except Exception as exc:
            raise RuntimeError(f"failed to load local embedding model '{self.model_id}'") from exc

    def embed(self, texts: tuple[str, ...]) -> tuple[list[float], ...]:
        if not texts:
            return ()
        try:
            raw_vectors = self._model.encode(
                list(texts), normalize_embeddings=self.normalize_embeddings, convert_to_numpy=True
            )
            vectors = _embedding_rows(raw_vectors.tolist())
            validate_embedding_batch(vectors, expected_count=len(texts), declared_dimensions=self.dimensions)
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(f"failed to generate local embeddings with '{self.model_id}'") from exc
        return vectors


def configure_huggingface_runtime() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def _embedding_rows(raw_rows: object) -> tuple[list[float], ...]:
    if not isinstance(raw_rows, list):
        raise ValueError("local embedding model returned a non-list vector payload")
    if all(isinstance(value, int | float) for value in raw_rows):
        return ([float(value) for value in raw_rows],)
    vectors: list[list[float]] = []
    for row in raw_rows:
        if not isinstance(row, list) or not all(isinstance(value, int | float) for value in row):
            raise ValueError("local embedding model returned non-numeric vector values")
        vectors.append([float(value) for value in row])
    return tuple(vectors)
