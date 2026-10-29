"""The single provider and text boundary for Margin embeddings."""

from margin_embedding.factory import (
    DEFAULT_EMBEDDING_INDEX_VERSION,
    DEFAULT_EMBEDDING_MODEL_ID,
    DEFAULT_EMBEDDING_PROVIDER_ID,
    create_embedding_provider,
    required_embedding_index_config,
)
from margin_embedding.interfaces import (
    EMBEDDING_DIMENSIONS,
    EmbeddingIndexConfig,
    EmbeddingProvider,
    validate_embedding_batch,
)
from margin_embedding.providers import SentenceTransformersEmbeddingProvider
from margin_embedding.text import (
    embedding_content_hash,
    embedding_text_for_idea_block,
    embedding_text_for_source_element,
)

__all__ = [
    "DEFAULT_EMBEDDING_PROVIDER_ID",
    "DEFAULT_EMBEDDING_MODEL_ID",
    "DEFAULT_EMBEDDING_INDEX_VERSION",
    "EMBEDDING_DIMENSIONS",
    "EmbeddingIndexConfig",
    "EmbeddingProvider",
    "SentenceTransformersEmbeddingProvider",
    "create_embedding_provider",
    "embedding_content_hash",
    "embedding_text_for_idea_block",
    "embedding_text_for_source_element",
    "required_embedding_index_config",
    "validate_embedding_batch",
]
