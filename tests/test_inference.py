from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from margin_inference import (
    InferenceClient,
    InferencePolicyError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    chat_completions_url,
    is_http_endpoint,
    resolve_secret,
    validate_capabilities,
)


def test_resolve_secret_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_secret(None) is None
    assert resolve_secret("") is None
    assert resolve_secret("   ") is None

    # Environment variable reference
    monkeypatch.setenv("TEST_LLM_KEY", "secret-from-env")
    assert resolve_secret("env:TEST_LLM_KEY") == "secret-from-env"

    monkeypatch.setenv("TEST_LLM_KEY_PLAIN", "secret-from-plain-reference")
    assert resolve_secret("TEST_LLM_KEY_PLAIN") == "secret-from-plain-reference"

    with pytest.raises(InferencePolicyError, match="referenced environment variable"):
        resolve_secret("sk-12345")

    # Missing environment variable
    monkeypatch.delenv("MISSING_LLM_KEY", raising=False)
    with pytest.raises(InferencePolicyError, match="referenced environment variable"):
        resolve_secret("env:MISSING_LLM_KEY")

    # Unsupported scheme
    with pytest.raises(InferencePolicyError, match="unsupported secret reference scheme"):
        resolve_secret("vault:secret-key")


def test_validate_capabilities() -> None:
    assert validate_capabilities(None) == {}
    assert validate_capabilities({}) == {}
    assert validate_capabilities({"structured_output": True, "image_input": False}) == {
        "structured_output": True,
        "image_input": False,
    }

    # Non-dict
    with pytest.raises(InferencePolicyError, match="capabilities must be a dictionary"):
        validate_capabilities("not-a-dict")

    # Unsupported capability key
    with pytest.raises(InferencePolicyError, match="unsupported model capability: foo"):
        validate_capabilities({"foo": True})

    # Non-boolean capability value
    with pytest.raises(InferencePolicyError, match="must have boolean value"):
        validate_capabilities({"structured_output": "yes"})


def test_inference_client_init_and_url_formatting() -> None:
    # Invalid url schemes
    with pytest.raises(InferencePolicyError, match="HTTP"):
        InferenceClient(base_url="ftp://example.com", model="test")
    with pytest.raises(InferencePolicyError, match="HTTP"):
        InferenceClient(base_url="", model="test")

    client = InferenceClient(
        base_url="http://host.docker.internal:11434/v1", model="minimax-m3:cloud", secret_reference="env:TEST_KEY"
    )
    assert client.base_url == "http://host.docker.internal:11434/v1"
    assert client.model == "minimax-m3:cloud"
    assert is_http_endpoint("http://example.com")
    assert is_http_endpoint("https://example.com")
    assert not is_http_endpoint("file:///path")

    assert chat_completions_url("http://host:11434/v1") == "http://host:11434/v1/chat/completions"
    assert chat_completions_url("http://host:11434/v1/chat/completions") == "http://host:11434/v1/chat/completions"


def test_inference_client_probe_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = InferenceClient(base_url="http://mock.provider:8080/v1", model="test-model")

    models_response = MagicMock()
    models_response.status = 200
    models_response.read.return_value = json.dumps({"data": [{"id": "test-model"}, {"id": "other-model"}]}).encode(
        "utf-8"
    )
    chat_response = MagicMock()
    chat_response.status = 200
    chat_response.read.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

    from margin_inference import client as client_module

    opener = MagicMock()
    opener.open.side_effect = [models_response, chat_response]
    models_response.__enter__.return_value = models_response
    chat_response.__enter__.return_value = chat_response
    monkeypatch.setattr(client_module, "_OPENER", opener)

    status, latency, detail = client.probe()
    assert status == "healthy"
    assert "responded" in detail


def test_inference_client_probe_model_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = InferenceClient(base_url="http://mock.provider:8080/v1", model="missing-model")

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({"data": [{"id": "other-model"}]}).encode("utf-8")

    from margin_inference import client as client_module

    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = mock_response
    monkeypatch.setattr(client_module, "_OPENER", opener)

    status, latency, detail = client.probe()
    assert status == "failed"
    assert "configured model was not listed by provider" in detail


def test_inference_client_chat_completions_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROD_SECRET", "super-secret-token")
    client = InferenceClient(
        base_url="http://mock.provider:8080/v1", model="test-model", secret_reference="env:PROD_SECRET"
    )

    response_body = {
        "choices": [{"message": {"content": "Generated completion text."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps(response_body).encode("utf-8")

    from margin_inference import client as client_module

    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = mock_response
    monkeypatch.setattr(client_module, "_OPENER", opener)

    res = client.chat_completions(
        messages=[{"role": "user", "content": "hello"}], max_tokens=100, response_format={"type": "json_object"}
    )

    assert res.content == "Generated completion text."
    assert res.usage.prompt_tokens == 10
    assert res.usage.completion_tokens == 5
    assert res.usage.total_tokens == 15

    # Check that request included Authorization header
    req_sent = opener.open.call_args[0][0]
    assert req_sent.headers["Authorization"] == "Bearer super-secret-token"
    assert json.loads(req_sent.data)["response_format"] == {"type": "json_object"}


def test_inference_client_redacts_sensitive_interaction_content(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = InferenceClient(base_url="http://mock.provider:8080/v1", model="test-model")
    response_body = {
        "choices": [{"message": {"content": "restricted answer"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    from margin_inference import client as client_module

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps(response_body).encode("utf-8")
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = mock_response
    monkeypatch.setattr(client_module, "_OPENER", opener)

    with caplog.at_level("INFO", logger="margin_inference.client"):
        client.chat_completions(messages=[{"role": "user", "content": "restricted evidence"}], redact_content=True)

    interactions = [record.llm_interaction for record in caplog.records if hasattr(record, "llm_interaction")]
    assert len(interactions) == 1
    assert interactions[0]["request"] == {"redacted": True, "message_count": 1, "max_tokens": None}
    assert interactions[0]["response"] == {"redacted": True, "received": True}
    assert "restricted evidence" not in caplog.text
    assert "restricted answer" not in caplog.text


def test_inference_client_chat_completions_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    client = InferenceClient(base_url="http://mock.provider:8080/v1", model="test-model")

    from margin_inference import client as client_module

    opener = MagicMock()
    opener.open.side_effect = TimeoutError("Connection timed out")
    monkeypatch.setattr(client_module, "_OPENER", opener)

    with pytest.raises(InferenceTimeoutError, match="timed out"):
        client.chat_completions(messages=[{"role": "user", "content": "hello"}])


def test_inference_client_chat_completions_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.error import HTTPError

    client = InferenceClient(base_url="http://mock.provider:8080/v1", model="test-model")

    from margin_inference import client as client_module

    opener = MagicMock()
    opener.open.side_effect = HTTPError(
        url="http://mock.provider:8080/v1/chat/completions", code=503, msg="Service Unavailable", hdrs={}, fp=None
    )
    monkeypatch.setattr(client_module, "_OPENER", opener)

    with pytest.raises(InferenceUnavailableError, match="HTTP 503"):
        client.chat_completions(messages=[{"role": "user", "content": "hello"}])
