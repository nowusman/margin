from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from margin_db.base import Base
from margin_db.model_catalog_models import ModelConfiguration
from margin_db.user_models import Domain, DomainPermission


def _client() -> TestClient:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    app: FastAPI = create_app()
    app.state.settings = Settings()
    app.state.session_factory = session_factory
    with session_factory() as session:
        session.add(Domain(id="OSS", name="OSS"))
        for permission in ("read", "write", "review"):
            session.add(
                DomainPermission(domain_id="OSS", principal_type="user", principal_id="alice", permission=permission)
            )
        session.add(
            ModelConfiguration(
                id="inference-narrator",
                display_name="Primary Inference Provider",
                purpose="extraction",
                runtime="Ollama",
                model_name="minimax-m3:cloud",
                endpoint_url="http://host.docker.internal:11434/v1",
                system_prompt="Margin governed architecture extraction engine",
                max_tokens=4096,
                status="active",
                capabilities={"structured_output": True},
                last_test_status="healthy",
            )
        )
        session.commit()
    return TestClient(app)


def test_admin_model_and_connector_mutations_require_permissions() -> None:
    client = _client()

    assert client.get("/api/admin/models", headers={"x-margin-user": "bob"}).status_code == 403
    assert (
        client.patch(
            "/api/admin/models/inference-narrator", json={"status": "active"}, headers={"x-margin-user": "bob"}
        ).status_code
        == 403
    )

    headers = {"x-margin-user": "alice", "x-margin-groups": "margin-admin"}
    models = client.get("/api/admin/models", headers=headers)
    assert models.status_code == 200
    inference_model = models.json()[0]
    assert inference_model["id"] == "inference-narrator"
    assert inference_model["displayName"] == "Primary Inference Provider"
    assert inference_model["purpose"] == "extraction"
    assert inference_model["runtime"] == "Ollama"
    assert inference_model["modelName"] == "minimax-m3:cloud"
    assert inference_model["endpointUrl"] == "http://host.docker.internal:11434/v1"
    assert inference_model["status"] == "active"
    assert inference_model["secretConfigured"] is False
    assert "apiKey" not in inference_model
    assert "secretReference" not in inference_model
    assert "approvalStatus" not in inference_model

    invalid_status = client.patch("/api/admin/models/inference-narrator", json={"status": "retired"}, headers=headers)
    assert invalid_status.status_code == 422

    invalid_model = client.patch(
        "/api/admin/models/inference-narrator",
        json={"status": "active", "modelName": "", "endpointUrl": "", "secretReference": "env:SECRET_KEY"},
        headers=headers,
    )
    assert invalid_model.status_code == 422

    stale_approval_field = client.patch(
        "/api/admin/models/inference-narrator", json={"approvalStatus": "approved"}, headers=headers
    )
    assert stale_approval_field.status_code == 422

    invalid_temperature = client.patch(
        "/api/admin/models/inference-narrator", json={"temperatureDefault": -0.1}, headers=headers
    )
    assert invalid_temperature.status_code == 422

    display_name_patch = client.patch(
        "/api/admin/models/inference-narrator", json={"displayName": "Renamed provider"}, headers=headers
    )
    assert display_name_patch.status_code == 200
    assert display_name_patch.json()["model"]["status"] == "active"

    sensitive_patch = client.patch(
        "/api/admin/models/inference-narrator", json={"secretReference": "env:SECRET_KEY"}, headers=headers
    )
    assert sensitive_patch.status_code == 422
    assert "health test" in sensitive_patch.json()["detail"]
    unchanged_model = client.get("/api/admin/models", headers=headers).json()[0]
    assert unchanged_model["secretConfigured"] is False
    assert "apiKey" not in unchanged_model
    assert "secretReference" not in unchanged_model

    first_created_model = client.post(
        "/api/admin/models", json={"displayName": "Extra provider", "status": "disabled"}, headers=headers
    )
    assert first_created_model.status_code == 201
    assert first_created_model.json()["model"]["id"] == "model-2"
    assert first_created_model.json()["model"]["status"] == "disabled"
    assert first_created_model.json()["model"]["modelName"] == ""
    assert first_created_model.json()["model"]["endpointUrl"] == ""

    second_created_model = client.post(
        "/api/admin/models", json={"displayName": "Second extra provider", "status": "disabled"}, headers=headers
    )
    assert second_created_model.status_code == 201
    assert second_created_model.json()["model"]["id"] == "model-3"

    retrieval_model = client.post(
        "/api/admin/models", json={"displayName": "Retrieval provider", "purpose": "embedding"}, headers=headers
    )
    assert retrieval_model.status_code == 422
    assert "unsupported model purpose" in retrieval_model.json()["detail"]

    experimental_model = client.post(
        "/api/admin/models",
        json={
            "displayName": "Experimental provider",
            "modelName": "experimental-model",
            "endpointUrl": "http://host.docker.internal:11435/v1",
            "status": "experimental",
        },
        headers=headers,
    )
    assert experimental_model.status_code == 201

    duplicate_active = client.post(
        "/api/admin/models",
        json={
            "displayName": "Duplicate extraction provider",
            "purpose": "extraction",
            "modelName": "duplicate-model",
            "endpointUrl": "http://host.docker.internal:11436/v1",
            "status": "active",
        },
        headers=headers,
    )
    assert duplicate_active.status_code == 422
    model_health = client.get("/api/admin/models/health", headers=headers)
    assert model_health.status_code == 200
    health_by_detail = {item["detail"]: item for item in model_health.json()}
    assert health_by_detail["http://host.docker.internal:11435/v1 experimental-model"]["status"] == "experimental"

    model_test = client.post("/api/admin/models/inference-narrator/test", headers=headers)
    assert model_test.status_code == 200
    assert model_test.json()["status"] in {"healthy", "failed"}

    audit_response = client.get(
        "/api/admin/audit-events?target_type=model_configuration&target_id=inference-narrator", headers=headers
    )
    assert audit_response.status_code == 200
    assert any(event["action"] == "model_configuration.tested" for event in audit_response.json()["audit_events"])

    assert client.post("/api/admin/models/missing/test", headers=headers).status_code == 404


