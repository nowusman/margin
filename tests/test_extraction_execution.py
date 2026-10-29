from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.extraction_models import ExtractionRun, SpanExtractionAttempt
from margin_db.knowledge_models import IdeaBlock
from margin_db.relationship_models import EntityRelationship
from margin_db.review_models import ReviewTask
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_db.user_models import Domain
from margin_schema.extraction import ExtractorOutput
from tests.helpers.catalog import catalog_entity
from workers.extract.inference import ExtractionRequest, InferenceResult
from workers.extract.pipeline import MarginIdeaBlockPipeline, _specialized_deterministic_output


def _evidence(element_id: str, quote: str | None) -> dict[str, str]:
    return {"source_element_id": element_id, "source_quote": quote or ""}


class SuccessfulInferenceExtractor:
    base_url = "http://127.0.0.1:1/v1"
    api_key: str | None = None
    model = "test-extraction-model"

    def extract_many(self, requests: tuple[ExtractionRequest, ...]) -> tuple[InferenceResult, ...]:
        results = []
        for request in requests:
            exact_quote = next(
                (line for line in reversed(request.text.splitlines()) if line.strip() and "---" not in line),
                request.text,
            )
            output = ExtractorOutput.model_validate(
                {
                    "idea_blocks": [
                        {
                            "statement": request.text,
                            "type": "statement",
                            "confidence": "extracted",
                            "evidence": [_evidence(request.source_element_id, exact_quote)],
                            "entities": [{"name": "Customer Platform", "kind": "system", "role": "subject"}],
                        }
                    ]
                }
            )
            results.append(
                InferenceResult(request=request, output=output, attempt_count=1, input_tokens=24, output_tokens=12)
            )
        return tuple(results)


class PartiallyFailingInferenceExtractor(SuccessfulInferenceExtractor):
    def extract_many(self, requests: tuple[ExtractionRequest, ...]) -> tuple[InferenceResult, ...]:
        successful = super().extract_many(requests[:1])
        failed_request = requests[1]
        failed = InferenceResult(
            request=failed_request,
            output=None,
            attempt_count=2,
            input_tokens=20,
            output_tokens=0,
            warnings=("inference_failed",),
            error="request timed out",
        )
        return (*successful, failed)


class RecordingInferenceExtractor(SuccessfulInferenceExtractor):
    def __init__(self) -> None:
        self.source_element_ids: list[str] = []
        self.texts: list[str] = []

    def extract_many(self, requests: tuple[ExtractionRequest, ...]) -> tuple[InferenceResult, ...]:
        self.source_element_ids.extend(request.source_element_id for request in requests)
        self.texts.extend(request.text for request in requests)
        return super().extract_many(requests)


class GovernanceCandidateInferenceExtractor(SuccessfulInferenceExtractor):
    def extract_many(self, requests: tuple[ExtractionRequest, ...]) -> tuple[InferenceResult, ...]:
        return tuple(
            InferenceResult(
                request=request,
                output=ExtractorOutput.model_validate(
                    {
                        "governance_records": [
                            {
                                "business_key": "ADR-001",
                                "record_type": "adr",
                                "title": "Use PostgreSQL for architecture evidence",
                                "attributes": {"decision": "Use PostgreSQL for architecture evidence."},
                                "evidence": [_evidence(request.source_element_id, request.text)],
                                "proposed_lifecycle_action": "accept",
                            }
                        ]
                    }
                ),
                attempt_count=1,
                input_tokens=24,
                output_tokens=12,
            )
            for request in requests
        )


class EmptyDeterministicExtractor:
    def extract(self, *, elements: list[SourceElement]) -> ExtractorOutput:
        return ExtractorOutput()


class StructuredFirstSpanExtractor:
    def extract(self, *, elements: list[SourceElement]) -> ExtractorOutput:
        element = elements[0]
        return ExtractorOutput.model_validate(
            {
                "idea_blocks": [
                    {
                        "statement": element.text,
                        "type": "structured_table_row",
                        "confidence": "extracted",
                        "evidence": [_evidence(element.id, element.text)],
                        "extraction_method": "deterministic_table",
                    }
                ]
            }
        )


class PartiallyStructuredFirstSpanExtractor:
    def extract(self, *, elements: list[SourceElement]) -> ExtractorOutput:
        element = elements[0]
        quote = "Customer Platform and PostgreSQL are documented together."
        return ExtractorOutput.model_validate(
            {
                "idea_blocks": [
                    {
                        "statement": quote,
                        "type": "statement",
                        "confidence": "extracted",
                        "evidence": [_evidence(element.id, quote)],
                        "extraction_method": "deterministic",
                    }
                ]
            }
        )


