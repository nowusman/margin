from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from margin_db.embeddings import enqueue_embedding_job, process_embedding_jobs
from margin_db.entity_catalog import create_entity
from margin_db.extraction import ExtractionPersistenceService, _candidate_semantic_rejection_reason
from margin_db.knowledge_models import (
    ClaimEvidence,
    EmbeddingJob,
    EmbeddingRecord,
    IdeaBlock,
    IdeaBlockEntity,
    IdeaBlockRevision,
    SearchMaterializationJob,
)
from margin_db.object_storage import FilesystemObjectStore
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.review_models import ReviewTask
from margin_db.search_materialization import process_search_materialization_jobs
from margin_db.source_ingestion import SourceIndex, SourceIngestionService
from margin_db.source_models import SourceDocumentVersion, SourceElement
from margin_embedding.interfaces import EMBEDDING_DIMENSIONS, EmbeddingIndexConfig
from margin_schema.extraction import CandidateRejectionReason, ExtractedIdeaBlock, ExtractorOutput
from tests.helpers.converters import TestTextDocumentConverter
from tests.helpers.source_scope import seed_source_scope, upload_scoped_source
from tests.retrieval_test_providers import TEST_EMBEDDING_INDEX_VERSION, TEST_EMBEDDING_MODEL_ID, FakeEmbeddingProvider
from workers.extract.inference import (
    ExtractionRequest,
    InferenceInvalidOutputError,
    InferencePolicyError,
    InferenceResult,
    InferenceUnavailableError,
    OpenAICompatibleExtractor,
    chat_completions_url,
)
from workers.extract.pipeline import MarginIdeaBlockPipeline
from workers.extract.prompts import EXTRACTOR_SYSTEM_PROMPT, validate_prompt_output


def _evidence(element_id: str, quote: str) -> dict[str, str]:
    return {"source_element_id": element_id, "source_quote": quote}


def test_candidate_invented_numbers_are_unsupported_inference() -> None:
    candidate = ExtractedIdeaBlock.model_validate(
        {
            "statement": "API gateway timeout is 5000 milliseconds.",
            "type": "fact",
            "confidence": "inferred",
            "evidence": [
                {"source_element_id": "elem_1", "source_quote": "API gateway timeout is documented for callers."}
            ],
            "entities": [],
            "relationships": [],
            "extraction_method": "openai_compatible_inference",
        }
    )

    assert _candidate_semantic_rejection_reason(candidate) == CandidateRejectionReason.UNSUPPORTED_INFERENCE


def test_a_cited_record_title_is_a_reference_and_not_an_unsupported_inference() -> None:
    """A numbered title cites the source; it does not assert a new fact."""

    candidate = ExtractedIdeaBlock.model_validate(
        {
            "statement": "ADR-0001: Use PostgreSQL for Operational Records principle states "
            "Use PostgreSQL as the operational system of record.",
            "type": "decision",
            "confidence": "extracted",
            "evidence": [
                {"source_element_id": "elem_1", "source_quote": "Use PostgreSQL as the operational system of record."}
            ],
            "entities": [],
            "relationships": [],
            "extraction_method": "deterministic_principle",
            "metadata": {"principle_title": "ADR-0001: Use PostgreSQL for Operational Records"},
        }
    )

    assert _candidate_semantic_rejection_reason(candidate) is None


def test_a_number_the_evidence_does_not_support_is_still_an_unsupported_inference() -> None:
    candidate = ExtractedIdeaBlock.model_validate(
        {
            "statement": "ADR-0001: Use PostgreSQL for Operational Records principle states "
            "the ledger settles within 5000 milliseconds.",
            "type": "decision",
            "confidence": "extracted",
            "evidence": [
                {"source_element_id": "elem_1", "source_quote": "Use PostgreSQL as the operational system of record."}
            ],
            "entities": [],
            "relationships": [],
            "extraction_method": "deterministic_principle",
            "metadata": {"principle_title": "ADR-0001: Use PostgreSQL for Operational Records"},
        }
    )

    assert _candidate_semantic_rejection_reason(candidate) == CandidateRejectionReason.UNSUPPORTED_INFERENCE


class EmptyDeterministicExtractor:
    def extract(self, *, elements: list[SourceElement] | list[object]) -> ExtractorOutput:
        return ExtractorOutput()


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _service(tmp_path: Path) -> SourceIngestionService:
    return SourceIngestionService(
        FilesystemObjectStore(tmp_path / "objects"), converter=TestTextDocumentConverter(), source_index=SourceIndex()
    )


