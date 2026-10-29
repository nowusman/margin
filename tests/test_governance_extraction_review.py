from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.governance_extraction_review import create_governance_review_tasks
from margin_db.governance_models import GovernanceRecord, GovernanceRecordRevision
from margin_db.review_bundles import ReviewBundleError, build_review_bundle, decide_review_bundle
from margin_db.review_models import ReviewTask
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_db.user_models import Domain
from margin_schema.extraction import ExtractedGovernanceRecordCandidate


def _seed_source(session: Session) -> SourceDocument:
    session.add(Domain(id="domain_1", name="Domain One"))
    document = SourceDocument(
        id="src_1",
        title="Architecture Decision",
        object_key="sources/adr-001.md",
        media_type="text/markdown",
        sha256="source-sha",
        domain_id="domain_1",
        category_id="category_1",
    )
    version = SourceDocumentVersion(id="ver_1", source_document_id=document.id, version_sha256="version-sha")
    page = SourcePage(
        id="page_1",
        source_version_id=version.id,
        page_number=1,
        content_checksum="page-sha",
        classification="text_extracted",
    )
    session.add_all(
        (
            document,
            version,
            page,
            SourceElement(
                id="elem_1",
                source_version_id=version.id,
                source_page_id=page.id,
                native_element_id="paragraph:1",
                kind="paragraph",
                ordinal=0,
                text="ADR-001: Use PostgreSQL for architecture evidence.",
                checksum="element-sha",
                page_number=1,
                char_start=0,
                char_end=53,
            ),
        )
    )
    session.flush()
    return document


def _candidate(
    *, business_key: str | None = "ADR-001", title: str = "Use PostgreSQL"
) -> ExtractedGovernanceRecordCandidate:
    return ExtractedGovernanceRecordCandidate.model_validate(
        {
            "business_key": business_key,
            "record_type": "adr",
            "title": title,
            "attributes": {
                "context": "Architecture evidence needs durable storage.",
                "decision": "Use PostgreSQL for architecture evidence.",
            },
            "evidence": [
                {"source_element_id": "elem_1", "source_quote": "ADR-001: Use PostgreSQL for architecture evidence."}
            ],
            "proposed_lifecycle_action": "accept",
        }
    )


def test_governance_candidate_matching_is_exact_and_review_only() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        document = _seed_source(session)
        record = GovernanceRecord(
            id="gov_1",
            business_key="ADR-001",
            record_type="adr",
            title="Existing decision",
            domain_id="domain_1",
            lifecycle_status="proposed",
            attributes={"context": "Old context", "decision": "Old decision"},
        )
        session.add(record)
        session.flush()

        tasks = create_governance_review_tasks(
            session, candidates=[_candidate()], source_document_id=document.id, domain_id=document.domain_id
        )
        repeated = create_governance_review_tasks(
            session, candidates=[_candidate()], source_document_id=document.id, domain_id=document.domain_id
        )

        assert len(tasks) == len(repeated) == 1
        assert session.scalars(select(GovernanceRecord)).all() == [record]
        assert session.scalars(select(GovernanceRecordRevision)).all() == []
        assert record.title == "Existing decision"
        assert record.lifecycle_status == "proposed"
        assert session.scalars(select(ReviewTask)).all() == [tasks[0]]
        assert tasks[0].metadata_["identity_status"] == "matched"
        assert tasks[0].metadata_["matched_record_id"] == record.id

        bundle = build_review_bundle(session, tasks[0].id)
        governance_review = bundle["governance_review"]
        assert isinstance(governance_review, dict)
        assert governance_review["matched_record"]["id"] == record.id
        assert governance_review["proposed_lifecycle_action"] == "accept"
        assert governance_review["source_elements"][0]["source_element_id"] == "elem_1"
        assert {item["field"] for item in governance_review["changed_fields"]} >= {
            "attributes.context",
            "attributes.decision",
            "title",
        }

        with pytest.raises(ReviewBundleError, match="authorized governance record operation"):
            decide_review_bundle(
                session, task_id=tasks[0].id, decision="approved", actor_id="reviewer", resolution="approve"
            )


def test_missing_business_key_never_matches_a_record_by_title() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        document = _seed_source(session)
        session.add(
            GovernanceRecord(
                id="gov_1",
                business_key="ADR-001",
                record_type="adr",
                title="Use PostgreSQL",
                domain_id="domain_1",
                lifecycle_status="proposed",
                attributes={},
            )
        )
        session.flush()

        task = create_governance_review_tasks(
            session,
            candidates=[_candidate(business_key=None)],
            source_document_id=document.id,
            domain_id=document.domain_id,
        )[0]

        assert task.metadata_["identity_status"] == "missing_business_key"
        assert task.metadata_["matched_record_id"] is None
        assert len(session.scalars(select(GovernanceRecord)).all()) == 1


def test_governance_candidate_rejects_foreign_source_evidence() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        document = _seed_source(session)
        invalid = _candidate()
        invalid = invalid.model_copy(
            update={"evidence": [invalid.evidence[0].model_copy(update={"source_element_id": "elem_missing"})]}
        )

        with pytest.raises(ValueError, match="unknown or foreign source element"):
            create_governance_review_tasks(
                session, candidates=[invalid], source_document_id=document.id, domain_id=document.domain_id
            )

        assert session.scalars(select(ReviewTask)).all() == []
