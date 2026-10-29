from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.extraction_models import ExtractionRun, SpanExtractionAttempt
from margin_db.ingestion_observability import source_observability_metrics
from margin_db.ingestion_receipt import build_ingestion_receipt
from margin_db.review_models import ReviewTask
from margin_db.source_models import (
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourceElementOutcome,
    SourcePage,
)
from margin_db.user_models import Domain
from margin_schema.extraction import ExtractorOutput
from tests.helpers.catalog import catalog_entity
from workers.extract.accounting import (
    create_source_element_triage_tasks,
    pass_verified_no_claim_audit,
    record_failed_extraction_run,
)
from workers.extract.pipeline import MarginIdeaBlockPipeline
from workers.extract.windows import build_extraction_windows


def _columns(table_name: str) -> set[str]:
    return {column.name for column in Base.metadata.tables[table_name].columns}


def test_extraction_accounting_tables_are_registered() -> None:
    assert {"extraction_runs", "span_extraction_attempts"} <= set(Base.metadata.tables)
    assert {
        "id",
        "source_document_id",
        "source_version_id",
        "mode",
        "status",
        "extractor",
        "model_id",
        "prompt_version",
        "prompt_hash",
        "expected_window_count",
        "processed_window_count",
        "input_tokens",
        "output_tokens",
        "warnings",
        "started_at",
        "completed_at",
    } <= _columns("extraction_runs")
    assert {
        "id",
        "extraction_run_id",
        "source_element_id",
        "window_id",
        "window_index",
        "outcome",
        "extractor",
        "model_id",
        "prompt_version",
        "prompt_hash",
        "attempt_count",
        "input_tokens",
        "output_tokens",
        "warnings",
        "output_idea_block_ids",
        "output_entity_ids",
        "output_relationship_ids",
        "context_element_ids",
        "created_at",
    } <= _columns("span_extraction_attempts")


def _source_document() -> tuple[SourceDocument, SourceDocumentVersion, SourcePage, SourceElement]:
    document = SourceDocument(
        id="src_1",
        title="Platform Architecture",
        object_key="sources/platform.md",
        media_type="text/markdown",
        sha256="document-sha",
        domain_id="domain_1",
        category_id="category_1",
    )
    version = SourceDocumentVersion(id="ver_1", source_document_id=document.id, version_sha256="version-sha")
    page = SourcePage(
        id="page_1",
        source_version_id=version.id,
        page_number=1,
        content_checksum="page-checksum",
        classification="text_extracted",
    )
    element = SourceElement(
        id="elem_1",
        source_version_id=version.id,
        source_page_id=page.id,
        native_element_id="paragraph:1",
        kind="paragraph",
        ordinal=0,
        text="The Customer Platform uses PostgreSQL.",
        checksum="span-1",
        page_number=1,
        char_start=0,
        char_end=43,
    )
    return document, version, page, element


def test_extraction_accounting_records_round_trip() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    document, version, page, element = _source_document()

    with Session(engine) as session:
        session.add_all((document, version, page, element))
        run = ExtractionRun(
            id="run_1",
            source_document_id=document.id,
            source_version_id=version.id,
            mode="deterministic_llm",
            status="completed",
            extractor="deterministic_markdown+openai_compatible_inference",
            expected_window_count=1,
            processed_window_count=1,
        )
        session.add(run)
        session.add(
            SpanExtractionAttempt(
                id="attempt_1",
                extraction_run_id=run.id,
                source_element_id=element.id,
                window_id="window_1",
                window_index=0,
                outcome="extracted",
                extractor="deterministic_markdown",
                output_idea_block_ids=["ib_1"],
                context_element_ids=[element.id],
            )
        )
        session.commit()

        stored = session.get(SpanExtractionAttempt, "attempt_1")
        assert stored is not None
        assert stored.outcome == "extracted"
        assert stored.output_idea_block_ids == ["ib_1"]