def _body_element(service: SourceIngestionService, session: Session, source_document_id: str) -> dict[str, object]:
    return next(
        element
        for element in service.preview(session, source_document_id)["elements"]
        if element["kind"] == "paragraph"
    )


def _foreign_key_session_factory() -> sessionmaker:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record) -> None:
        del connection_record
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_extraction_completion_enqueues_idea_block_embedding_jobs(
    session_factory: sessionmaker, tmp_path: Path
) -> None:
    service = _service(tmp_path)
    with session_factory() as session:
        upload = upload_scoped_source(
            service,
            session,
            filename="embedding.md",
            media_type="text/markdown",
            data=b"# Embedding\n\nPostgreSQL stores architecture evidence.\n",
        )
        element = _body_element(service, session, upload.source_document.id)
        ExtractionPersistenceService().persist_output(
            session,
            {
                "idea_blocks": [
                    {
                        "statement": "PostgreSQL stores architecture evidence.",
                        "type": "fact",
                        "confidence": "extracted",
                        "evidence": [_evidence(element["id"], "PostgreSQL stores architecture evidence.")],
                        "entities": [{"name": "PostgreSQL", "kind": "datastore", "role": "subject"}],
                        "relationships": [],
                    }
                ]
            },
            source_document_id=upload.source_document.id,
        )

        block = session.scalar(select(IdeaBlock))
        job = session.scalar(select(EmbeddingJob).where(EmbeddingJob.target_type == "idea_block_revision"))
        session.commit()

    assert block is not None
    assert job is None

    with session_factory() as session:
        materialized = process_search_materialization_jobs(
            session, embedding_index=EmbeddingIndexConfig(TEST_EMBEDDING_MODEL_ID, TEST_EMBEDDING_INDEX_VERSION)
        )
        completed = process_embedding_jobs(session, provider=FakeEmbeddingProvider())
        record = session.scalar(select(EmbeddingRecord).where(EmbeddingRecord.target_type == "idea_block_revision"))
        session.commit()

    assert materialized == 1
    assert completed == 3
    assert record is not None
    assert record.target_type == "idea_block_revision"
    assert record.target_id == block.current_revision_id
    assert record.index_version == TEST_EMBEDDING_INDEX_VERSION
    assert len(record.embedding) == EMBEDDING_DIMENSIONS

    with session_factory() as session:
        enqueue_embedding_job(
            session,
            target_type="idea_block_revision",
            target_id=block.current_revision_id,
            model_id=TEST_EMBEDDING_MODEL_ID,
            index_version=TEST_EMBEDDING_INDEX_VERSION,
            reason="idea_block_changed",
        )
        session.commit()

    with session_factory() as session:
        completed = process_embedding_jobs(session, provider=FakeEmbeddingProvider())
        jobs = session.scalars(select(EmbeddingJob).order_by(EmbeddingJob.created_at)).all()
        session.commit()

    assert completed == 1
    assert len(jobs) == 4
    assert {job.status for job in jobs} == {"completed"}


def test_extraction_persistence_stages_search_materialization_before_embedding_jobs(
    session_factory: sessionmaker, tmp_path: Path
) -> None:
    service = _service(tmp_path)
    with session_factory() as session:
        upload = upload_scoped_source(
            service,
            session,
            filename="deterministic.md",
            media_type="text/markdown",
            data=b"# Deterministic\n\nPostgreSQL stores architecture evidence.\n",
        )
        element = _body_element(service, session, upload.source_document.id)
        ExtractionPersistenceService().persist_output(
            session,
            {
                "idea_blocks": [
                    {
                        "statement": "PostgreSQL stores architecture evidence.",
                        "type": "fact",
                        "confidence": "extracted",
                        "evidence": [_evidence(element["id"], "PostgreSQL stores architecture evidence.")],
                        "entities": [{"name": "PostgreSQL", "kind": "datastore", "role": "subject"}],
                        "relationships": [],
                    }
                ]
            },
            source_document_id=upload.source_document.id,
        )
        block = session.scalar(select(IdeaBlock))
        job = session.scalar(select(EmbeddingJob).where(EmbeddingJob.target_type == "idea_block_revision"))
        search_job = session.scalar(select(SearchMaterializationJob))

    assert block is not None
    assert job is None
    assert search_job is not None
    assert search_job.idea_block_revision_id == block.current_revision_id
    assert search_job.status == "queued"


