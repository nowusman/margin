from __future__ import annotations

from margin_db.entity_models import Entity
from margin_schema.text_normalization import normalize_entity_name


def catalog_entity(
    *,
    entity_id: str,
    preferred_label: str,
    ontology_type: str,
    domain_id: str | None = None,
    lifecycle_status: str = "active",
) -> Entity:
    """Build a canonical catalog row for test fixtures that need explicit IDs."""

    return Entity(
        id=entity_id,
        preferred_label=preferred_label,
        normalized_label=normalize_entity_name(preferred_label),
        ontology_type=ontology_type,
        domain_id=domain_id,
        locale="en",
        lifecycle_status=lifecycle_status,
    )
