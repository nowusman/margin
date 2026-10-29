"""Validate the unified Architecture Governance contract from the clean bootstrap."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

from margin_db.governance_types import (
    BACKLOG_ORIGIN_STATUSES,
    LIFECYCLE_STATES,
    GovernanceError,
    validate_record_fields,
    validate_record_type,
)
from margin_schema.ontology import governance_relationship_definition, validate_governance_relationship_endpoints

revision: str = "0008_unified_governance"
down_revision: str | Sequence[str] | None = "0007_canonical_entities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_TABLES = frozenset(
    {
        "adr_details",
        "risk_details",
        "waiver_details",
        "technical_debt_details",
        "backlog_details",
        "fitness_rule_exports",
    }
)
_REQUIRED_COLUMNS = {
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
    "claim_evidence": {"source_element_id", "governance_record_revision_id"},
}


def upgrade() -> None:
    """Validate the tables created by the clean bootstrap; never revive legacy survivors."""

    bind = op.get_bind()
    table_names = set(sa.inspect(bind).get_table_names())
    legacy = sorted(_LEGACY_TABLES.intersection(table_names))
    if legacy:
        raise RuntimeError(
            "0008_unified_governance requires clean bootstrap; legacy tables remain: " + ", ".join(legacy)
        )
    missing_tables = sorted(set(_REQUIRED_COLUMNS) - table_names)
    if missing_tables:
        raise RuntimeError("0008_unified_governance missing canonical tables: " + ", ".join(missing_tables))
    _assert_required_columns(bind)
    _ensure_lifecycle_constraint(bind)
    _assert_evidence_indexes(bind)
    _assert_records(bind)
    _ensure_governance_record_constraints(bind)
    _assert_links(bind)
    _assert_backlog_origins(bind)
    _assert_claim_evidence_origin(bind)


def downgrade() -> None:
    raise RuntimeError("0008_unified_governance is forward-only for the clean bootstrap")


def _assert_required_columns(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    missing: list[str] = []
    for table_name, columns in _REQUIRED_COLUMNS.items():
        present = {column["name"] for column in inspector.get_columns(table_name)}
        missing.extend(f"{table_name}.{column}" for column in sorted(columns - present))
    if missing:
        raise RuntimeError("0008_unified_governance missing columns: " + ", ".join(missing))


def _ensure_lifecycle_constraint(bind: sa.Connection) -> None:
    expected = {
        "adr": {"proposed", "accepted", "superseded", "deprecated", "rejected"},
        "risk": {"identified", "mitigating", "accepted", "closed"},
        "waiver": {"proposed", "approved", "expired", "revoked", "rejected"},
        "technical_debt": {"identified", "accepted", "planned", "in_progress", "resolved"},
        "architecture_backlog": {"open", "planned", "in_progress", "blocked", "completed", "cancelled"},
    }
    check = next(
        (
            item
            for item in sa.inspect(bind).get_check_constraints("governance_records")
            if item.get("name") == "ck_governance_records_lifecycle_status"
        ),
        None,
    )
    sqltext = ""
    if check is not None:
        raw_sqltext = check.get("sqltext")
        if isinstance(raw_sqltext, str):
            sqltext = raw_sqltext
    values = set(re.findall(r"'([^']+)'", sqltext))
    if values >= set().union(*expected.values()) and "lifecycle_status" in sqltext:
        return
    status_sql = (
        "(record_type = 'adr' AND lifecycle_status IN "
        "('proposed', 'accepted', 'superseded', 'deprecated', 'rejected')) "
        "OR (record_type = 'risk' AND lifecycle_status IN "
        "('identified', 'mitigating', 'accepted', 'closed')) "
        "OR (record_type = 'waiver' AND lifecycle_status IN "
        "('proposed', 'approved', 'expired', 'revoked', 'rejected')) "
        "OR (record_type = 'technical_debt' AND lifecycle_status IN "
        "('identified', 'accepted', 'planned', 'in_progress', 'resolved')) "
        "OR (record_type = 'architecture_backlog' AND lifecycle_status IN "
        "('open', 'planned', 'in_progress', 'blocked', 'completed', 'cancelled'))"
    )
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE governance_records DROP CONSTRAINT IF EXISTS ck_governance_records_lifecycle_status")
        op.execute(
            "ALTER TABLE governance_records ADD CONSTRAINT ck_governance_records_lifecycle_status CHECK ("
            + status_sql
            + ")"
        )
        return
    with op.batch_alter_table("governance_records", recreate="always") as batch:
        if check is not None:
            batch.drop_constraint("ck_governance_records_lifecycle_status", type_="check")
        batch.create_check_constraint("ck_governance_records_lifecycle_status", status_sql)


def _assert_records(bind: sa.Connection) -> None:
    attributes_type = "jsonb_typeof(attributes)" if bind.dialect.name == "postgresql" else "json_type(attributes)"
    invalid = bind.execute(
        sa.text(
            "SELECT id FROM governance_records WHERE trim(business_key) = '' OR trim(title) = '' "
            f"OR attributes IS NULL OR {attributes_type} <> 'object' OR current_revision_id IS NULL LIMIT 1"
        )
    ).scalar_one_or_none()
    if invalid is not None:
        raise RuntimeError(f"governance record {invalid} is missing its required canonical state")
    orphan = bind.execute(
        sa.text(
            "SELECT record.id FROM governance_records record "
            "LEFT JOIN governance_record_revisions revision ON revision.id = record.current_revision_id "
            "WHERE revision.id IS NULL LIMIT 1"
        )
    ).scalar_one_or_none()
    if orphan is not None:
        raise RuntimeError(f"governance record {orphan} has no current revision")
    mismatch = bind.execute(
        sa.text(
            "SELECT record.id FROM governance_records record "
            "JOIN governance_record_revisions revision ON revision.id = record.current_revision_id "
            "WHERE revision.governance_record_id <> record.id LIMIT 1"
        )
    ).scalar_one_or_none()
    if mismatch is not None:
        raise RuntimeError(f"governance record {mismatch} points to another record's revision")
    rows = bind.execute(
        sa.text(
            "SELECT id, record_type, lifecycle_status, attributes, priority, target_date, expiry_date "
            "FROM governance_records"
        )
    ).mappings()
    for row in rows:
        record_id = str(row["id"])
        try:
            record_type = validate_record_type(row["record_type"])
            lifecycle_status = row["lifecycle_status"]
            if lifecycle_status not in LIFECYCLE_STATES[record_type]:
                raise GovernanceError(f"{record_type} has invalid lifecycle state: {lifecycle_status}")
            attributes = row["attributes"]
            if isinstance(attributes, str):
                attributes = json.loads(attributes)
            if not isinstance(attributes, dict):
                raise GovernanceError("attributes must be an object")
            target_date = row["target_date"]
            if isinstance(target_date, str) and target_date.strip():
                target_date = date.fromisoformat(target_date)
            validate_record_fields(
                record_type,
                attributes,
                priority=row["priority"],
                target_date=target_date,
                target_status=lifecycle_status,
            )
            if record_type == "waiver":
                attribute_expiry = date.fromisoformat(str(attributes["expiry_date"]))
                scalar_expiry = row["expiry_date"]
                if isinstance(scalar_expiry, str) and scalar_expiry.strip():
                    scalar_expiry = date.fromisoformat(scalar_expiry)
                if not isinstance(scalar_expiry, date):
                    raise GovernanceError("waiver requires expiry_date scalar")
                if scalar_expiry != attribute_expiry:
                    raise GovernanceError("waiver expiry_date must match the typed attribute")
        except (GovernanceError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"governance record {record_id} violates the canonical contract: {exc}") from exc


def _ensure_governance_record_constraints(bind: sa.Connection) -> None:
    constraints = {
        "ck_governance_records_waiver_expiry": "record_type <> 'waiver' OR expiry_date IS NOT NULL",
        "ck_governance_records_attributes_object": (
            "jsonb_typeof(attributes) = 'object'"
            if bind.dialect.name == "postgresql"
            else "json_type(attributes) = 'object'"
        ),
        "ck_governance_records_waiver_expiry_attribute": (
            "record_type <> 'waiver' OR (expiry_date IS NOT NULL AND CASE "
            "WHEN jsonb_typeof(attributes -> 'expiry_date') = 'string' "
            "AND (attributes ->> 'expiry_date') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' "
            "THEN (attributes ->> 'expiry_date')::date = expiry_date ELSE false END)"
            if bind.dialect.name == "postgresql"
            else "record_type <> 'waiver' OR (expiry_date IS NOT NULL AND "
            "json_type(attributes, '$.expiry_date') = 'text' AND "
            "json_extract(attributes, '$.expiry_date') GLOB "
            "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND "
            "date(json_extract(attributes, '$.expiry_date')) = "
            "json_extract(attributes, '$.expiry_date') AND "
            "date(json_extract(attributes, '$.expiry_date')) = expiry_date)"
        ),
    }
    present = {
        item.get("name") for item in sa.inspect(bind).get_check_constraints("governance_records") if item.get("name")
    }
    missing = [(name, expression) for name, expression in constraints.items() if name not in present]
    if not missing:
        return
    if bind.dialect.name == "postgresql":
        for name, expression in missing:
            op.execute(sa.text(f"ALTER TABLE governance_records ADD CONSTRAINT {name} CHECK ({expression})"))
        return
    with op.batch_alter_table("governance_records", recreate="always") as batch:
        for name, expression in missing:
            batch.create_check_constraint(name, expression)


def _assert_links(bind: sa.Connection) -> None:
    checks = {
        "governance_record_idea_blocks": ("idea_blocks", "idea_block_id"),
        "governance_record_entities": ("entities", "entity_id"),
    }
    for table_name, (target_table, target_column) in checks.items():
        orphan = bind.execute(
            sa.text(
                f"SELECT link.governance_record_id FROM {table_name} link "
                f"LEFT JOIN governance_records record ON record.id = link.governance_record_id "
                f"LEFT JOIN {target_table} target ON target.id = link.{target_column} "
                "WHERE record.id IS NULL OR target.id IS NULL LIMIT 1"
            )
        ).scalar_one_or_none()
        if orphan is not None:
            raise RuntimeError(f"{table_name} contains an orphan link for governance record {orphan}")
    cross_domain = bind.execute(
        sa.text(
            "SELECT link.governance_record_id FROM governance_record_idea_blocks link "
            "JOIN governance_records record ON record.id = link.governance_record_id "
            "JOIN idea_blocks block ON block.id = link.idea_block_id "
            "WHERE record.domain_id <> block.domain_id LIMIT 1"
        )
    ).scalar_one_or_none()
    if cross_domain is not None:
        raise RuntimeError(f"governance_record_idea_blocks crosses domains for governance record {cross_domain}")
    invalid_entity_link = bind.execute(
        sa.text(
            "SELECT link.governance_record_id FROM governance_record_entities link "
            "JOIN governance_records record ON record.id = link.governance_record_id "
            "JOIN entities entity ON entity.id = link.entity_id "
            "WHERE entity.domain_id IS NOT NULL AND record.domain_id <> entity.domain_id LIMIT 1"
        )
    ).scalar_one_or_none()
    if invalid_entity_link is not None:
        raise RuntimeError(f"governance_record_entities crosses domains for governance record {invalid_entity_link}")
    orphan_relation = bind.execute(
        sa.text(
            "SELECT relation.id FROM governance_record_relations relation "
            "LEFT JOIN governance_records source ON source.id = relation.source_record_id "
            "LEFT JOIN governance_records target ON target.id = relation.target_record_id "
            "WHERE source.id IS NULL OR target.id IS NULL LIMIT 1"
        )
    ).scalar_one_or_none()
    if orphan_relation is not None:
        raise RuntimeError(f"governance_record_relations contains orphan relation {orphan_relation}")
    invalid_relation = bind.execute(
        sa.text(
            "SELECT relation.id FROM governance_record_relations relation "
            "JOIN governance_records source ON source.id = relation.source_record_id "
            "JOIN governance_records target ON target.id = relation.target_record_id "
            "WHERE source.domain_id <> target.domain_id "
            "OR relation.source_record_id = relation.target_record_id "
            "OR relation.relation_type NOT IN ('supersedes', 'mitigates', 'waives', 'incurs', 'resolves', 'corrects') "
            "OR relation.status NOT IN ('candidate', 'verified', 'rejected', 'stale') "
            "OR (relation.valid_to IS NOT NULL AND relation.valid_from IS NOT NULL "
            "AND relation.valid_to < relation.valid_from) LIMIT 1"
        )
    ).scalar_one_or_none()
    if invalid_relation is not None:
        raise RuntimeError(f"governance_record_relations contains invalid relation state {invalid_relation}")
    relation_rows = bind.execute(
        sa.text(
            "SELECT relation.id, relation.relation_type, relation.provenance, "
            "source.record_type AS source_type, target.record_type AS target_type "
            "FROM governance_record_relations relation "
            "JOIN governance_records source ON source.id = relation.source_record_id "
            "JOIN governance_records target ON target.id = relation.target_record_id"
        )
    ).mappings()
    for row in relation_rows:
        relation_id = str(row["id"])
        try:
            relation_type = governance_relationship_definition(str(row["relation_type"])).name
            validate_governance_relationship_endpoints(relation_type, row["source_type"], row["target_type"])
            provenance = row["provenance"]
            if isinstance(provenance, str):
                provenance = json.loads(provenance)
            if not isinstance(provenance, dict):
                raise GovernanceError("relation provenance must be an object")
            reason = provenance.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise GovernanceError("relation provenance requires a non-empty reason")
        except (GovernanceError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"governance relation {relation_id} violates the canonical contract: {exc}") from exc


def _assert_backlog_origins(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT id, domain_id, attributes FROM governance_records " "WHERE record_type = 'architecture_backlog'"
        )
    ).mappings()
    for row in rows:
        backlog_id = str(row["id"])
        attributes = row["attributes"]
        if isinstance(attributes, str):
            attributes = json.loads(attributes)
        if not isinstance(attributes, dict):
            raise RuntimeError(f"Architecture Backlog {backlog_id} attributes must be an object")
        origin_id = attributes.get("originating_governance_record_id")
        if not isinstance(origin_id, str) or not origin_id.strip():
            continue
        origin = (
            bind.execute(
                sa.text(
                    "SELECT id, domain_id, record_type, lifecycle_status "
                    "FROM governance_records WHERE id = :origin_id"
                ),
                {"origin_id": origin_id},
            )
            .mappings()
            .first()
        )
        if origin is None:
            raise RuntimeError(f"Architecture Backlog {backlog_id} has a missing origin {origin_id}")
        if origin["domain_id"] != row["domain_id"]:
            raise RuntimeError(f"Architecture Backlog {backlog_id} origin crosses domains")
        if origin["lifecycle_status"] not in BACKLOG_ORIGIN_STATUSES.get(origin["record_type"], frozenset()):
            raise RuntimeError(f"Architecture Backlog {backlog_id} origin is not an eligible interim record")
        relation = (
            bind.execute(
                sa.text(
                    "SELECT relation.provenance FROM governance_record_relations relation "
                    "WHERE relation.source_record_id = :backlog_id "
                    "AND relation.target_record_id = :origin_id "
                    "AND relation.relation_type = 'corrects' "
                    "AND relation.status = 'verified'"
                ),
                {"backlog_id": backlog_id, "origin_id": origin_id},
            )
            .mappings()
            .first()
        )
        if relation is None:
            raise RuntimeError(f"Architecture Backlog {backlog_id} is missing its verified correction relation")
        provenance = relation["provenance"]
        if isinstance(provenance, str):
            provenance = json.loads(provenance)
        if not isinstance(provenance, dict) or not isinstance(provenance.get("reason"), str):
            raise RuntimeError(f"Architecture Backlog {backlog_id} correction relation lacks provenance reason")
        if not provenance["reason"].strip():
            raise RuntimeError(f"Architecture Backlog {backlog_id} correction relation lacks provenance reason")


def _assert_claim_evidence_origin(bind: sa.Connection) -> None:
    invalid = bind.execute(
        sa.text(
            "SELECT id FROM claim_evidence WHERE "
            "(source_element_id IS NULL AND governance_record_revision_id IS NULL) "
            "OR (source_element_id IS NOT NULL AND governance_record_revision_id IS NOT NULL) LIMIT 1"
        )
    ).scalar_one_or_none()
    if invalid is not None:
        raise RuntimeError(f"claim evidence {invalid} does not have exactly one origin")


def _assert_evidence_indexes(bind: sa.Connection) -> None:
    """Remove the pre-governance mixed-origin identity index."""

    bind.execute(sa.text("DROP INDEX IF EXISTS uq_claim_evidence_exact_identity"))
    bind.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_claim_evidence_document_location_polarity_run "
            "ON claim_evidence (idea_block_revision_id, source_element_id, "
            "COALESCE(quote_char_start, -1), COALESCE(quote_char_end, -1), polarity, "
            "COALESCE(extraction_run_id, '')) WHERE source_element_id IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_claim_evidence_governance_revision_polarity "
            "ON claim_evidence (idea_block_revision_id, governance_record_revision_id, polarity) "
            "WHERE governance_record_revision_id IS NOT NULL"
        )
    )
