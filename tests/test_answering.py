from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from margin_db.model_catalog_models import ModelConfiguration
from margin_inference import InferenceClient, InferenceResponse, InferenceUsage
from margin_retrieval.answering import CatalogAnswerSynthesizer
from margin_retrieval.interfaces import Citation, EvidenceItem, RetrievalScope


def _session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _item() -> EvidenceItem:
    return EvidenceItem(
        item_id="idea_block:block-1:revision-1",
        kind="idea_block",
        stage="rrf",
        score=1.0,
        text="The gateway requires mutual TLS for internal API traffic.",
        citations=(
            Citation(
                citation_id="citation-1",
                idea_block_id="block-1",
                idea_block_revision_id="revision-1",
                source_document_id="source-1",
                source_version_id="version-1",
                source_element_id="element-1",
                quote="The gateway requires mutual TLS for internal API traffic.",
            ),
        ),
        metadata={"idea_block_id": "block-1", "idea_block_revision_id": "revision-1"},
    )


def _active_model() -> ModelConfiguration:
    return ModelConfiguration(
        id="answer-model",
        display_name="Answer model",
        purpose="answer_synthesis",
        runtime="openai-compatible",
        model_name="answer-model-v1",
        endpoint_url="http://provider.local/v1",
        status="active",
        last_test_status="healthy",
        capabilities={"structured_output": True},
    )


def test_synthesis_uses_catalog_model_and_redacts_prompt(monkeypatch) -> None:
    session = _session()
    session.add(_active_model())
    session.commit()
    observed: dict[str, object] = {}

    def fake_chat(_, **kwargs):
        observed.update(kwargs)
        return InferenceResponse(
            content=(
                '{"answer":"The gateway requires mutual TLS for internal API traffic. [citation-1]",'
                '"citation_ids":["citation-1"]}'
            ),
            usage=InferenceUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
            latency_ms=7,
        )

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_chat)

    result = CatalogAnswerSynthesizer().synthesize(
        session,
        query="What protects internal APIs?",
        items=(_item(),),
        scope=RetrievalScope(domain_ids=("architecture",), sensitivities=("internal",)),
    )

    assert result.answer_markdown is not None
    assert result.citation_ids == ("citation-1",)
    assert result.unavailable_reason is None
    assert result.validation == "valid"
    assert observed["event"] == "answer_synthesis"
    assert observed["redact_content"] is True
    assert observed["response_format"]["type"] == "json_schema"
    assert observed["response_format"]["json_schema"]["strict"] is True
    assert observed["response_format"]["json_schema"]["schema"]["additionalProperties"] is False


def test_synthesis_returns_search_only_for_invalid_model_response(monkeypatch) -> None:
    session = _session()
    session.add(_active_model())
    session.commit()

    def fake_chat(_, **_kwargs):
        return InferenceResponse(
            content='{"answer":"Unsupported claim [unknown]","citation_ids":["unknown"]}', latency_ms=4
        )

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_chat)

    result = CatalogAnswerSynthesizer().synthesize(
        session, query="What protects internal APIs?", items=(_item(),), scope=RetrievalScope()
    )

    assert result.answer_markdown is None
    assert result.unavailable_reason == "answer_validation_failed"


def test_synthesis_requires_healthy_structured_model() -> None:
    session = _session()
    model = _active_model()
    model.last_test_status = "failed"
    model.capabilities = {}
    session.add(model)
    session.commit()

    result = CatalogAnswerSynthesizer().synthesize(
        session, query="What protects internal APIs?", items=(_item(),), scope=RetrievalScope()
    )

    assert result.answer_markdown is None
    assert result.unavailable_reason == "answer_model_unhealthy"


def test_synthesis_requires_text_input_capability() -> None:
    session = _session()
    model = _active_model()
    model.capabilities = {"structured_output": True, "text_input": False}
    session.add(model)
    session.commit()

    result = CatalogAnswerSynthesizer().synthesize(
        session, query="What protects internal APIs?", items=(_item(),), scope=RetrievalScope()
    )

    assert result.answer_markdown is None
    assert result.unavailable_reason == "answer_model_not_authorized"


def test_synthesis_rejects_model_scope_when_caller_has_all_domains() -> None:
    session = _session()
    model = _active_model()
    model.capabilities = {"structured_output": True, "allowed_domains": ["architecture"]}
    session.add(model)
    session.commit()

    result = CatalogAnswerSynthesizer().synthesize(
        session,
        query="What protects internal APIs?",
        items=(_item(),),
        scope=RetrievalScope(all_domain_ids_authorized=True, sensitivities=("internal",)),
    )

    assert result.answer_markdown is None
    assert result.unavailable_reason == "answer_model_not_authorized"
