from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from margin_db.base import Base
from margin_db.user_models import CategoryPermission, Domain, DomainPermission, User


def _base64_url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _token_with_payload(payload: dict[str, object]) -> str:
    header = _base64_url(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8"))
    body = _base64_url(json.dumps(payload).encode("utf-8"))
    return f"{header}.{body}."


def _use_local_auth_fallback(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MARGIN_ENV", "local")
    monkeypatch.setenv("MARGIN_AUTH_HEADER_FALLBACK", "true")
    monkeypatch.setenv("MARGIN_OIDC_ISSUER_URL", "")
    monkeypatch.setenv("MARGIN_OIDC_JWKS_URL", "")
    monkeypatch.setenv("MARGIN_OIDC_HS256_SECRET", "")
    monkeypatch.setenv("MARGIN_OIDC_VERIFY_SIGNATURE", "false")


def _sqlite_session_factory() -> sessionmaker:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_rbac_middleware_reads_database_permissions(monkeypatch: MonkeyPatch) -> None:
    _use_local_auth_fallback(monkeypatch)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add(Domain(id="BSS", name="BSS", owner="ea", steward="bss"))
        session.add(DomainPermission(domain_id="BSS", principal_type="user", principal_id="alice", permission="read"))
        session.add(
            CategoryPermission(category="Risk", principal_type="group", principal_id="reviewers", permission="review")
        )
        session.add(
            CategoryPermission(
                category="sensitivity:confidential", principal_type="group", principal_id="reviewers", permission="read"
            )
        )
        session.commit()

    app = create_app()
    app.state.session_factory = session_factory
    client = TestClient(app)

    response = client.get("/rbac/me", headers={"x-margin-user": "alice", "x-margin-groups": "reviewers"})

    assert response.status_code == 200
    assert response.json()["auth_source"] == "header_fallback"
    assert response.json()["domain_permissions"] == {"BSS": ["read"]}
    assert response.json()["category_permissions"] == {"Risk": ["review"]}
    assert response.json()["sensitivity_permissions"] == {"confidential": ["read"]}
    assert response.json()["authz_backend"] == "postgres_permission_cache"


def test_invalid_bearer_does_not_fall_back_to_dev_headers(monkeypatch: MonkeyPatch) -> None:
    _use_local_auth_fallback(monkeypatch)
    app = create_app()
    app.state.session_factory = _sqlite_session_factory()
    client = TestClient(app)

    response = client.get(
        "/rbac/me",
        headers={"authorization": "Bearer not-a-jwt", "x-margin-user": "alice", "x-margin-groups": "reviewers"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["principal_id"] == "anonymous"
    assert body["auth_source"] == "invalid_bearer"
    assert body["domain_permissions"] == {}
    assert body["category_permissions"] == {}
    assert body["sensitivity_permissions"] == {}


def test_fallback_disabled_without_bearer_keeps_anonymous_principal(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MARGIN_AUTH_HEADER_FALLBACK", "false")
    app = create_app()
    app.state.session_factory = _sqlite_session_factory()
    client = TestClient(app)

    response = client.get("/rbac/me", headers={"x-margin-user": "alice", "x-margin-groups": "reviewers"})

    assert response.status_code == 200
    body = response.json()
    assert body["principal_id"] == "anonymous"
    assert body["auth_source"] == "fallback_disabled"
    assert body["domain_permissions"] == {}


def test_settings_only_allow_oidc_auth_mode() -> None:
    settings = Settings(_env_file=None)

    assert settings.auth_mode == "oidc"


def test_group_based_implicit_permissions_include_super_admin_and_reviewer(monkeypatch: MonkeyPatch) -> None:
    _use_local_auth_fallback(monkeypatch)
    app = create_app()
    app.state.session_factory = _sqlite_session_factory()
    client = TestClient(app)

    super_admin = client.get("/rbac/me", headers={"x-margin-user": "alice", "x-margin-groups": "margin-admin"})
    assert super_admin.status_code == 200
    super_admin_body = super_admin.json()
    assert sorted(super_admin_body["domain_permissions"]["*"]) == ["read", "review", "write"]
    assert sorted(super_admin_body["category_permissions"]["*"]) == ["read", "review", "write"]
    assert sorted(super_admin_body["sensitivity_permissions"]["*"]) == ["read", "review", "write"]

    platform_admin = client.get("/rbac/me", headers={"x-margin-user": "platform", "x-margin-groups": "platform_admin"})
    assert platform_admin.status_code == 200
    platform_admin_body = platform_admin.json()
    assert sorted(platform_admin_body["domain_permissions"]["*"]) == ["read", "review", "write"]

    reviewer = client.get("/rbac/me", headers={"x-margin-user": "bob", "x-margin-groups": "architecture-reviewers"})
    assert reviewer.status_code == 200
    reviewer_body = reviewer.json()
    assert sorted(reviewer_body["domain_permissions"]["*"]) == ["read", "review"]
    assert sorted(reviewer_body["category_permissions"]["*"]) == ["read", "review"]
    assert sorted(reviewer_body["sensitivity_permissions"]["*"]) == ["read", "review"]

    architect = client.get("/rbac/me", headers={"x-margin-user": "dana", "x-margin-groups": "architect"})
    assert architect.status_code == 200
    architect_body = architect.json()
    assert sorted(architect_body["domain_permissions"]["*"]) == ["read", "write"]
    assert sorted(architect_body["category_permissions"]["*"]) == ["read", "write"]
    assert sorted(architect_body["sensitivity_permissions"]["*"]) == ["read", "write"]


def test_valid_bearer_can_use_dev_group_fallback_when_token_has_no_groups(monkeypatch: MonkeyPatch) -> None:
    _use_local_auth_fallback(monkeypatch)
    app = create_app()
    app.state.session_factory = _sqlite_session_factory()
    client = TestClient(app)
    token = _token_with_payload({"preferred_username": "alice", "aud": "margin-web", "exp": 4102444800})

    response = client.get(
        "/rbac/me", headers={"authorization": f"Bearer {token}", "x-margin-groups": "architecture-admins"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["auth_source"] == "oidc_bearer"
    assert body["group_ids"] == ["architecture-admins"]
    assert sorted(body["category_permissions"]["*"]) == ["read", "review", "write"]

    with app.state.session_factory() as session:
        user = session.get(User, "alice")
        assert user is not None
        assert user.display_name == "alice"
