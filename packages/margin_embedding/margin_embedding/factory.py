from __future__ import annotations

import os

from margin_embedding.interfaces import EMBEDDING_DIMENSIONS, EmbeddingIndexConfig
from margin_embedding.providers import SentenceTransformersEmbeddingProvider, configure_huggingface_runtime

DEFAULT_EMBEDDING_PROVIDER_ID = "local-sentence-transformers"
DEFAULT_EMBEDDING_MODEL_ID = "BAAI/bge-small-en-v1.5"
DEFAULT_EMBEDDING_INDEX_VERSION = "bge-small-en-v1.5-v1"


def required_embedding_index_config() -> EmbeddingIndexConfig:
    model_id = os.getenv("MARGIN_EMBEDDING_MODEL_ID", DEFAULT_EMBEDDING_MODEL_ID).strip() or DEFAULT_EMBEDDING_MODEL_ID
    index_version = (
        os.getenv("MARGIN_EMBEDDING_INDEX_VERSION", DEFAULT_EMBEDDING_INDEX_VERSION).strip()
        or DEFAULT_EMBEDDING_INDEX_VERSION
    )
    return EmbeddingIndexConfig(model_id=model_id, index_version=index_version, dimensions=EMBEDDING_DIMENSIONS)


def create_embedding_provider() -> SentenceTransformersEmbeddingProvider:
    configure_huggingface_runtime()
    config = required_embedding_index_config()
    provider_id = (
        os.getenv("MARGIN_EMBEDDING_PROVIDER_ID", DEFAULT_EMBEDDING_PROVIDER_ID).strip()
        or DEFAULT_EMBEDDING_PROVIDER_ID
    )
    return SentenceTransformersEmbeddingProvider(
        provider_id=provider_id,
        model_id=config.model_id,
        index_version=config.index_version,
        model_cache_dir=os.getenv("MARGIN_EMBEDDING_MODEL_CACHE_DIR") or None,
        dimensions=config.dimensions,
    )
