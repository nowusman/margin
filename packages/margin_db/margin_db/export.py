"""Governed export of a domain's canonical records.

An export is a portable snapshot of what the corpus says, so that a team can
take it with them. Two properties matter more than convenience.

First, the export contains only what it says it contains. The bundle is built
from an explicit list of record kinds, so an operational artefact cannot
appear in an archive by accident, and the manifest states exactly what was
included.

Second, the export is deterministic. The same corpus produces the same bytes
and the same content hash, so two exports can be compared, and a manifest can
be checked against the archive it describes.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_auth.permissions import WILDCARD_SCOPE
from margin_db.charter_models import DomainCharter
from margin_db.entity_models import Entity
from margin_db.export_models import EXPORT_STATE_FAILED, EXPORT_STATE_PENDING, EXPORT_STATE_READY, ExportRequest
from margin_db.governance_models import GovernanceRecord, GovernanceRecordRevision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_schema.ids import export_bundle_id

EXPORT_FORMAT_VERSION = "margin-export-v1"
MANIFEST_NAME = "manifest.json"
RECORDS_DIRECTORY = "records"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

RECORD_KINDS: tuple[str, ...] = (
    "charters",
    "entities",
    "idea_blocks",
    "idea_block_revisions",
    "evidence",
    "entity_relationships",
    "relationship_provenance",
    "governance_records",
    "governance_record_revisions",
    "source_documents",
)


@dataclass(frozen=True, slots=True)
class ExportScope:
    """What a reader was permitted to read when they asked for the export.

    The scope is captured at request time and applied when the export is
    composed, so an archive can never contain more than the requester was
    able to read at the moment they asked for it.
    """

    domain_ids: frozenset[str] = frozenset()
    category_ids: frozenset[str] = frozenset()
    sensitivities: frozenset[str] = frozenset()
    governance_record_types: frozenset[str] = frozenset()

    def allows_domain(self, domain_id: str | None) -> bool:
        if WILDCARD_SCOPE in self.domain_ids:
            return bool(domain_id)
        return bool(domain_id) and domain_id in self.domain_ids

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "domain_ids": sorted(self.domain_ids),
            "category_ids": sorted(self.category_ids),
            "sensitivities": sorted(self.sensitivities),
            "governance_record_types": sorted(self.governance_record_types),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExportScope:
        def _values(key: str) -> frozenset[str]:
            values = payload.get(key)
            if not isinstance(values, list):
                return frozenset()
            return frozenset(str(value) for value in values if str(value))

        return cls(
            domain_ids=_values("domain_ids"),
            category_ids=_values("category_ids"),
            sensitivities=_values("sensitivities"),
            governance_record_types=_values("governance_record_types"),
        )


@dataclass(frozen=True, slots=True)
class ExportContent:
    """A domain's exportable content, ready to be written."""

    domain_id: str
    generated_at: str
    records: dict[str, list[dict[str, Any]]]
    content_hash: str
    excluded: tuple[str, ...]

    def manifest(self) -> dict[str, Any]:
        return {
            "format_version": EXPORT_FORMAT_VERSION,
            "domain_id": self.domain_id,
            "generated_at": self.generated_at,
            "content_hash": self.content_hash,
            "record_kinds": {kind: len(self.records.get(kind, [])) for kind in RECORD_KINDS},
            "included_record_kinds": list(RECORD_KINDS),
            "excluded_by_construction": list(self.excluded),
        }

    def archive(self) -> bytes:
        """Render the bundle as a deterministic ZIP archive."""

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            manifest = json.dumps(self.manifest(), ensure_ascii=False, indent=2, sort_keys=True)
            _write(archive, MANIFEST_NAME, manifest)
            for kind in RECORD_KINDS:
                rows = self.records.get(kind, [])
                body = json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True, default=str)
                _write(archive, f"{RECORDS_DIRECTORY}/{kind}.json", body)
        return buffer.getvalue()


EXCLUDED_BY_CONSTRUCTION: tuple[str, ...] = (
    "source file contents",
    "extraction caches",
    "embedding vectors",
    "model configuration and credentials",
    "runtime state and job history",
    "personal notes, saved records and conversations",
    "audit events",
)