def test_contextual_windows_are_ordered_bounded_and_include_neighbors() -> None:
    document, version, page, first_elem = _source_document()
    second_elem = SourceElement(
        id="elem_2",
        source_version_id=version.id,
        source_page_id=page.id,
        native_element_id="paragraph:2",
        kind="paragraph",
        ordinal=1,
        text="The platform publishes CustomerCreated events.",
        checksum="span-2",
        page_number=2,
        char_start=45,
        char_end=91,
    )

    windows = build_extraction_windows(
        document=document, version=version, elements=[second_elem, first_elem], max_tokens=80, neighbor_tokens=20
    )

    assert [window.source_element_id for window in windows] == [first_elem.id, second_elem.id]
    assert windows[0].following_context[0].source_element_id == second_elem.id
    assert windows[1].preceding_context[0].source_element_id == first_elem.id
    assert windows[0].document_metadata["title"] == document.title
    assert all(window.estimated_tokens <= 80 for window in windows)


def test_large_table_is_split_into_header_preserving_windows() -> None:
    document, version, page, element = _source_document()
    element.kind = "table"
    element.text = "| System | Owner | Status |\n|---|---|---|\n" + "\n".join(
        f"| Service {index} | Team {index} | Active |" for index in range(30)
    )

    windows = build_extraction_windows(
        document=document, version=version, elements=[element], max_tokens=60, neighbor_tokens=0
    )

    assert len(windows) > 1
    assert all(window.strategy == "table_rows" for window in windows)
    assert all(window.text.startswith("| System | Owner | Status |") for window in windows)
    assert all(window.estimated_tokens <= 60 for window in windows)


def test_visual_spans_use_specialized_windows() -> None:
    document, version, page, element = _source_document()
    element.kind = "figure"
    element.text = "```mermaid\ngraph TD\n  API --> Database\n```"

    diagram = build_extraction_windows(document=document, version=version, elements=[element])
    element.text = "![Deployment topology](topology.png)"
    image = build_extraction_windows(document=document, version=version, elements=[element])

    assert diagram[0].strategy == "diagram_records"
    assert image[0].strategy == "image_ocr"


def test_oversized_specialized_record_is_still_token_bounded() -> None:
    document, version, page, element = _source_document()
    element.kind = "contract"
    element.text = "operation: short\n\n" + ("schema-property " * 100)

    windows = build_extraction_windows(
        document=document, version=version, elements=[element], max_tokens=60, neighbor_tokens=0
    )

    assert len(windows) > 1
    assert all(len(window.text) <= 60 * 4 for window in windows)


def test_pipeline_records_a_terminal_outcome_for_every_window() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    document, version, page, element = _source_document()
    document.metadata_ = {"source_metadata": {"page_count": 1}}

    with Session(engine) as session:
        session.add(Domain(id="domain_1", name="Domain One"))
        session.add(catalog_entity(entity_id="category_1", preferred_label="Design Document", ontology_type="category"))
        session.add_all((document, version, page, element))
        session.flush()

        result = MarginIdeaBlockPipeline().run_once(session, source_document_id=document.id)

        run = session.get(ExtractionRun, result.extraction_run_id)
        attempts = tuple(
            session.scalars(
                select(SpanExtractionAttempt)
                .where(SpanExtractionAttempt.extraction_run_id == result.extraction_run_id)
                .order_by(SpanExtractionAttempt.window_index)
            ).all()
        )
        session.refresh(document)

        assert run is not None
        assert run.status == "completed"
        assert run.expected_window_count == run.processed_window_count == len(attempts) == 1
        assert attempts[0].outcome == "extracted"
        assert attempts[0].output_idea_block_ids
        assert document.status == "knowledge_ready"

        receipt = build_ingestion_receipt(session, source_document_id=document.id)
        assert receipt["overall_status"] == "complete"
        assert receipt["operational"]["outcomes"]["extracted"] == 1
        assert receipt["knowledge"]["candidate_claims"] >= 1

        run.status = "failed"
        session.flush()
        assert build_ingestion_receipt(session, source_document_id=document.id)["overall_status"] == "failed"


