from __future__ import annotations

import os

from margin_embedding.factory import create_embedding_provider
from margin_embedding.interfaces import EmbeddingProvider
from margin_embedding.providers import configure_huggingface_runtime
from margin_retrieval.adapters import CrossEncoderRerankerProvider
from margin_retrieval.service import RetrievalService

DEFAULT_RERANKER_PROVIDER_ID = "local-cross-encoder"
DEFAULT_RERANKER_MODEL_ID = "BAAI/bge-reranker-base"


def local_reranker_provider() -> CrossEncoderRerankerProvider:
    configure_huggingface_runtime()
    model_id = os.getenv("MARGIN_RERANKER_MODEL_ID", DEFAULT_RERANKER_MODEL_ID).strip() or DEFAULT_RERANKER_MODEL_ID
    provider_id = (
        os.getenv("MARGIN_RERANKER_PROVIDER_ID", DEFAULT_RERANKER_PROVIDER_ID).strip() or DEFAULT_RERANKER_PROVIDER_ID
    )
    return CrossEncoderRerankerProvider(
        provider_id=provider_id, model_id=model_id, model_cache_dir=os.getenv("MARGIN_RERANKER_MODEL_CACHE_DIR") or None
    )


def build_retrieval_service() -> RetrievalService:
    return RetrievalService(
        embedding_provider=_optional_embedding_provider(), reranker_provider=_optional_reranker_provider()
    )


def _optional_embedding_provider() -> EmbeddingProvider | None:
    if not _enabled("MARGIN_RETRIEVAL_DENSE_ENABLED"):
        return None
    try:
        return create_embedding_provider()
    except (ImportError, OSError, RuntimeError):
        return None


def _optional_reranker_provider() -> CrossEncoderRerankerProvider | None:
    if not _enabled("MARGIN_RETRIEVAL_RERANK_ENABLED"):
        return None
    try:
        return local_reranker_provider()
    except (ImportError, OSError, RuntimeError):
        return None


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes"}
