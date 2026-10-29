from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from margin_db.extraction_models import ExtractionRun, SpanExtractionAttempt
from margin_db.knowledge_models import ClaimEvidence
from margin_db.source_models import SourceElementOutcome, SourcePageOutcome, UnmappedExtraction


def delete_element_accounting(
    session: Session, *, source_version_ids: list[str], source_element_ids: list[str]
) -> None:
    run_ids = list(
        session.scalars(select(ExtractionRun.id).where(ExtractionRun.source_version_id.in_(source_version_ids))).all()
    )
    if run_ids:
        session.execute(delete(SourcePageOutcome).where(SourcePageOutcome.extraction_run_id.in_(run_ids)))
        session.execute(delete(SourceElementOutcome).where(SourceElementOutcome.extraction_run_id.in_(run_ids)))
        session.execute(delete(SpanExtractionAttempt).where(SpanExtractionAttempt.extraction_run_id.in_(run_ids)))
    if source_element_ids:
        session.execute(delete(ClaimEvidence).where(ClaimEvidence.source_element_id.in_(source_element_ids)))
        session.execute(delete(UnmappedExtraction).where(UnmappedExtraction.source_element_id.in_(source_element_ids)))
    if run_ids:
        session.execute(delete(ExtractionRun).where(ExtractionRun.id.in_(run_ids)))