def compose_domain_export(session: Session, domain_id: str, scope: ExportScope | None = None) -> ExportContent:
    """Assemble the exportable content of one domain.

    When a scope is supplied, the export contains only what that scope
    permits. The scope is applied through the evidence chain, so a filtered
    export stays internally consistent: a claim is present only when its
    evidence is readable, and a relationship only when both of its endpoints
    and its own provenance survive.
    """

    effective_scope = scope or ExportScope()
    records: dict[str, list[dict[str, Any]]] = {kind: [] for kind in RECORD_KINDS}
    readable_source_ids = _readable_source_ids(session, domain_id, effective_scope)
    readable_element_ids = _readable_element_ids(session, readable_source_ids)
    domain_allowed = effective_scope.allows_domain(domain_id)

    records["charters"] = (
        [
            _row(
                charter,
                (
                    "id",
                    "domain_id",
                    "version",
                    "status",
                    "author_principal_id",
                    "purpose",
                    "scope_inclusions",
                    "scope_exclusions",
                    "terminology",
                    "created_at",
                    "activated_at",
                ),
            )
            for charter in session.scalars(
                select(DomainCharter).where(DomainCharter.domain_id == domain_id).order_by(DomainCharter.version)
            ).all()
        ]
        if domain_allowed
        else []
    )

    records["source_documents"] = (
        [
            _row(
                source, ("id", "title", "domain_id", "category_id", "sensitivity", "status", "created_at", "updated_at")
            )
            for source in session.scalars(
                select(SourceDocument).where(SourceDocument.domain_id == domain_id).order_by(SourceDocument.id)
            ).all()
            if source.id in readable_source_ids
        ]
        if domain_allowed
        else []
    )

    all_domain_block_ids = (
        {row[0] for row in session.execute(select(IdeaBlock.id).where(IdeaBlock.domain_id == domain_id)).all()}
        if domain_allowed
        else set()
    )
    block_ids = (
        {
            row[0]
            for row in session.execute(
                select(IdeaBlock.id)
                .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
                .where(IdeaBlock.domain_id == domain_id, ClaimEvidence.source_element_id.in_(readable_element_ids))
                .distinct()
            ).all()
        }
        if domain_allowed and readable_element_ids
        else set()
    )

    linked_entity_ids = (
        {
            row[0]
            for row in session.execute(
                select(IdeaBlockEntity.entity_id).where(IdeaBlockEntity.idea_block_id.in_(block_ids)).distinct()
            ).all()
        }
        if block_ids
        else set()
    )
    claimed_entity_ids = (
        {
            row[0]
            for row in session.execute(
                select(IdeaBlockEntity.entity_id)
                .where(IdeaBlockEntity.idea_block_id.in_(all_domain_block_ids))
                .distinct()
            ).all()
        }
        if all_domain_block_ids
        else set()
    )
    readable_entity_ids = {
        entity.id
        for entity in session.scalars(select(Entity).where(Entity.domain_id == domain_id).order_by(Entity.id)).all()
        if entity.id in linked_entity_ids or entity.id not in claimed_entity_ids
    }
    records["entities"] = (
        [
            _row(
                entity,
                (
                    "id",
                    "preferred_label",
                    "normalized_label",
                    "ontology_type",
                    "domain_id",
                    "definition",
                    "lifecycle_status",
                    "created_at",
                    "updated_at",
                ),
            )
            for entity in session.scalars(
                select(Entity).where(Entity.id.in_(readable_entity_ids)).order_by(Entity.id)
            ).all()
        ]
        if domain_allowed and readable_entity_ids
        else []
    )
    entity_ids = {row["id"] for row in records["entities"]}

    records["idea_blocks"] = (
        [
            _row(block, ("id", "domain_id", "current_revision_id", "created_at", "updated_at"))
            for block in session.scalars(
                select(IdeaBlock).where(IdeaBlock.id.in_(block_ids)).order_by(IdeaBlock.id)
            ).all()
        ]
        if block_ids
        else []
    )

    records["idea_block_revisions"] = (
        [
            _row(
                revision,
                (
                    "id",
                    "idea_block_id",
                    "revision_number",
                    "statement",
                    "normalized_statement",
                    "type",
                    "confidence",
                    "publication_status",
                    "semantic_key",
                    "valid_from",
                    "valid_to",
                    "created_at",
                ),
            )
            for revision in session.scalars(
                select(IdeaBlockRevision)
                .where(IdeaBlockRevision.idea_block_id.in_(block_ids))
                .order_by(IdeaBlockRevision.id)
            ).all()
        ]
        if block_ids
        else []
    )

    records["evidence"] = (
        [
            _row(
                evidence,
                (
                    "id",
                    "idea_block_id",
                    "idea_block_revision_id",
                    "source_element_id",
                    "source_quote",
                    "confidence",
                    "extraction_method",
                    "status",
                ),
            )
            for evidence in session.scalars(
                select(ClaimEvidence)
                .where(
                    ClaimEvidence.idea_block_id.in_(block_ids),
                    ClaimEvidence.source_element_id.in_(readable_element_ids),
                )
                .order_by(ClaimEvidence.id)
            ).all()
        ]
        if block_ids and readable_element_ids
        else []
    )

    evidenced_relationship_ids = (
        {
            row[0]
            for row in session.execute(
                select(RelationshipProvenance.relationship_id)
                .where(RelationshipProvenance.idea_block_id.in_(block_ids))
                .distinct()
            ).all()
        }
        if block_ids
        else set()
    )
    records["entity_relationships"] = (
        [
            _row(
                relationship,
                (
                    "id",
                    "source_entity_id",
                    "target_entity_id",
                    "relation_type",
                    "confidence",
                    "status",
                    "valid_from",
                    "valid_to",
                    "created_at",
                ),
            )
            for relationship in session.scalars(
                select(EntityRelationship)
                .where(EntityRelationship.source_entity_id.in_(entity_ids))
                .where(EntityRelationship.target_entity_id.in_(entity_ids))
                .order_by(EntityRelationship.id)
            ).all()
            if relationship.id in evidenced_relationship_ids
        ]
        if entity_ids and evidenced_relationship_ids
        else []
    )

    relationship_ids = {row["id"] for row in records["entity_relationships"]}
    records["relationship_provenance"] = (
        [
            _row(
                provenance,
                (
                    "id",
                    "relationship_id",
                    "idea_block_id",
                    "source_element_id",
                    "source_quote",
                    "confidence",
                    "extraction_method",
                    "status",
                ),
            )
            for provenance in session.scalars(
                select(RelationshipProvenance)
                .where(RelationshipProvenance.relationship_id.in_(relationship_ids))
                .order_by(RelationshipProvenance.id)
            ).all()
        ]
        if relationship_ids
        else []
    )

    governance_records = (
        session.scalars(
            select(GovernanceRecord)
            .where(GovernanceRecord.domain_id == domain_id)
            .where(GovernanceRecord.record_type.in_(effective_scope.governance_record_types))
            .order_by(GovernanceRecord.id)
        ).all()
        if domain_allowed and effective_scope.governance_record_types
        else []
    )
    records["governance_records"] = [
        _row(record, ("id", "domain_id", "record_type", "title", "lifecycle_status", "created_at", "updated_at"))
        for record in governance_records
    ]
    governance_ids = {record.id for record in governance_records}
    records["governance_record_revisions"] = (
        [
            _row(revision, ("id", "governance_record_id", "revision_number", "created_at"))
            for revision in session.scalars(
                select(GovernanceRecordRevision)
                .where(GovernanceRecordRevision.governance_record_id.in_(governance_ids))
                .order_by(GovernanceRecordRevision.id)
            ).all()
        ]
        if governance_ids
        else []
    )

    content_hash = _content_hash(records)
    return ExportContent(
        domain_id=domain_id,
        generated_at=datetime.now(UTC).isoformat(),
        records=records,
        content_hash=content_hash,
        excluded=EXCLUDED_BY_CONSTRUCTION,
    )


