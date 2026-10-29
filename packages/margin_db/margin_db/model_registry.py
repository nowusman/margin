from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.model_catalog_models import ModelConfiguration
from margin_inference import InferencePolicyError, is_http_endpoint, validate_capabilities

ModelPurpose = Literal["extraction", "vision_ocr", "answer_synthesis", "semantic_resolution"]
MODEL_PURPOSES: tuple[ModelPurpose, ...] = ("extraction", "vision_ocr", "answer_synthesis", "semantic_resolution")


class ModelRegistryError(ValueError):
    """Raised when model registry policy is violated."""


@dataclass(frozen=True)
class ActiveModelConfig:
    id: str
    purpose: ModelPurpose
    endpoint_url: str
    model_name: str
    secret_reference: str | None
    temperature_default: float
    max_tokens: int
    system_prompt: str
    capabilities: dict[str, Any] = field(default_factory=dict)
    last_test_status: str | None = None
    last_test_detail: str | None = None


_MODEL_TEST_SENSITIVE_FIELDS = frozenset(
    {
        "purpose",
        "runtime",
        "model_name",
        "endpoint_url",
        "secret_reference",
        "capabilities",
        "context_length",
        "max_tokens",
        "temperature_default",
        "system_prompt",
    }
)


def normalize_model_purpose(purpose: str) -> ModelPurpose:
    normalized = purpose.strip().lower().replace("-", "_")
    if normalized in MODEL_PURPOSES:
        return cast(ModelPurpose, normalized)
    raise ModelRegistryError(f"unsupported model purpose: {purpose}")


def next_model_id(session: Session) -> str:
    existing_ids = set(session.scalars(select(ModelConfiguration.id)).all())
    next_index = len(existing_ids) + 1
    while True:
        model_id = f"model-{next_index}"
        if model_id not in existing_ids:
            return model_id
        next_index += 1


def configuration_status(model: ModelConfiguration) -> str:
    status = model.status.strip().lower()
    if status != "active":
        return status if status in {"disabled", "experimental"} else "disabled"
    endpoint_url = model.endpoint_url.strip().lower()
    if model.purpose in MODEL_PURPOSES:
        if model.model_name.strip() and is_http_endpoint(endpoint_url):
            if model.purpose == "extraction" and not model.system_prompt.strip():
                return "misconfigured"
            return "configured"
        return "misconfigured"
    return "configured"


def public_model_dict(model: ModelConfiguration) -> dict[str, object]:
    return {
        "id": model.id,
        "displayName": model.display_name,
        "purpose": model.purpose,
        "runtime": model.runtime,
        "modelName": model.model_name,
        "endpointUrl": model.endpoint_url,
        "contextLength": model.context_length,
        "maxTokens": model.max_tokens,
        "temperatureDefault": model.temperature_default,
        "license": model.license,
        "hardwareProfile": model.hardware_profile,
        "defaultScopes": list(model.default_scopes or []),
        "systemPrompt": model.system_prompt,
        "status": model.status,
        "capabilities": dict(model.capabilities or {}),
        "secretConfigured": bool((model.secret_reference or "").strip()),
        "lastTestStatus": model.last_test_status,
        "lastTestLatencyMs": model.last_test_latency_ms,
        "lastTestDetail": model.last_test_detail,
        "lastTestedAt": (model.last_tested_at.isoformat() if model.last_tested_at else None),
    }


def snapshot_model(model: ModelConfiguration) -> dict[str, object]:
    payload = public_model_dict(model)
    payload["secretConfigured"] = bool((model.secret_reference or "").strip())
    return payload


def apply_model_patch(model: ModelConfiguration, payload: dict[str, Any]) -> None:
    mapping = {
        "displayName": "display_name",
        "purpose": "purpose",
        "runtime": "runtime",
        "modelName": "model_name",
        "endpointUrl": "endpoint_url",
        "secretReference": "secret_reference",
        "capabilities": "capabilities",
        "contextLength": "context_length",
        "maxTokens": "max_tokens",
        "temperatureDefault": "temperature_default",
        "license": "license",
        "hardwareProfile": "hardware_profile",
        "defaultScopes": "default_scopes",
        "systemPrompt": "system_prompt",
        "status": "status",
    }
    sensitive_changed = False
    for key, value in payload.items():
        if key not in mapping:
            continue
        attr = mapping[key]
        if attr == "purpose":
            value = normalize_model_purpose(str(value))
        elif attr == "capabilities":
            try:
                value = validate_capabilities(value)
            except InferencePolicyError as exc:
                raise ModelRegistryError(str(exc)) from exc
        if attr in _MODEL_TEST_SENSITIVE_FIELDS and getattr(model, attr) != value:
            sensitive_changed = True
        setattr(model, attr, value)
    if sensitive_changed:
        model.last_test_status = None
        model.last_test_latency_ms = None
        model.last_test_detail = None
        model.last_tested_at = None