def test_receipt_lists_empty_text_vision_quarantined_elements() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    document, version, page, _ = _source_document()
    picture = SourceElement(
        id="elem_picture_quarantined",
        source_version_id=version.id,
        source_page_id=page.id,
        native_element_id="picture:1",
        kind="picture",
        ordinal=1,
        text="",
        checksum="picture-1",
        page_number=1,
        metadata_={
            "quarantined": True,
            "quarantine_reason": "no active vision_ocr model configured",
            "error_kind": "missing_model",
        },
    )
    with Session(engine) as session:
        session.add(Domain(id="domain_1", name="Domain One"))
        session.add(catalog_entity(entity_id="category_1", preferred_label="Design Document", ontology_type="category"))
        session.add_all((document, version, page, picture))
        session.add(
            ReviewTask(
                task_type="vision_quarantine",
                target_type="source_element",
                target_id=picture.id,
                reason="no active vision_ocr model configured",
                status="open",
                priority="medium",
            )
        )
        session.flush()

        receipt = build_ingestion_receipt(session, source_document_id=document.id)
        unresolved = receipt["unresolved_elements"]
        assert isinstance(unresolved, list)
        match = next(item for item in unresolved if item["id"] == picture.id)
        assert match["kind"] == "picture"
        assert match["outcome"] == "quarantined"
        assert "vision_ocr" in str(match["reason"])
        assert match["review_task_id"] is not None


def test_source_element_dispositions_create_idempotent_triage_tasks() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        elem1 = SourceElement(
            id="elem_unsupported", source_version_id="version_1", native_element_id="image:1", kind="image", ordinal=0
        )
        elem2 = SourceElement(
            id="elem_empty", source_version_id="version_1", native_element_id="empty:1", kind="paragraph", ordinal=1
        )
        out1 = SourceElementOutcome(
            id="outcome_quarantined",
            extraction_run_id="run_1",
            source_element_id="elem_unsupported",
            outcome="quarantined",
            reason="OCR unavailable",
            extractor="test",
        )
        out2 = SourceElementOutcome(
            id="outcome_extracted",
            extraction_run_id="run_1",
            source_element_id="elem_empty",
            outcome="extracted",
            extractor="test",
        )
        session.add_all((elem1, elem2, out1, out2))
        session.flush()
        assert create_source_element_triage_tasks(session, source_version_id="version_1") == 1
        assert create_source_element_triage_tasks(session, source_version_id="version_1") == 0
        tasks = session.scalars(select(ReviewTask).where(ReviewTask.task_type == "source_element_triage")).all()
        assert {task.priority for task in tasks} == {"medium"}


def test_no_claim_audit_metrics_and_task_closure_are_consistent() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    document, version, page, element = _source_document()
    with Session(engine) as session:
        session.add_all(
            (
                Domain(id="domain_1", name="Domain One"),
                catalog_entity(entity_id="category_1", preferred_label="Design Document", ontology_type="category"),
                document,
                version,
                page,
                element,
            )
        )
        session.flush()
        run = ExtractionRun(
            id="run_no_claim_audit",
            source_document_id=document.id,
            source_version_id=version.id,
            mode="deterministic_llm",
            status="completed",
            extractor="test",
            expected_window_count=1,
            processed_window_count=1,
        )
        outcome = SourceElementOutcome(
            id="outcome_no_claim_audit",
            extraction_run_id=run.id,
            source_element_id=element.id,
            outcome="verified_no_claim",
            reason="residual extraction found no claim",
            extractor="test",
            verification_method="residual",
            verifier_version="v1",
            audit_status="selected",
        )
        task = ReviewTask(
            task_type="verified_no_claim_audit",
            target_type="source_element_outcome",
            target_id=outcome.id,
            reason="quality audit",
        )
        session.add_all((run, outcome, task))
        session.flush()

        pass_verified_no_claim_audit(session, outcome_id=outcome.id)
        metrics = source_observability_metrics(session, source_document_id=document.id)

        assert task.status == "closed"
        assert task.closed_at is not None
        assert metrics["no_claim_audit_results"] == {"selected": 1, "passed": 1, "pending": 0, "not_selected": 0}