def test_reprocess_reuses_immutable_version_without_deleting_knowledge(tmp_path: Path) -> None:
    session_factory = _foreign_key_session_factory()
    service = _service(tmp_path)

    with session_factory() as session:
        create_entity(session, preferred_label="Service A", ontology_type="component", domain_id="OSS")
        create_entity(session, preferred_label="Service B", ontology_type="component", domain_id="OSS")
        upload = upload_scoped_source(
            service,
            session,
            filename="interfaces.md",
            media_type="text/markdown",
            data=b"# Interfaces\n\nService A calls Service B.\n",
        )
        element = _body_element(service, session, upload.source_document.id)
        ExtractionPersistenceService().persist_output(
            session,
            {
                "idea_blocks": [
                    {
                        "statement": "Service A calls Service B.",
                        "type": "relationship",
                        "confidence": "extracted",
                        "evidence": [_evidence(element["id"], "Service A calls Service B.")],
                        "entities": [
                            {"name": "Service A", "kind": "component", "role": "subject"},
                            {"name": "Service B", "kind": "component", "role": "object"},
                        ],
                        "relationships": [
                            {
                                "source": "Service A",
                                "relation_type": "depends_on",
                                "target": "Service B",
                                "confidence": "extracted",
                            }
                        ],
                    }
                ]
            },
            source_document_id=upload.source_document.id,
        )
        idea_block = session.scalar(select(IdeaBlock))
        relationship = session.scalar(select(EntityRelationship))
        claim_evidence = session.scalar(select(ClaimEvidence))
        provenance = session.scalar(select(RelationshipProvenance))
        assert idea_block is not None
        assert relationship is not None
        assert claim_evidence is not None
        assert provenance is not None
        session.add(
            ReviewTask(
                task_type="idea_block_review",
                target_type="idea_block",
                target_id=idea_block.id,
                reason="exercise reprocess cleanup",
            )
        )
        session.add(
            ReviewTask(
                task_type="unrelated_review",
                target_type="entity",
                target_id=idea_block.id,
                reason="same target id but different target type should survive",
            )
        )
        session.flush()

        job = service.create_reprocess_job(session, upload.source_document.id)
        processed = service.process_job(session, job.id)

        assert processed.status == "extracting"
        assert session.scalars(select(IdeaBlock)).all() == [idea_block]
        assert session.scalars(select(EntityRelationship)).all() == [relationship]
        assert session.scalars(select(ClaimEvidence)).all() == [claim_evidence]
        assert session.scalars(select(RelationshipProvenance)).all() == [provenance]
        assert len(session.scalars(select(SourceDocumentVersion)).all()) == 1
        remaining_tasks = session.scalars(select(ReviewTask)).all()
        assert {task.target_type for task in remaining_tasks} == {"idea_block", "entity", "entity_relationship"}
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_extractor_schema_rejects_uncited_output() -> None:
    with pytest.raises(ValueError):
        ExtractorOutput(
            idea_blocks=[
                {
                    "statement": "Margin uses PostgreSQL.",
                    "type": "decision",
                    "confidence": "extracted",
                    "evidence": [_evidence("elem_abc", "")],
                }
            ]
        )


def test_prompt_pack_rejects_hallucinated_or_uncited_json() -> None:
    with pytest.raises(ValueError):
        validate_prompt_output(
            """
            {"idea_blocks":[{"statement":"Unsupported claim","category":"ADR","type":"decision",
            "confidence":"extracted","evidence":[{"source_element_id":"elem_abc","source_quote":""}]}]}
            """
        )


def test_inference_extractor_rejects_non_http_endpoint() -> None:
    with pytest.raises(InferencePolicyError):
        OpenAICompatibleExtractor(base_url="file:///tmp/model", model="test-model")


def test_inference_extractor_accepts_openai_compatible_endpoint() -> None:
    extractor = OpenAICompatibleExtractor(base_url="https://api.example.com/v1", model="test-model")

    assert extractor.base_url == "https://api.example.com/v1"


def test_chat_completions_url_accepts_base_or_full_endpoint() -> None:
    assert chat_completions_url("https://api.example.com/v1") == ("https://api.example.com/v1/chat/completions")
    assert chat_completions_url("https://api.example.com/v1/chat/completions") == (
        "https://api.example.com/v1/chat/completions"
    )