def validate_single_active_model(session: Session, model: ModelConfiguration) -> None:
    if model.status != "active":
        return
    existing = session.scalar(
        select(ModelConfiguration.id)
        .where(ModelConfiguration.purpose == model.purpose)
        .where(ModelConfiguration.status == "active")
        .where(ModelConfiguration.id != model.id)
        .limit(1)
    )
    if existing is not None:
        raise ModelRegistryError(f"only one active {model.purpose} model is allowed")


def validate_model_configuration(model: ModelConfiguration, *, require_connectivity: bool = False) -> None:
    if model.purpose not in MODEL_PURPOSES:
        raise ModelRegistryError(f"unsupported model purpose: {model.purpose}")
    if model.status not in {"active", "disabled", "experimental"}:
        raise ModelRegistryError(f"unsupported model status: {model.status}")
    if model.context_length is not None and model.context_length < 0:
        raise ModelRegistryError("contextLength must be greater than or equal to 0")
    if model.max_tokens is not None and model.max_tokens < 0:
        raise ModelRegistryError("maxTokens must be greater than or equal to 0")
    if model.temperature_default is not None and model.temperature_default < 0:
        raise ModelRegistryError("temperatureDefault must be greater than or equal to 0")
    if model.capabilities is not None:
        try:
            validate_capabilities(model.capabilities)
        except InferencePolicyError as exc:
            raise ModelRegistryError(str(exc)) from exc
    if model.status.strip().lower() != "active" and not require_connectivity:
        return
    if not model.model_name.strip():
        raise ModelRegistryError(f"active {model.purpose} model requires modelName")
    endpoint_url = model.endpoint_url.strip().lower()
    if not is_http_endpoint(endpoint_url):
        raise ModelRegistryError(f"active {model.purpose} model requires http(s) endpointUrl")
    if model.purpose == "extraction" and not model.system_prompt.strip():
        raise ModelRegistryError("active extraction model requires systemPrompt")
    if model.purpose == "vision_ocr":
        caps = model.capabilities or {}
        if not caps.get("image_input"):
            raise ModelRegistryError("active vision_ocr model must support image input")


def require_successful_model_test(model: ModelConfiguration) -> None:
    """Require a successful admin health test before a model becomes active."""

    if model.last_test_status != "healthy":
        raise ModelRegistryError("model must pass the admin health test before activation")


class ModelSelectionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def configured_active_registry(self) -> dict[ModelPurpose, ActiveModelConfig | None]:
        return {purpose: self.configured_active_model(purpose) for purpose in MODEL_PURPOSES}

    def configured_active_model(self, purpose: ModelPurpose | str) -> ActiveModelConfig | None:
        return configured_active_model(self.session, purpose)


def configured_active_registry(session: Session) -> dict[ModelPurpose, ActiveModelConfig | None]:
    return ModelSelectionService(session).configured_active_registry()


def configured_active_model(session: Session, purpose: ModelPurpose | str) -> ActiveModelConfig | None:
    canonical_purpose = cast(ModelPurpose, normalize_model_purpose(str(purpose)))
    rows = list(
        session.scalars(
            select(ModelConfiguration)
            .where(ModelConfiguration.purpose == canonical_purpose)
            .where(ModelConfiguration.status == "active")
            .order_by(ModelConfiguration.id)
        ).all()
    )
    if not rows:
        return None
    configured = [row for row in rows if configuration_status(row) == "configured"]
    if len(configured) != 1:
        raise ModelRegistryError(f"expected exactly one active configured {canonical_purpose} model")
    model = configured[0]
    return ActiveModelConfig(
        id=model.id,
        purpose=canonical_purpose,
        endpoint_url=model.endpoint_url,
        model_name=model.model_name,
        secret_reference=(model.secret_reference or None),
        temperature_default=model.temperature_default,
        max_tokens=model.max_tokens,
        system_prompt=model.system_prompt,
        capabilities=dict(model.capabilities or {}),
        last_test_status=model.last_test_status,
        last_test_detail=model.last_test_detail,
    )


def record_model_test_result(
    model: ModelConfiguration, *, status: str, latency_ms: int, detail: str, tested_at: datetime
) -> None:
    model.last_test_status = status
    model.last_test_latency_ms = latency_ms
    model.last_test_detail = detail[:1000]
    model.last_tested_at = tested_at