def _readable_source_ids(session: Session, domain_id: str, scope: ExportScope) -> frozenset[str]:
    """Return the source documents the scope permits.

    A category permission names the category, which is the label a source
    document carries, so the filter is applied to that label. A reader with no
    category grant at all may read no source, exactly as the catalogue decides,
    and a document whose category is no longer an active category is no longer
    a document anyone may read.
    """

    query = (
        select(SourceDocument.id)
        .join(Entity, Entity.id == SourceDocument.category_id)
        .where(SourceDocument.domain_id == domain_id)
        .where(Entity.ontology_type == "category")
        .where(Entity.lifecycle_status == "active")
    )
    if WILDCARD_SCOPE not in scope.category_ids:
        if not scope.category_ids:
            return frozenset()
        query = query.where(SourceDocument.category.in_(scope.category_ids))
    if scope.sensitivities and WILDCARD_SCOPE not in scope.sensitivities:
        query = query.where(SourceDocument.sensitivity.in_(scope.sensitivities))
    return frozenset(row[0] for row in session.execute(query).all())


def _readable_element_ids(session: Session, source_ids: frozenset[str]) -> frozenset[str]:
    """Return the source elements that belong to the readable sources."""

    if not source_ids:
        return frozenset()
    rows = session.execute(
        select(SourceElement.id)
        .join(SourceDocumentVersion, SourceElement.source_version_id == SourceDocumentVersion.id)
        .where(SourceDocumentVersion.source_document_id.in_(source_ids))
    ).all()
    return frozenset(row[0] for row in rows)


