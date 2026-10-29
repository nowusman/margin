from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.authz import require_any_domain_permission
from app.catalog_routes import audit_events as catalog_audit_events
from app.source_routes import get_session_factory
from margin_auth.permissions import is_platform_admin
from margin_db.audit import write_audit_event
from margin_db.model_catalog_models import ModelConfiguration
from margin_db.model_registry import (
    ModelRegistryError,
    apply_model_patch,
    configuration_status,
    next_model_id,
    public_model_dict,
    record_model_test_result,
    require_successful_model_test,
    snapshot_model,
    validate_model_configuration,
    validate_single_active_model,
)
from margin_inference import InferenceClient, InferencePolicyError

ModelStatus = Literal["active", "disabled", "experimental"]


def _require_margin_admin(request: Request) -> None:
    if not is_platform_admin(getattr(request.state, "group_ids", ())):
        raise HTTPException(status_code=403, detail="margin-admin group is required for platform administration")


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(_require_margin_admin)])


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    displayName: str | None = None
    purpose: str | None = None
    runtime: str | None = None
    modelName: str | None = None
    endpointUrl: str | None = None
    secretReference: str | None = None
    capabilities: dict[str, Any] | None = None
    contextLength: int | None = None
    maxTokens: int | None = None
    temperatureDefault: float | None = None
    license: str | None = None
    hardwareProfile: str | None = None
    defaultScopes: list[str] | None = None
    systemPrompt: str | None = None
    status: ModelStatus | None = None


def _new_model_template() -> dict[str, object]:
    return {
        "displayName": "",
        "purpose": "extraction",
        "runtime": "openai-compatible",
        "modelName": "",
        "endpointUrl": "",
        "secretReference": None,
        "capabilities": {},
        "contextLength": 0,
        "maxTokens": 0,
        "temperatureDefault": 0,
        "license": "model-specific",
        "hardwareProfile": "configured-runtime",
        "status": "disabled",
        "defaultScopes": ["internal"],
        "systemPrompt": "",
    }


@contextmanager
def _model_session(request: Request):
    session_factory = get_session_factory(request)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _actor(request: Request) -> str:
    return str(getattr(request.state, "principal_id", "unknown"))


def _http_model_health(model: ModelConfiguration) -> tuple[str, int, str]:
    try:
        client = InferenceClient(
            base_url=model.endpoint_url, model=model.model_name, secret_reference=model.secret_reference
        )
    except InferencePolicyError as error:
        raise HTTPException(status_code=409, detail="model is misconfigured") from error
    return client.probe(timeout_seconds=5)


