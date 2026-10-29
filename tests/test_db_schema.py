from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.knowledge_models import IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship
from margin_db.review_models import ReviewTask
from margin_db.source_models import DUBLIN_CORE_FIELDS
from margin_db.user_models import Domain


def columns(table_name: str) -> set[str]:
    return {column.name for column in Base.metadata.tables[table_name].columns}


def test_canonical_core_tables_are_registered() -> None:
    required_tables = {
        "source_documents",
        "source_document_versions",
        "source_pages",
        "source_elements",
        "source_page_outcomes",
        "source_element_outcomes",
        "entities",
        "idea_blocks",
        "idea_block_entities",
        "entity_relationships",
        "relationship_provenance",
        "governance_records",
        "governance_record_revisions",
        "governance_record_idea_blocks",
        "governance_record_entities",
        "governance_record_relations",
        "audit_events",
        "review_tasks",
        "extraction_runs",
        "span_extraction_attempts",
        "entity_aliases",
        "entity_mentions",
        "entity_resolution_decisions",
        "entity_resolution_actions",
        "reprocessing_diffs",
    }

    assert required_tables <= set(Base.metadata.tables)


def test_identity_and_access_tables_are_registered() -> None:
    required_tables = {"users", "groups", "user_groups", "domains", "domain_permissions", "category_permissions"}

    assert required_tables <= set(Base.metadata.tables)


def test_dublin_core_contract_covers_all_15_fields() -> None:
    assert {
        "title",
        "creator",
        "subject",
        "description",
        "publisher",
        "contributor",
        "date",
        "type",
        "format",
        "identifier",
        "source",
        "language",
        "relation",
        "coverage",
        "rights",
    } == set(DUBLIN_CORE_FIELDS)


def test_provenance_and_evidence_columns_exist() -> None:
    assert {
        "id",
        "idea_block_id",
        "source_element_id",
        "source_quote",
        "confidence",
        "extraction_method",
        "polarity",
        "evidence_kind",
        "quote_char_start",
        "quote_char_end",
    } <= columns("claim_evidence")
    assert "evidence_idea_block_id" not in columns("entity_relationships")
    assert {"confidence", "status"} <= columns("entity_relationships")
    assert {
        "id",
        "relationship_id",
        "idea_block_id",
        "source_element_id",
        "confidence",
        "polarity",
        "status",
        "valid_from",
        "valid_to",
        "metadata",
        "reviewed_at",
        "reviewed_by",
    } <= columns("relationship_provenance")
    assert {
        "normalized_label",
        "domain_id",
        "external_id",
        "superseded_by_entity_id",
        "retired_at",
        "ontology_version",
    } <= columns("entities")


def test_ownership_and_stewardship_columns_exist() -> None:
    for table_name in ("domains", "source_documents"):
        assert {"owner", "steward", "owner_user_id", "steward_user_id"} <= columns(table_name)
    assert {"owner_user_id", "steward_user_id"} <= columns("entities")


def test_rbac_placeholder_columns_exist() -> None:
    assert {"id", "display_name", "email", "external_subject", "status"} <= columns("users")
    assert {"id", "name", "description", "status"} <= columns("groups")
    assert {"user_id", "group_id"} <= columns("user_groups")
    assert {"domain_id", "principal_type", "principal_id", "permission"} <= columns("domain_permissions")
    assert {"category", "principal_type", "principal_id", "permission"} <= columns("category_permissions")


def test_model_catalog_prompt_column_exists() -> None:
    assert {"system_prompt"} <= columns("model_configurations")


