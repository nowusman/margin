"""PostgreSQL and migration helpers for Margin."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import column_property

from . import (  # noqa: F401
    agent_models,
    audit_models,
    charter_models,
    entity_models,
    export_models,
    extraction_models,
    governance_models,
    graph_models,
    knowledge_models,
    model_catalog_models,
    personal_models,
    relationship_models,
    review_models,
    source_models,
    user_models,
)
from .entity_models import Entity
from .knowledge_models import IdeaBlock, IdeaBlockRevision
from .source_models import SourceDocument

SourceDocument.category = column_property(  # type: ignore[attr-defined]
    select(Entity.preferred_label)
    .where(Entity.id == SourceDocument.category_id)
    .limit(1)
    .correlate(SourceDocument)
    .scalar_subquery()
)

IdeaBlock.category = column_property(  # type: ignore[attr-defined]
    select(IdeaBlockRevision.metadata_["category"].as_string())
    .where(IdeaBlockRevision.id == IdeaBlock.current_revision_id, IdeaBlockRevision.idea_block_id == IdeaBlock.id)
    .limit(1)
    .correlate(IdeaBlock)
    .scalar_subquery()
)