def _configuration_error(exc: ModelRegistryError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _require_admin_read(request: Request) -> None:
    require_any_domain_permission(
        permissions=request.state.permissions, action="read", detail="missing read access for admin data"
    )


def _require_admin_write(request: Request) -> None:
    require_any_domain_permission(
        permissions=request.state.permissions, action="write", detail="missing write access for admin data"
    )


@router.get("/models")
def models(request: Request) -> list[dict[str, object]]:
    _require_admin_read(request)
    with _model_session(request) as session:
        rows = session.scalars(select(ModelConfiguration).order_by(ModelConfiguration.created_at)).all()
        return [public_model_dict(model) for model in rows]


@router.post("/models", status_code=201)
def create_model(body: ModelPatch, request: Request) -> dict[str, object]:
    _require_admin_write(request)
    with _model_session(request) as session:
        payload = {**_new_model_template(), **body.model_dump(exclude_none=True)}
        model = ModelConfiguration(id=next_model_id(session), display_name="")
        try:
            apply_model_patch(model, payload)
            validate_model_configuration(model)
            if model.status == "active":
                require_successful_model_test(model)
            validate_single_active_model(session, model)
        except ModelRegistryError as exc:
            raise _configuration_error(exc) from exc
        session.add(model)
        session.flush()
        write_audit_event(
            session,
            actor=_actor(request),
            action="model_configuration.created",
            target_type="model_configuration",
            target_id=model.id,
            after=snapshot_model(model),
        )
        return {"status": "created", "model": public_model_dict(model)}


@router.patch("/models/{model_id}")
def patch_model(model_id: str, body: ModelPatch, request: Request) -> dict[str, object]:
    _require_admin_write(request)
    with _model_session(request) as session:
        model = session.get(ModelConfiguration, model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="model not found")
        before = snapshot_model(model)
        try:
            apply_model_patch(model, body.model_dump(exclude_none=True))
            validate_model_configuration(model)
            if model.status == "active":
                require_successful_model_test(model)
            validate_single_active_model(session, model)
        except ModelRegistryError as exc:
            raise _configuration_error(exc) from exc
        session.flush()
        write_audit_event(
            session,
            actor=_actor(request),
            action="model_configuration.updated",
            target_type="model_configuration",
            target_id=model.id,
            before=before,
            after=snapshot_model(model),
        )
        return {"status": "updated", "model": public_model_dict(model)}


@router.delete("/models/{model_id}")
def delete_model(model_id: str, request: Request) -> dict[str, object]:
    _require_admin_write(request)
    with _model_session(request) as session:
        model = session.get(ModelConfiguration, model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="model not found")
        if model.status == "active":
            raise HTTPException(status_code=409, detail="active model must be disabled before deletion")
        before = snapshot_model(model)
        session.delete(model)
        write_audit_event(
            session,
            actor=_actor(request),
            action="model_configuration.deleted",
            target_type="model_configuration",
            target_id=model_id,
            before=before,
        )
        return {"status": "deleted", "modelId": model_id}


@router.post("/models/{model_id}/test")
def test_model(model_id: str, request: Request) -> dict[str, object]:
    _require_admin_write(request)
    with _model_session(request) as session:
        model = session.get(ModelConfiguration, model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="model not found")
        try:
            validate_model_configuration(model, require_connectivity=True)
        except ModelRegistryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        before = snapshot_model(model)
        health_status, latency_ms, detail = _http_model_health(model)
        record_model_test_result(
            model, status=health_status, latency_ms=latency_ms, detail=detail, tested_at=datetime.now(UTC)
        )
        session.flush()
        write_audit_event(
            session,
            actor=_actor(request),
            action="model_configuration.tested",
            target_type="model_configuration",
            target_id=model.id,
            before=before,
            after=snapshot_model(model),
        )
        return {"modelId": model_id, "status": health_status, "latencyMs": latency_ms, "detail": detail}


@router.get("/models/health")
def model_health(request: Request) -> list[dict[str, str]]:
    _require_admin_read(request)
    with _model_session(request) as session:
        rows = session.scalars(select(ModelConfiguration).order_by(ModelConfiguration.created_at)).all()
        return [
            {
                "provider": model.runtime,
                "status": model.last_test_status or configuration_status(model),
                "detail": model.last_test_detail or f"{model.endpoint_url} {model.model_name}".strip(),
            }
            for model in rows
        ]


@router.get("/models/configuration-checks")
def model_configuration_checks(request: Request) -> list[dict[str, object]]:
    _require_admin_read(request)
    with _model_session(request) as session:
        rows = session.scalars(select(ModelConfiguration).order_by(ModelConfiguration.created_at)).all()
        return [{"id": model.id, "configured": configuration_status(model) == "configured"} for model in rows]


@router.get("/system-health")
def system_health(request: Request) -> dict[str, object]:
    _require_admin_read(request)
    database_status = "unavailable"
    try:
        with _model_session(request) as session:
            session.execute(text("SELECT 1"))
        database_status = "ready"
    except (OSError, SQLAlchemyError):
        pass
    settings = getattr(request.app.state, "settings", None)
    object_storage_status = (
        "configured"
        if getattr(settings, "object_store_endpoint", "") and getattr(settings, "object_store_bucket", "")
        else "not_configured"
    )
    redis_status = "configured" if getattr(settings, "redis_url", "") else "not_configured"
    converter_status = getattr(request.app.state, "converter_dependency_status", "unknown")
    ready = database_status == "ready" and converter_status == "ready"
    return {
        "status": "ready" if ready else "blocked",
        "database": database_status,
        "converterDependencies": converter_status,
        "objectStorage": object_storage_status,
        "redis": redis_status,
    }


@router.get("/audit-events")
def audit_events(
    request: Request,
    actor: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, object]:
    return catalog_audit_events(
        request=request,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        date_from=date_from,
        date_to=date_to,
        limit=200,
    )