def test_admin_record_crud_routes() -> None:
    client = _client()
    headers = {"x-margin-user": "platform", "x-margin-groups": "platform_admin"}

    created_model = client.post(
        "/api/admin/models", json={"displayName": "Disposable provider", "status": "disabled"}, headers=headers
    )
    assert created_model.status_code == 201
    model_id = created_model.json()["model"]["id"]
    assert client.delete(f"/api/admin/models/{model_id}", headers=headers).status_code == 200
    assert model_id not in {model["id"] for model in client.get("/api/admin/models", headers=headers).json()}
    assert client.delete(f"/api/admin/models/{model_id}", headers=headers).status_code == 404
    assert client.delete("/api/admin/models/inference-narrator", headers=headers).status_code == 409
    assert "inference-narrator" in {model["id"] for model in client.get("/api/admin/models", headers=headers).json()}

    created_access = client.post(
        "/api/admin/access?user=dave",
        json={"groups": ["architect"], "domains": ["OSS"], "sensitivities": ["internal"]},
        headers=headers,
    )
    assert created_access.status_code == 201
    patched_access = client.patch(
        "/api/admin/access/dave",
        json={"groups": ["auditor"], "domains": ["OSS"], "sensitivities": ["internal"]},
        headers=headers,
    )
    assert patched_access.status_code == 200
    assert patched_access.json()["profile"]["groups"] == ["auditor"]
    assert client.delete("/api/admin/access/dave", headers=headers).status_code == 200
    assert client.delete("/api/admin/access/dave", headers=headers).status_code == 404