def test_unpersisted_extractor_output_is_terminal_but_not_complete() -> None:
    class UnsupportedQuoteExtractor:
        def extract(self, *, elements, default_category=None) -> ExtractorOutput:
            elem = list(elements)[0]
            return ExtractorOutput.model_validate(
                {
                    "idea_blocks": [
                        {
                            "statement": "Unsupported architecture assertion.",
                            "type": "statement",
                            "confidence": "extracted",
                            "evidence": [
                                {
                                    "source_element_id": elem.id,
                                    "source_quote": "This quote does not occur in the source.",
                                }
                            ],
                        }
                    ]
                }
            )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    document, version, page, element = _source_document()
    with Session(engine) as session:
        session.add_all(
            (
                Domain(id="domain_1", name="Domain One"),
                catalog_entity(entity_id="category_1", preferred_label="Design Document", ontology_type="category"),
                document,
                version,
                page,
                element,
            )
        )
        session.flush()

        result = MarginIdeaBlockPipeline(extractor=UnsupportedQuoteExtractor()).run_once(
            session, source_document_id=document.id
        )
        run = session.get(ExtractionRun, result.extraction_run_id)
        attempt = session.scalar(
            select(SpanExtractionAttempt).where(SpanExtractionAttempt.extraction_run_id == result.extraction_run_id)
        )

        assert run is not None and run.status == "completed_with_issues"
        assert attempt is not None and attempt.outcome == "unsupported"
        assert attempt.output_idea_block_ids == []
        assert document.status == "review_required"
        assert build_ingestion_receipt(session, source_document_id=document.id)["overall_status"] != "complete"


def test_no_claim_windows_create_low_priority_triage_tasks() -> None:
    class NoClaimExtractor:
        def extract(self, *, elements, default_category=None) -> ExtractorOutput:
            return ExtractorOutput(idea_blocks=[])

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    document, version, page, element = _source_document()

    with Session(engine) as session:
        session.add_all(
            (
                Domain(id="domain_1", name="Domain One"),
                catalog_entity(entity_id="category_1", preferred_label="Design Document", ontology_type="category"),
                document,
                version,
                page,
                element,
            )
        )
        session.flush()

        result = MarginIdeaBlockPipeline(extractor=NoClaimExtractor()).run_once(session, source_document_id=document.id)
        attempt = session.scalar(
            select(SpanExtractionAttempt).where(SpanExtractionAttempt.extraction_run_id == result.extraction_run_id)
        )
        task = session.scalar(select(ReviewTask).where(ReviewTask.task_type == "no_claim_window_triage"))
        outcome = session.scalar(
            select(SourceElementOutcome).where(SourceElementOutcome.extraction_run_id == result.extraction_run_id)
        )

        assert attempt is not None and attempt.outcome == "no_claim"
        assert attempt.metadata_["no_claim_reason"] == "no_extractable_claims_found"
        assert task is not None
        assert task.target_id == element.id
        assert task.target_type == "source_element"
        assert task.priority == "low"
        assert outcome is not None
        assert outcome.outcome == "verified_no_claim"
        assert outcome.reason == "extraction produced no supported claim"
        assert build_ingestion_receipt(session, source_document_id=document.id)["overall_status"] == "complete"


def test_failed_pipeline_run_is_durable_and_visible_after_transaction_recovery() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    document, version, page, element = _source_document()

    with Session(engine) as session:
        session.add_all(
            (
                Domain(id="domain_1", name="Domain One"),
                catalog_entity(entity_id="category_1", preferred_label="Design Document", ontology_type="category"),
                document,
                version,
                page,
                element,
            )
        )
        session.flush()
        run = record_failed_extraction_run(
            session,
            source_document_id=document.id,
            mode="deterministic_llm",
            extractor="deterministic_markdown+openai_compatible_inference",
            model_id="extractor-v1",
            prompt_version="prompt-v1",
            prompt_hash_value="prompt-hash",
            max_window_tokens=1_500,
            neighbor_tokens=160,
            error=RuntimeError("compile failed"),
        )

        assert run is not None
        assert run.status == "failed"
        assert run.expected_window_count == 1
        assert run.processed_window_count == 0
        assert document.status == "failed"
        assert document.metadata_["latest_extraction_run_id"] == run.id