def test_governed_schema_columns_exist() -> None:
    required_columns = {
        "source_documents": {
            "id",
            "title",
            "source_uri",
            "object_key",
            "media_type",
            "sha256",
            "size_bytes",
            "domain_id",
            "category_id",
            "sensitivity",
            "status",
            "created_at",
            "ingested_by",
        },
        "source_document_versions": {
            "id",
            "source_document_id",
            "version_sha256",
            "converter_name",
            "converter_version",
            "converted_object_key",
            "created_at",
        },
        "source_pages": {"id", "source_version_id", "page_number", "content_checksum", "classification", "created_at"},
        "source_elements": {
            "id",
            "source_version_id",
            "source_page_id",
            "parent_element_id",
            "native_element_id",
            "kind",
            "ordinal",
            "text",
            "checksum",
            "char_start",
            "char_end",
            "bounding_box",
            "asset_key",
            "converter_method",
            "converter_version",
            "confidence",
            "created_at",
        },
        "source_page_outcomes": {"id", "extraction_run_id", "source_page_id", "outcome", "created_at"},
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
            "metadata",
            "created_at",
            "updated_at",
        },
        "idea_blocks": {
            "id",
            "domain_id",
            "current_revision_id",
            "merged_into_id",
            "tombstoned_at",
            "tombstone_reason",
            "archived_at",
            "archive_reason",
            "created_at",
            "updated_at",
        },
        "idea_block_revisions": {
            "id",
            "idea_block_id",
            "revision_number",
            "statement",
            "normalized_statement",
            "type",
            "confidence",
            "polarity",
            "qualifiers",
            "valid_from",
            "valid_to",
            "publication_status",
            "ontology_version",
            "actor",
            "process",
            "change_reason",
            "metadata",
            "created_at",
        },
        "idea_block_relations": {
            "id",
            "source_idea_block_id",
            "target_idea_block_id",
            "relation_type",
            "direction",
            "status",
            "valid_from",
            "valid_to",
            "confidence",
            "decision_provenance",
            "created_at",
            "updated_at",
        },
        "idea_block_entities": {"idea_block_id", "entity_id", "role"},
        "entity_aliases": {
            "id",
            "entity_id",
            "alias",
            "normalized_alias",
            "locale",
            "domain_id",
            "ontology_type",
            "lifecycle_status",
            "source_provenance",
        },
        "entity_mentions": {
            "id",
            "idea_block_revision_id",
            "source_element_id",
            "domain_id",
            "text",
            "normalized_text",
            "proposed_type",
            "candidate_entity_ids",
            "ontology_version",
        },
        "entity_resolution_decisions": {
            "id",
            "mention_id",
            "entity_id",
            "outcome",
            "candidate_entity_ids",
            "method",
            "ontology_version",
            "is_current",
        },
        "entity_relationships": {
            "id",
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            "ontology_version",
            "confidence",
            "status",
            "valid_from",
            "valid_to",
            "metadata",
            "created_at",
        },
        "relationship_provenance": {"relationship_id", "idea_block_id", "source_element_id"},
        "governance_records": {
            "id",
            "business_key",
            "record_type",
            "title",
            "domain_id",
            "lifecycle_status",
            "attributes",
            "current_revision_id",
        },
        "governance_record_revisions": {
            "id",
            "governance_record_id",
            "revision_number",
            "snapshot",
            "transition_reason",
            "actor",
            "process",
        },
        "governance_record_idea_blocks": {"governance_record_id", "idea_block_id", "role", "display_order"},
        "governance_record_entities": {"governance_record_id", "entity_id", "role"},
        "governance_record_relations": {
            "id",
            "source_record_id",
            "target_record_id",
            "relation_type",
            "status",
            "provenance",
        },
        "audit_events": {
            "id",
            "actor",
            "action",
            "target_type",
            "target_id",
            "before",
            "after",
            "git_commit_sha",
            "created_at",
        },
        "review_tasks": {
            "id",
            "task_type",
            "status",
            "priority",
            "target_type",
            "target_id",
            "reason",
            "assigned_to",
            "created_at",
            "closed_at",
        },
    }

    for table_name, table_columns in required_columns.items():
        assert table_columns <= columns(table_name), table_name


def test_status_check_constraints_reject_invalid_values() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    invalid_rows = [
        Entity(
            id="ent_invalid",
            preferred_label="Invalid",
            normalized_label="invalid",
            ontology_type="system",
            domain_id="OSS",
            lifecycle_status="invalid_status",
        ),
        IdeaBlockRevision(
            id="ib_invalid",
            idea_block_id="ib_parent",
            revision_number=1,
            statement="Invalid claim.",
            normalized_statement="invalid claim",
            type="fact",
            confidence="extracted",
            publication_status="invalid_status",
        ),
        EntityRelationship(
            id="rel_invalid",
            source_entity_id="ent_a",
            target_entity_id="ent_b",
            relation_type="depends_on",
            confidence="extracted",
            status="invalid_status",
        ),
        ReviewTask(
            task_type="hygiene",
            target_type="entity",
            target_id="ent_a",
            reason="Constraint test",
            status="invalid_status",
        ),
    ]

    for row in invalid_rows:
        with Session(engine) as session:
            session.add(Domain(id="OSS", name="OSS"))
            session.add(
                Entity(id="ent_a", preferred_label="A", normalized_label="a", ontology_type="system", domain_id="OSS")
            )
            session.add(
                Entity(id="ent_b", preferred_label="B", normalized_label="b", ontology_type="system", domain_id="OSS")
            )
            session.flush()
            session.add(row)
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()
