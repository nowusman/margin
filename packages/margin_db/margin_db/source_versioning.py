from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.knowledge_models import ClaimEvidence, ReprocessingDiff
from margin_db.relationship_evidence import aggregate_relationship_evidence
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocumentVersion, SourceElement
from margin_schema.ids import source_version_id


def activate_source_version(session: Session, *, version: SourceDocumentVersion) -> ReprocessingDiff | None:
    if version.status not in {None, "active"}:
        raise ValueError("an immutable source version cannot be reactivated after supersession")
    with session.no_autoflush:
        existing = session.scalar(select(ReprocessingDiff).where(ReprocessingDiff.current_version_id == version.id))
    if existing is not None:
        return existing
    version.status = "active"
    with session.no_autoflush:
        previous_versions = list(
            session.scalars(
                select(SourceDocumentVersion)
                .where(
                    SourceDocumentVersion.source_document_id == version.source_document_id,
                    SourceDocumentVersion.id != version.id,
                    SourceDocumentVersion.status == "active",
                )
                .order_by(SourceDocumentVersion.created_at.desc(), SourceDocumentVersion.id.desc())
            ).all()
        )
    if len(previous_versions) > 1:
        raise ValueError(f"source document {version.source_document_id} has multiple active prior versions")
    if not previous_versions:
        session.add(version)
        session.flush()
        return None
    previous = previous_versions[0]

    # Persist both rows as non-active before switching the new row to active so
    # the database-level one-active-version constraint remains valid.
    version.status = "superseded"
    previous.status = "superseded"
    session.add(version)
    session.flush()
    version.status = "active"
    previous.superseded_by_version_id = version.id
    session.flush()
    supersede_version_knowledge(session, source_version_id=previous.id, superseded_on=datetime.now(UTC).date())
    diff = ReprocessingDiff(
        id=source_version_id({"previous_version_id": previous.id, "current_version_id": version.id}).replace(
            "srcver_", "diff_", 1
        ),
        source_document_id=version.source_document_id,
        previous_version_id=previous.id,
        current_version_id=version.id,
    )
    session.add(diff)
    return diff


def supersede_version_knowledge(session: Session, *, source_version_id: str, superseded_on: date) -> None:
    old_element_ids = set(
        session.scalars(select(SourceElement.id).where(SourceElement.source_version_id == source_version_id)).all()
    )
    if not old_element_ids:
        return

    evidence = list(
        session.scalars(select(ClaimEvidence).where(ClaimEvidence.source_element_id.in_(old_element_ids))).all()
    )
    for item in evidence:
        item.status = "stale"
        item.valid_to = item.valid_to or superseded_on
    session.flush()

    provenance = list(
        session.scalars(
            select(RelationshipProvenance).where(RelationshipProvenance.source_element_id.in_(old_element_ids))
        ).all()
    )
    relationship_ids = {item.relationship_id for item in provenance}
    for item in provenance:
        if item.status in {"candidate", "verified"}:
            item.status = "stale"
        if item.valid_to is None:
            item.valid_to = superseded_on
    session.flush()
    for relationship in session.scalars(
        select(EntityRelationship).where(EntityRelationship.id.in_(relationship_ids))
    ).all():
        aggregate_relationship_evidence(session, relationship)