class PartiallyStructuredTableExtractor:
    def extract(self, *, elements: list[SourceElement]) -> ExtractorOutput:
        element = elements[0]
        quote = "| Service 0 | Team 0 |"
        return ExtractorOutput.model_validate(
            {
                "idea_blocks": [
                    {
                        "statement": "Service 0 is owned by Team 0.",
                        "type": "structured_table_row",
                        "confidence": "extracted",
                        "evidence": [_evidence(element.id, quote)],
                        "extraction_method": "deterministic_table",
                    }
                ]
            }
        )


class QuarantinedRelationshipExtractor:
    def extract(self, *, elements: list[SourceElement]) -> ExtractorOutput:
        element = elements[0]
        return ExtractorOutput.model_validate(
            {
                "idea_blocks": [
                    {
                        "statement": element.text,
                        "type": "relationship",
                        "confidence": "extracted",
                        "evidence": [_evidence(element.id, element.text)],
                        "extraction_method": "deterministic",
                        "entities": [
                            {"name": "Customer Platform", "kind": "system", "role": "subject"},
                            {"name": "PostgreSQL", "kind": "technology", "role": "object"},
                        ],
                        "unmapped": [
                            {
                                "kind": "relationship",
                                "reason": "unknown_relationship_type",
                                "raw_payload": {
                                    "source": "Customer Platform",
                                    "relation_type": "synchronizes_with",
                                    "target": "PostgreSQL",
                                },
                            }
                        ],
                    }
                ]
            }
        )


def _seed_source(session: Session, *, two_spans: bool = False) -> SourceDocument:
    session.add(Domain(id="domain_1", name="Domain One"))
    session.add(catalog_entity(entity_id="category_1", preferred_label="Design Document", ontology_type="category"))
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
        content_checksum="page-1-checksum",
        classification="text_extracted",
    )
    element1 = SourceElement(
        id="elem_1",
        source_version_id=version.id,
        source_page_id=page.id,
        native_element_id="paragraph:1",
        kind="paragraph",
        ordinal=0,
        text="Customer Platform and PostgreSQL are documented together.",
        checksum="span-1",
        page_number=1,
        char_start=0,
        char_end=58,
    )
    elements = [element1]
    if two_spans:
        element2 = SourceElement(
            id="elem_2",
            source_version_id=version.id,
            source_page_id=page.id,
            native_element_id="paragraph:2",
            kind="paragraph",
            ordinal=1,
            text="Customer Platform publishes CustomerCreated events.",
            checksum="span-2",
            page_number=1,
            char_start=59,
            char_end=111,
        )
        elements.append(element2)
    session.add_all((document, version, page, *elements))
    session.flush()
    return document


def test_hybrid_extraction_reconciles_outputs_without_manufacturing_edges() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        document = _seed_source(session)
        result = MarginIdeaBlockPipeline(
            inference_extractor=SuccessfulInferenceExtractor(), extraction_mode="llm"
        ).run_once(session, source_document_id=document.id)

        run = session.get(ExtractionRun, result.extraction_run_id)
        blocks = tuple(session.scalars(select(IdeaBlock)).all())
        relationships = tuple(session.scalars(select(EntityRelationship)).all())
        attempts = tuple(session.scalars(select(SpanExtractionAttempt)).all())

        assert run is not None
        assert run.status == "completed"
        assert run.extractor == "deterministic_markdown+openai_compatible_inference"
        assert len(attempts) == 1
        assert attempts[0].outcome == "extracted"
        assert blocks and all(block.current_revision.publication_status == "candidate" for block in blocks)
        assert relationships == ()


def test_pipeline_creates_review_only_governance_candidates() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        document = _seed_source(session)
        result = MarginIdeaBlockPipeline(
            extractor=EmptyDeterministicExtractor(),
            inference_extractor=GovernanceCandidateInferenceExtractor(),
            extraction_mode="llm",
            queue_embeddings=True,
        ).run_once(session, source_document_id=document.id)

        task = session.scalar(select(ReviewTask))
        attempt = session.scalar(select(SpanExtractionAttempt))

        assert result.governance_review_candidate_count == 1
        assert task is not None
        assert task.task_type == "governance_record_review"
        assert task.target_type == "governance_record_candidate"
        assert task.metadata_["identity_status"] == "new_business_key"
        assert task.metadata_["matched_record_id"] is None
        assert attempt is not None and attempt.outcome == "extracted"


def test_uploaded_openapi_uses_the_specialized_deterministic_extractor() -> None:
    document = SourceDocument(
        id="src_contract",
        title="Customer API",
        object_key="sources/openapi.yaml",
        source_uri="openapi.yaml",
        media_type="application/yaml",
        sha256="contract-sha",
        domain_id="domain_1",
        category_id="category_1",
    )
    element = SourceElement(
        id="elem_contract",
        source_version_id="ver_contract",
        source_page_id="page_contract",
        native_element_id="contract:1",
        kind="contract",
        text=(
            "openapi: 3.0.0\n"
            "info:\n  title: Customer API\n  version: '1.0'\n"
            "paths:\n  /customers:\n    get:\n      operationId: listCustomers\n"
            "components:\n  securitySchemes:\n    bearerAuth:\n      type: http\n      scheme: bearer\n"
        ),
        checksum="contract-elem-sha",
    )

    prepared = _specialized_deterministic_output(document, [element])

    assert prepared is not None
    output, extractor_name = prepared
    assert extractor_name == "deterministic_contract"
    assert {block.extraction_method for block in output.idea_blocks} == {"deterministic_contract"}
    assert {relationship.relation_type for block in output.idea_blocks for relationship in block.relationships} >= {
        "exposes",
        "secured_by",
    }


