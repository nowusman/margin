from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

InferenceEvent = Literal["chat", "probe", "extract", "document_text_extraction", "vision_ocr", "answer_synthesis"]


class InferenceError(RuntimeError):
    """Base exception for inference client errors."""


class InferenceUnavailableError(InferenceError):
    """Raised when inference endpoint is unavailable or returns an error."""


class InferenceTimeoutError(InferenceUnavailableError):
    """Raised when an inference call times out."""


class InferencePolicyError(ValueError):
    """Raised when an inference endpoint or configuration violates policy."""


@dataclass(frozen=True)
class InferenceUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class InferenceResponse:
    content: str
    usage: InferenceUsage = field(default_factory=InferenceUsage)
    raw_response: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


KNOWN_CAPABILITIES = {
    "text_input",
    "image_input",
    "structured_output",
    "json_mode",
    "supported_mime_types",
    "allowed_domains",
    "allowed_sensitivities",
    "max_request_bytes",
    "max_context_tokens",
    "max_output_tokens",
}


@dataclass(frozen=True)
class ModelCapabilities:
    text_input: bool = True
    image_input: bool = False
    structured_output: bool = False
    json_mode: bool = False
    supported_mime_types: tuple[str, ...] = ("text/plain",)
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None
    allowed_domains: tuple[str, ...] = ()
    allowed_sensitivities: tuple[str, ...] = ()
    max_request_bytes: int | None = None


def validate_capabilities(capabilities: dict[str, Any] | None) -> dict[str, Any]:
    if capabilities is None:
        return {}
    if not isinstance(capabilities, dict):
        raise InferencePolicyError("capabilities must be a dictionary")
    for key, value in capabilities.items():
        if key not in KNOWN_CAPABILITIES:
            raise InferencePolicyError(f"unsupported model capability: {key}")
        if key in {"text_input", "image_input", "structured_output", "json_mode"}:
            if not isinstance(value, bool):
                raise InferencePolicyError(f"capability '{key}' must have boolean value")
        elif key in {"supported_mime_types", "allowed_domains", "allowed_sensitivities"}:
            if not isinstance(value, list | tuple) or not all(isinstance(x, str) for x in value):
                raise InferencePolicyError(f"capability '{key}' must be a list of strings")
        elif key in {"max_context_tokens", "max_output_tokens", "max_request_bytes"}:
            if value is not None and (type(value) is not int or value < 0):
                raise InferencePolicyError(f"capability '{key}' must be a non-negative integer or null")
    return dict(capabilities)
