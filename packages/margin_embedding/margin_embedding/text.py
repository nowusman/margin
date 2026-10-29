from __future__ import annotations

import json
from collections.abc import Iterable
from hashlib import sha256

from margin_db.knowledge_models import IdeaBlockRevision
from margin_db.source_models import SourceElement


def embedding_text_for_idea_block(revision: IdeaBlockRevision, *, entity_ids: Iterable[str] = ()) -> str:
    """Build deterministic semantic text from the exact revision contract."""
    qualifiers = json.dumps(revision.qualifiers or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    entities = ",".join(sorted({str(entity_id).strip() for entity_id in entity_ids if str(entity_id).strip()}))
    return " ".join(
        value
        for value in (revision.normalized_statement, revision.type, revision.polarity, qualifiers, entities)
        if value
    )


def embedding_text_for_source_element(element: SourceElement) -> str:
    """Use only the element's own text or persisted visual description."""
    metadata = element.metadata_ or {}
    visual_description = next(
        (str(metadata[key]).strip() for key in ("visual_description", "description", "summary") if metadata.get(key)),
        "",
    )
    return " ".join(value for value in ((element.text or "").strip(), visual_description) if value)


def embedding_content_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()
