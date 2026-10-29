from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.extraction_models import ExtractionRun
from margin_db.source_models import SourceDocumentVersion, SourceElement, SourceElementOutcome


def source_element_coverage(session: Session, *, source_document_id: str | None = None) -> tuple[set[str], set[str]]:
    versions_statement = select(SourceDocumentVersion.id).where(SourceDocumentVersion.status == "active")
    if source_document_id is not None:
        versions_statement = versions_statement.where(SourceDocumentVersion.source_document_id == source_document_id)
    version_ids = tuple(session.scalars(versions_statement).all())
    elements = list(
        session.scalars(
            select(SourceElement).where(
                SourceElement.source_version_id.in_(version_ids), SourceElement.text.is_not(None)
            )
        ).all()
    )
    element_ids = {element.id for element in elements if (element.text or "").strip()}
    if not element_ids:
        return set(), set()
    runs = session.scalars(
        select(ExtractionRun)
        .where(ExtractionRun.source_version_id.in_(version_ids))
        .order_by(ExtractionRun.started_at.desc(), ExtractionRun.id.desc())
    ).all()
    latest_run_ids: dict[str, str] = {}
    for run in runs:
        latest_run_ids.setdefault(run.source_version_id, run.id)
    covered = set(
        session.scalars(
            select(SourceElementOutcome.source_element_id)
            .where(
                SourceElementOutcome.extraction_run_id.in_(tuple(latest_run_ids.values())),
                SourceElementOutcome.source_element_id.in_(element_ids),
                SourceElementOutcome.outcome.in_(("extracted", "verified_no_claim", "intentionally_ignored")),
            )
            .distinct()
        ).all()
    )
    return covered, element_ids - covered
