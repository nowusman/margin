from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from margin_db.model_catalog_models import ModelConfiguration
from margin_db.model_registry import (
    MODEL_PURPOSES,
    ModelRegistryError,
    ModelSelectionService,
    apply_model_patch,
    configured_active_model,
    normalize_model_purpose,
    public_model_dict,
    snapshot_model,
    validate_model_configuration,
    validate_single_active_model,
)
from margin_embedding.factory import (
    DEFAULT_EMBEDDING_INDEX_VERSION,
    DEFAULT_EMBEDDING_MODEL_ID,
    required_embedding_index_config,
)


def _session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _model(model_id: str, purpose: str, **kwargs) -> ModelConfiguration:
    params = {
        "id": model_id,
        "display_name": model_id,
        "purpose": purpose,
        "runtime": "openai-compatible",
        "model_name": f"{model_id}-name",
        "endpoint_url": "http://provider.local/v1",
        "system_prompt": "Extract governed architecture evidence.",
        "status": "active",
        "capabilities": {},
    }
    params.update(kwargs)
    return ModelConfiguration(**params)


def test_model_selection_service_returns_configured_models_for_all_purposes() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        session.add(_model("extractor", "extraction"))
        session.add(_model("ocr", "vision_ocr", capabilities={"image_input": True}))
        session.add(_model("synthesis", "answer_synthesis"))
        session.commit()

        registry = ModelSelectionService(session).configured_active_registry()

    assert set(registry) == set(MODEL_PURPOSES)
    assert registry["extraction"] is not None
    assert registry["extraction"].id == "extractor"
    assert registry["vision_ocr"] is not None
    assert registry["vision_ocr"].id == "ocr"
    assert registry["answer_synthesis"] is not None
    assert registry["answer_synthesis"].id == "synthesis"


def test_model_registry_rejects_legacy_and_retrieval_purposes() -> None:
    for invalid in ("embedding", "reranking", "answer_generation", "compilation", "inference", "narration"):
        with pytest.raises(ModelRegistryError, match="unsupported model purpose"):
            normalize_model_purpose(invalid)

    embedding = _model("embedder", "embedding")
    with pytest.raises(ModelRegistryError, match="unsupported model purpose"):
        validate_model_configuration(embedding)


def test_model_capabilities_validation() -> None:
    ocr_model = _model("ocr-invalid", "vision_ocr", capabilities={"image_input": False})
    with pytest.raises(ModelRegistryError, match="active vision_ocr model must support image input"):
        validate_model_configuration(ocr_model)

    unknown_cap = _model("extractor-bad", "extraction", capabilities={"unknown_key": True})
    with pytest.raises(ModelRegistryError, match="unsupported model capability: unknown_key"):
        validate_model_configuration(unknown_cap)


def test_public_model_dict_redacts_secret_reference_and_reports_secret_configured() -> None:
    model_with_secret = _model(
        "sec-model", "extraction", secret_reference="env:TEST_LLM_KEY", capabilities={"structured_output": True}
    )
    data = public_model_dict(model_with_secret)
    assert data["secretConfigured"] is True
    assert "secret_reference" not in data
    assert "secretReference" not in data
    assert "api_key" not in data
    assert "apiKey" not in data
    assert data["capabilities"] == {"structured_output": True}

    snapshot = snapshot_model(model_with_secret)
    assert snapshot["secretConfigured"] is True
    assert "secret_reference" not in snapshot
    assert "secretReference" not in snapshot

    model_no_secret = _model("no-sec-model", "extraction", secret_reference=None)
    assert public_model_dict(model_no_secret)["secretConfigured"] is False


def test_apply_model_patch_supports_secret_reference_and_capabilities() -> None:
    model = _model("patch-test", "extraction")
    apply_model_patch(
        model, {"secretReference": "env:SECRET_KEY", "capabilities": {"json_mode": True}, "purpose": "answer_synthesis"}
    )
    assert model.secret_reference == "env:SECRET_KEY"
    assert model.capabilities == {"json_mode": True}
    assert model.purpose == "answer_synthesis"

    with pytest.raises(ModelRegistryError, match="unsupported model capability"):
        apply_model_patch(model, {"capabilities": {"bad_cap": True}})


def test_embedding_index_config_uses_code_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARGIN_EMBEDDING_MODEL_ID", raising=False)
    monkeypatch.delenv("MARGIN_EMBEDDING_INDEX_VERSION", raising=False)

    config = required_embedding_index_config()

    assert config.model_id == DEFAULT_EMBEDDING_MODEL_ID
    assert config.index_version == DEFAULT_EMBEDDING_INDEX_VERSION


def test_model_selection_rejects_ambiguous_active_models() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        session.add(_model("extractor-a", "extraction"))
        session.add(_model("extractor-b", "extraction"))
        session.commit()

        with pytest.raises(ModelRegistryError, match="expected exactly one"):
            configured_active_model(session, "extraction")


def test_single_active_validation_rejects_duplicate_purpose() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        session.add(_model("extractor-a", "extraction"))
        duplicate = _model("extractor-b", "extraction")
        session.add(duplicate)
        session.flush()

        with pytest.raises(ModelRegistryError, match="only one active extraction"):
            validate_single_active_model(session, duplicate)
