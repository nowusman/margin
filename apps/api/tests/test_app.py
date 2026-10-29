from __future__ import annotations

import sys
import types
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import margin_retrieval.local_runtime as local_runtime
from app.main import create_app
from margin_db.base import Base
from margin_retrieval.adapters import CrossEncoderRerankerProvider
from margin_retrieval.local_runtime import (
    DEFAULT_RERANKER_MODEL_ID,
    DEFAULT_RERANKER_PROVIDER_ID,
    build_retrieval_service,
    local_reranker_provider,
)


def _app_state(client: TestClient) -> Any:
    return cast(Any, client.app).state


def _client_without_database() -> TestClient:
    app = create_app()
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        engine.dispose()
    app.state.engine = None
    app.state.session_factory = None
    return TestClient(app)


def _client_with_database() -> TestClient:
    client = _client_without_database()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    state = _app_state(client)
    state.session_factory = sessionmaker(bind=engine)
    state.converter_dependency_status = "ready"
    return client


def _unavailable_session_factory() -> object:
    raise SQLAlchemyError("database unavailable")


def test_health_endpoint_boots() -> None:
    client = _client_without_database()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "margin-api"}


def test_ready_endpoint_declares_baseline_architecture() -> None:
    client = _client_without_database()

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()["detail"]
    assert body["system_of_record"] == "postgresql"
    assert body["object_store_bucket"]
    assert "inference_enabled" not in body
    assert body["database"] == "unavailable"
    assert body["release_status"] == "blocked"


def test_ready_endpoint_requires_database_and_converter_health() -> None:
    client = _client_with_database()

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["database"] == "ready"
    assert response.json()["release_status"] == "ready"


def test_ready_endpoint_is_reachable_when_database_connection_fails() -> None:
    client = _client_without_database()
    state = _app_state(client)
    state.session_factory = _unavailable_session_factory
    state.converter_dependency_status = "ready"

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["database"] == "unavailable"


def test_app_mounts_source_ingestion_routes() -> None:
    client = _client_without_database()

    response = client.get("/api/admin/ingestion-jobs", headers={"x-margin-groups": "margin-admin"})

    assert response.status_code == 503
    assert response.json()["detail"] == "source ingestion database is not configured"


def test_retrieval_service_builds_from_local_runtime() -> None:
    assert build_retrieval_service() is not None


def test_retrieval_service_keeps_lexical_path_when_optional_models_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARGIN_RETRIEVAL_DENSE_ENABLED", "true")
    monkeypatch.setenv("MARGIN_RETRIEVAL_RERANK_ENABLED", "true")

    def fail_dense() -> None:
        raise RuntimeError("dense")

    def fail_reranker() -> None:
        raise RuntimeError("rerank")

    monkeypatch.setattr(local_runtime, "create_embedding_provider", fail_dense)
    monkeypatch.setattr(local_runtime, "local_reranker_provider", fail_reranker)

    service = build_retrieval_service()

    assert service is not None


def test_local_reranker_provider_uses_code_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeCrossEncoderRerankerProvider:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.delenv("MARGIN_RERANKER_MODEL_ID", raising=False)
    monkeypatch.delenv("MARGIN_RERANKER_PROVIDER_ID", raising=False)
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    monkeypatch.setattr(local_runtime, "CrossEncoderRerankerProvider", FakeCrossEncoderRerankerProvider)

    provider = local_reranker_provider()

    assert provider is not None
    assert captured["model_id"] == DEFAULT_RERANKER_MODEL_ID
    assert captured["provider_id"] == DEFAULT_RERANKER_PROVIDER_ID
    assert local_runtime.os.environ["HF_HUB_DISABLE_XET"] == "1"


def test_cross_encoder_reranker_uses_supported_cache_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeCrossEncoder:
        def __init__(self, model_id: str, **kwargs: object) -> None:
            captured["model_id"] = model_id
            captured.update(kwargs)

    fake_module = types.SimpleNamespace(CrossEncoder=FakeCrossEncoder)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    provider = CrossEncoderRerankerProvider(
        provider_id="local-cross-encoder",
        model_id="test-reranker",
        model_cache_dir="/models/sentence-transformers",
        max_length=256,
    )

    assert provider is not None
    assert captured == {
        "model_id": "test-reranker",
        "tokenizer_args": {"cache_dir": "/models/sentence-transformers"},
        "automodel_args": {"cache_dir": "/models/sentence-transformers"},
        "max_length": 256,
    }