def test_inference_extractor_accepts_openai_compatible_json(caplog: pytest.LogCaptureFixture) -> None:
    observed_payloads: list[dict[str, object]] = []
    response_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "idea_blocks": [
                                {
                                    "statement": "Margin uses PostgreSQL.",
                                    "type": "decision",
                                    "confidence": "extracted",
                                    "evidence": [_evidence("elem_abc", "Margin uses PostgreSQL.")],
                                    "entities": [{"name": "PostgreSQL", "kind": "technology", "role": "context"}],
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            observed_payloads.append(json.loads(self.rfile.read(length).decode("utf-8")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response_payload).encode("utf-8"))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        caplog.set_level("INFO")
        output = OpenAICompatibleExtractor(
            base_url=f"http://127.0.0.1:{server.server_port}/v1", model="test-model", temperature=0.2, max_tokens=1234
        ).extract(source_element_id="elem_abc", text="Margin uses PostgreSQL.")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert output.idea_blocks[0].statement == "Margin uses PostgreSQL."
    messages = observed_payloads[0]["messages"]
    assert isinstance(messages, list)
    assert messages[0] == {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT}
    assert observed_payloads[0]["temperature"] == 0.2
    assert observed_payloads[0]["max_tokens"] == 1234
    interactions = [record.llm_interaction for record in caplog.records if hasattr(record, "llm_interaction")]
    assert interactions
    assert interactions[0]["event"] == "extract"
    assert interactions[0]["request"] == {"redacted": True, "message_count": 2, "max_tokens": 1234}
    assert interactions[0]["response"] == {"redacted": True, "received": True}
    assert "Margin uses PostgreSQL." not in caplog.text
    assert interactions[0]["start_timestamp"]
    assert interactions[0]["end_timestamp"]
    assert '"start_timestamp"' in caplog.records[-1].getMessage()
    assert '"response"' in caplog.records[-1].getMessage()


def test_citation_validation_accepts_normalized_quote_reformulations() -> None:
    from workers.extract.inference import _quote_present

    source = "The Customer Platform\n  depends on PostgreSQL for durable storage."
    assert _quote_present("Customer Platform depends on PostgreSQL", source)
    assert _quote_present("  depends on\nPostgreSQL ", source)
    assert _quote_present("PostgreSQL", source)
    assert not _quote_present("does not depend on MySQL", source)


def _extraction_request(element_id: str, text: str) -> ExtractionRequest:
    return ExtractionRequest(
        window_id=f"window_{element_id}",
        source_element_id=element_id,
        text=text,
        content_type="paragraph",
        page_number=1,
        section_heading="Architecture",
        section_path="Document / Architecture",
        preceding_context=(),
        following_context=(),
        document_metadata={"title": "Margin"},
    )


def test_inference_extractor_rejects_citations_outside_the_window() -> None:
    output = ExtractorOutput.model_validate(
        {
            "idea_blocks": [
                {
                    "statement": "Margin uses PostgreSQL.",
                    "type": "decision",
                    "confidence": "extracted",
                    "evidence": [_evidence("elem_other", "Margin uses PostgreSQL.")],
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="eligible-element set"):
        OpenAICompatibleExtractor._validate_citations(
            output, _extraction_request("elem_abc", "Margin uses PostgreSQL.")
        )


def test_inference_extractor_accepts_citations_from_eligible_record_elements() -> None:
    from dataclasses import replace

    output = ExtractorOutput.model_validate(
        {
            "idea_blocks": [
                {
                    "statement": "Customer Platform uses PostgreSQL.",
                    "type": "fact",
                    "confidence": "extracted",
                    "evidence": [
                        _evidence("elem_caption", "Ownership table:"),
                        _evidence("elem_row", "Customer Platform | PostgreSQL"),
                    ],
                }
            ]
        }
    )
    base_request = _extraction_request("elem_row", "Customer Platform | PostgreSQL")
    request = replace(
        base_request,
        eligible_citable_elements=(
            {
                "source_element_id": "elem_caption",
                "kind": "caption",
                "page_number": 1,
                "text": "Ownership table:",
                "role": "eligible",
                "text_role": "table_caption",
            },
            {
                "source_element_id": "elem_row",
                "kind": "table_row",
                "page_number": 1,
                "text": "Customer Platform | PostgreSQL",
                "role": "primary",
                "text_role": "table_row",
            },
        ),
    )
    OpenAICompatibleExtractor._validate_citations(output, request)


def test_inference_extractor_rejects_quote_not_present_in_cited_element() -> None:
    output = ExtractorOutput.model_validate(
        {
            "idea_blocks": [
                {
                    "statement": "Margin uses PostgreSQL.",
                    "type": "decision",
                    "confidence": "extracted",
                    "evidence": [_evidence("elem_abc", "Margin uses PostgreSQL.")],
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="source_quote"):
        OpenAICompatibleExtractor._validate_citations(
            output, _extraction_request("elem_abc", "Margin stores source elements.")
        )


def test_inference_extractor_returns_one_result_per_window_and_retries_failures() -> None:
    requests = (
        _extraction_request("elem_abc", "Margin uses PostgreSQL."),
        _extraction_request("elem_def", "Margin stores source elements."),
    )

    class RetryingExtractor(OpenAICompatibleExtractor):
        calls: dict[str, int] = {}

        def _request_once(self, request: ExtractionRequest) -> tuple[ExtractorOutput, int, int]:
            self.calls[request.window_id] = self.calls.get(request.window_id, 0) + 1
            if request.source_element_id == "elem_def" and self.calls[request.window_id] == 1:
                raise InferenceInvalidOutputError("invalid output")
            output = ExtractorOutput.model_validate(
                {
                    "idea_blocks": [
                        {
                            "statement": request.text,
                            "type": "statement",
                            "confidence": "extracted",
                            "evidence": [_evidence(request.source_element_id, request.text)],
                        }
                    ]
                }
            )
            return output, 10, 5

    output = RetryingExtractor(base_url="https://api.example.com/v1", model="test-model").extract_many(requests)

    assert [result.request.source_element_id for result in output] == ["elem_abc", "elem_def"]
    assert [result.attempt_count for result in output] == [1, 2]
    assert all(result.succeeded for result in output)


def test_inference_extractor_reports_partial_failure_without_skipping_the_window() -> None:
    requests = (
        _extraction_request("elem_abc", "Margin uses PostgreSQL."),
        _extraction_request("elem_def", "Margin stores source elements."),
    )

    class RetryingExtractor(OpenAICompatibleExtractor):
        def _request_once(self, request: ExtractionRequest) -> tuple[ExtractorOutput, int, int]:
            if request.source_element_id == "elem_def":
                raise InferenceUnavailableError("request timed out")
            output = ExtractorOutput.model_validate(
                {
                    "idea_blocks": [
                        {
                            "statement": request.text,
                            "type": "statement",
                            "confidence": "extracted",
                            "evidence": [_evidence(request.source_element_id, request.text)],
                        }
                    ]
                }
            )
            return output, 10, 5

    output = RetryingExtractor(base_url="https://api.example.com/v1", model="test-model").extract_many(requests)

    assert output[0].succeeded is True
    assert output[1].succeeded is False
    assert output[1].attempt_count == 2
    assert output[1].error == "request timed out"


def test_llm_pipeline_keeps_inference_blocks_candidate_and_entity_linked(tmp_path: Path, session_factory) -> None:
    service = _service(tmp_path)

    class FakeInferenceExtractor:
        base_url = "http://127.0.0.1:1/v1"
        api_key: str | None = None
        model = "test-model"

        def extract_many(self, requests: tuple[ExtractionRequest, ...]) -> tuple[InferenceResult, ...]:
            request = requests[0]
            output = ExtractorOutput.model_validate(
                {
                    "idea_blocks": [
                        {
                            "statement": "Margin uses PostgreSQL.",
                            "type": "statement",
                            "confidence": "extracted",
                            "evidence": [
                                _evidence(
                                    next(
                                        element["source_element_id"]
                                        for element in request.eligible_citable_elements
                                        if "Margin uses PostgreSQL." in element["text"]
                                    ),
                                    "Margin uses PostgreSQL.",
                                )
                            ],
                            "entities": [
                                {"name": "Margin", "kind": "system", "role": "subject"},
                                {"name": "PostgreSQL", "kind": "technology", "role": "object"},
                            ],
                        }
                    ]
                }
            )
            return (InferenceResult(request=request, output=output, attempt_count=1, input_tokens=10, output_tokens=5),)

    with session_factory() as session:
        create_entity(session, preferred_label="Margin", ontology_type="system", domain_id="BSS")
        create_entity(session, preferred_label="PostgreSQL", ontology_type="technology", domain_id="BSS")
        upload = upload_scoped_source(
            service,
            session,
            filename="llm.md",
            media_type="text/markdown",
            data=b"# LLM\n\nMargin uses PostgreSQL.\n",
            domain_id="BSS",
            category="Design Document",
            sensitivity="internal",
        )
        MarginIdeaBlockPipeline(
            extractor=EmptyDeterministicExtractor(), inference_extractor=FakeInferenceExtractor(), extraction_mode="llm"
        ).run_once(session, source_document_id=upload.source_document.id)
        block = session.scalar(select(IdeaBlock))
        link = session.scalar(select(IdeaBlockEntity))
        relationship = session.scalar(select(EntityRelationship))
        revision = session.get(IdeaBlockRevision, block.current_revision_id) if block is not None else None

        assert block is not None
        assert revision is not None
        assert revision.publication_status == "candidate"
    assert revision.process == "openai_compatible_inference"
    assert link is not None
    assert link.idea_block_id == block.id
    assert relationship is None