def test_partial_inference_failure_is_a_terminal_visible_issue() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        document = _seed_source(session, two_spans=True)
        result = MarginIdeaBlockPipeline(
            extractor=EmptyDeterministicExtractor(),
            inference_extractor=PartiallyFailingInferenceExtractor(),
            extraction_mode="llm",
        ).run_once(session, source_document_id=document.id)

        run = session.get(ExtractionRun, result.extraction_run_id)
        attempts = tuple(
            session.scalars(select(SpanExtractionAttempt).order_by(SpanExtractionAttempt.window_index)).all()
        )
        session.refresh(document)

        assert run is not None
        assert run.status == "completed_with_issues"
        assert run.expected_window_count == run.processed_window_count == 2
        assert [attempt.outcome for attempt in attempts] == ["extracted", "failed"]
        assert attempts[1].attempt_count == 2
        assert "request timed out" in attempts[1].warnings
        assert document.status == "review_required"


def test_unmapped_relationship_prevents_a_false_complete_run() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        document = _seed_source(session)
        result = MarginIdeaBlockPipeline(
            extractor=QuarantinedRelationshipExtractor(), extraction_mode="deterministic"
        ).run_once(session, source_document_id=document.id)

        run = session.get(ExtractionRun, result.extraction_run_id)
        attempt = session.scalar(select(SpanExtractionAttempt))
        session.refresh(document)

        assert run is not None and run.status == "completed_with_issues"
        assert attempt is not None and attempt.outcome == "unsupported"
        assert "quarantined for ontology review" in attempt.warnings[0]
        assert document.status == "review_required"


def test_hybrid_sends_only_residual_spans_to_inference() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        document = _seed_source(session, two_spans=True)
        inference = RecordingInferenceExtractor()
        MarginIdeaBlockPipeline(
            extractor=StructuredFirstSpanExtractor(), inference_extractor=inference, extraction_mode="llm"
        ).run_once(session, source_document_id=document.id)

        assert inference.source_element_ids == ["elem_2"]
        attempts = tuple(
            session.scalars(select(SpanExtractionAttempt).order_by(SpanExtractionAttempt.window_index)).all()
        )
        assert [attempt.outcome for attempt in attempts] == ["extracted", "extracted"]


def test_hybrid_sends_residual_text_from_partially_covered_window_to_inference() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        document = _seed_source(session)
        element = session.get(SourceElement, "elem_1")
        assert element is not None
        element.text += " Customer Platform publishes CustomerCreated events."
        inference = RecordingInferenceExtractor()

        MarginIdeaBlockPipeline(
            extractor=PartiallyStructuredFirstSpanExtractor(), inference_extractor=inference, extraction_mode="llm"
        ).run_once(session, source_document_id=document.id)

        assert inference.source_element_ids == [element.id]
        assert inference.texts == ["Customer Platform publishes CustomerCreated events."]


def test_hybrid_sends_uncovered_windows_from_a_partially_structured_span_to_inference() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        document = _seed_source(session)
        element = session.get(SourceElement, "elem_1")
        assert element is not None
        element.kind = "table"
        element.text = "| System | Owner |\n|---|---|\n" + "\n".join(
            f"| Service {index} | Team {index} |" for index in range(40)
        )
        inference = RecordingInferenceExtractor()

        MarginIdeaBlockPipeline(
            extractor=PartiallyStructuredTableExtractor(),
            inference_extractor=inference,
            extraction_mode="llm",
            max_window_tokens=60,
            neighbor_tokens=0,
        ).run_once(session, source_document_id=document.id)

        attempts = tuple(session.scalars(select(SpanExtractionAttempt)).all())
        assert len(attempts) > 1
        assert len(inference.source_element_ids) == len(attempts)
        assert set(inference.source_element_ids) == {element.id}


def test_pipeline_scopes_semantic_review_discovery_to_source_domain(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    captured: dict[str, object] = {}

    def _capture(session, **kwargs):
        del session
        captured.update(kwargs)
        return []

    monkeypatch.setattr("workers.extract.pipeline.create_semantic_review_tasks", _capture)

    with Session(engine) as session:
        document = _seed_source(session)
        MarginIdeaBlockPipeline().run_once(session, source_document_id=document.id)

    assert captured.get("domain_id") == "domain_1"
