"""Validate the clean-bootstrap canonical Entity cutover."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_canonical_entities"
down_revision: str | Sequence[str] | None = "0006_semantic_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_TABLES = frozenset(
    {
        "architecture_entities",
        "architecture_entity_sources",
        "controlled_vocabulary",
        "vocabulary_aliases",
        "glossary_terms",
        "glossary_aliases",
    }
)
_REQUIRED_TABLES = frozenset(
    {
        "entities",
        "entity_aliases",
        "entity_mentions",
        "entity_resolution_decisions",
        "entity_resolution_actions",
        "entity_reresolution_jobs",
        "entity_relationships",
    }
)
_REQUIRED_COLUMNS = {
    "entities": {
        "id",
        "preferred_label",
        "normalized_label",
        "definition",
        "definition_idea_block_id",
        "ontology_type",
        "domain_id",
        "locale",
        "external_id",
        "lifecycle_status",
        "ontology_version",
        "catalog_version",
    },
    "entity_aliases": {
        "id",
        "entity_id",
        "normalized_alias",
        "locale",
        "domain_id",
        "ontology_type",
        "lifecycle_status",
        "source_provenance",
    },
    "entity_mentions": {"id", "idea_block_revision_id", "source_element_id", "proposed_type", "ontology_version"},
    "entity_resolution_decisions": {
        "mention_id",
        "entity_id",
        "method",
        "candidate_entity_ids",
        "ontology_version",
        "is_current",
    },
    "entity_relationships": {"source_entity_id", "target_entity_id", "relation_type", "ontology_version"},
}


def upgrade() -> None:
    """Accept only the paired clean target schema; never invent a legacy winner."""

    bind = op.get_bind()
    table_names = set(sa.inspect(bind).get_table_names())
    legacy = sorted(_LEGACY_TABLES.intersection(table_names))
    if legacy:
        raise RuntimeError(
            "0007_canonical_entities requires the clean Entity bootstrap; legacy tables remain: " + ", ".join(legacy)
        )
    missing = sorted(_REQUIRED_TABLES - table_names)
    if missing:
        raise RuntimeError(
            "0007_canonical_entities must run with the matching Entity model release; missing tables: "
            + ", ".join(missing)
        )
    _assert_required_columns(bind)
    _assert_catalog_collisions(bind)
    _assert_entity_types(bind)
    _assert_relationship_endpoints(bind)


def downgrade() -> None:
    raise RuntimeError("0007_canonical_entities is forward-only for the clean bootstrap")


def _assert_catalog_collisions(bind: sa.Connection) -> None:
    external_collision = bind.execute(
        sa.text(
            "SELECT external_id FROM entities WHERE external_id IS NOT NULL "
            "GROUP BY external_id HAVING count(*) > 1 LIMIT 1"
        )
    ).scalar_one_or_none()
    if external_collision is not None:
        raise RuntimeError(f"canonical Entity external_id collision: {external_collision}")
    alias_collision = bind.execute(
        sa.text(
            "SELECT a.normalized_alias FROM entity_aliases a "
            "JOIN entity_aliases b ON a.id < b.id "
            "AND a.lifecycle_status = 'active' AND b.lifecycle_status = 'active' "
            "AND a.ontology_type = b.ontology_type AND a.locale = b.locale "
            "AND a.normalized_alias = b.normalized_alias "
            "AND (a.domain_id = b.domain_id OR a.domain_id IS NULL OR b.domain_id IS NULL) LIMIT 1"
        )
    ).scalar_one_or_none()
    if alias_collision is not None:
        raise RuntimeError(f"canonical Entity alias collision: {alias_collision}")
    canonical_collision = bind.execute(
        sa.text(
            "SELECT a.normalized_label FROM entities a JOIN entities b ON a.id < b.id "
            "AND a.lifecycle_status <> 'retired' AND b.lifecycle_status <> 'retired' "
            "AND a.ontology_type = b.ontology_type AND a.locale = b.locale "
            "AND a.normalized_label = b.normalized_label "
            "AND (a.domain_id = b.domain_id OR a.domain_id IS NULL OR b.domain_id IS NULL) LIMIT 1"
        )
    ).scalar_one_or_none()
    if canonical_collision is not None:
        raise RuntimeError(f"canonical Entity label collision: {canonical_collision}")
    alias_canonical_collision = bind.execute(
        sa.text(
            "SELECT a.normalized_alias FROM entity_aliases a JOIN entities e "
            "ON a.entity_id <> e.id AND a.ontology_type = e.ontology_type AND a.locale = e.locale "
            "AND a.normalized_alias = e.normalized_label "
            "AND a.lifecycle_status = 'active' AND e.lifecycle_status = 'active' "
            "AND (a.domain_id = e.domain_id OR a.domain_id IS NULL OR e.domain_id IS NULL) LIMIT 1"
        )
    ).scalar_one_or_none()
    if alias_canonical_collision is not None:
        raise RuntimeError(f"canonical Entity alias/canonical collision: {alias_canonical_collision}")


def _assert_entity_types(bind: sa.Connection) -> None:
    from margin_schema.ontology import ENTITY_KINDS

    invalid = bind.execute(sa.text("SELECT DISTINCT ontology_type FROM entities ORDER BY ontology_type")).scalars()
    unexpected = [value for value in invalid if value not in ENTITY_KINDS]
    if unexpected:
        raise RuntimeError("canonical Entity ontology type collision: " + ", ".join(unexpected))


def _assert_required_columns(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    missing: list[str] = []
    for table_name, required in _REQUIRED_COLUMNS.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing.extend(f"{table_name}.{column}" for column in sorted(required - columns))
    if missing:
        raise RuntimeError("0007_canonical_entities is missing required Entity columns: " + ", ".join(missing))


def _assert_relationship_endpoints(bind: sa.Connection) -> None:
    """Reject rows that do not belong in the EntityRelationship persistence class."""

    from margin_schema.ontology import RelationPersistence, relationship_definition, validate_relationship_endpoints

    rows = bind.execute(
        sa.text(
            "SELECT relationship.id, relationship.relation_type, relationship.source_entity_id, "
            "relationship.target_entity_id, source.ontology_type, target.ontology_type "
            "FROM entity_relationships relationship "
            "JOIN entities source ON source.id = relationship.source_entity_id "
            "JOIN entities target ON target.id = relationship.target_entity_id"
        )
    )
    for relationship_id, relation_type, source_id, target_id, source_type, target_type in rows:
        try:
            if source_id == target_id:
                raise ValueError("relationship endpoints must be distinct")
            definition = relationship_definition(str(relation_type))
            if definition.persistence_class is not RelationPersistence.ENTITY:
                raise ValueError("relationship uses a different persistence class")
            validate_relationship_endpoints(str(relation_type), str(source_type), str(target_type))
        except ValueError as exc:
            raise RuntimeError(
                "canonical Entity relationship endpoint collision "
                f"for {relationship_id}: {relation_type} {source_type}->{target_type}: {exc}"
            ) from exc