def _content_hash(records: dict[str, list[dict[str, Any]]]) -> str:
    """Hash the canonical content of the export, excluding presentation."""

    canonical = json.dumps(
        {kind: records.get(kind, []) for kind in RECORD_KINDS}, ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row(model: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """Render one record as a plain dictionary of the requested fields."""

    row: dict[str, Any] = {}
    for field in fields:
        value = getattr(model, field, None)
        row[field] = value.isoformat() if isinstance(value, datetime) else value
    return row


def _write(archive: zipfile.ZipFile, name: str, body: str) -> None:
    """Write one entry with a fixed timestamp so archives are reproducible."""

    info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, body)


def request_export(session: Session, *, domain_id: str, requested_by: str, scope: ExportScope) -> ExportRequest:
    """Record a request for an export, before any work is done.

    The scope is captured now, while the requester's permissions are known, so
    that the archive can never contain more than they were able to read.
    """

    bundle = ExportRequest(
        id=export_bundle_id(
            {"domain_id": domain_id, "requested_by": requested_by, "requested_at": datetime.now(UTC).isoformat()}
        ),
        domain_id=domain_id,
        requested_by=requested_by,
        state=EXPORT_STATE_PENDING,
        content_hash=None,
        manifest={},
        records={},
        scope=scope.as_dict(),
        error=None,
        created_at=datetime.now(UTC),
        completed_at=None,
    )
    session.add(bundle)
    session.flush()
    return bundle


def complete_export(session: Session, export_id: str) -> ExportRequest:
    """Compose the export and store the result."""

    bundle = session.get(ExportRequest, export_id)
    if bundle is None:
        raise ValueError(f"unknown export: {export_id}")
    composed = compose_domain_export(session, bundle.domain_id, ExportScope.from_dict(bundle.scope or {}))
    bundle.records = composed.records
    bundle.manifest = composed.manifest()
    bundle.content_hash = composed.content_hash
    bundle.state = EXPORT_STATE_READY
    bundle.error = None
    bundle.completed_at = datetime.now(UTC)
    session.flush()
    return bundle


def fail_export(session: Session, export_id: str, error: str) -> ExportRequest:
    """Record that an export could not be produced."""

    bundle = session.get(ExportRequest, export_id)
    if bundle is None:
        raise ValueError(f"unknown export: {export_id}")
    bundle.state = EXPORT_STATE_FAILED
    bundle.error = error[:2_000]
    bundle.completed_at = datetime.now(UTC)
    session.flush()
    return bundle


def load_export(session: Session, export_id: str) -> ExportRequest | None:
    """Return a stored export, if it exists."""

    return session.get(ExportRequest, export_id)


def render_export_archive(bundle: ExportRequest) -> bytes:
    """Render a stored export as the archive a reader downloads."""

    export = ExportContent(
        domain_id=bundle.domain_id,
        generated_at=str(bundle.manifest.get("generated_at", "")),
        records={kind: list(bundle.records.get(kind, [])) for kind in RECORD_KINDS},
        content_hash=bundle.content_hash or "",
        excluded=EXCLUDED_BY_CONSTRUCTION,
    )
    return export.archive()
