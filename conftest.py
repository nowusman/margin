from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auth_header_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARGIN_ENV", "local")
    monkeypatch.setenv("MARGIN_AUTH_DEPLOYMENT_PROFILE", "external_oidc")
    monkeypatch.setenv("MARGIN_AUTH_HEADER_FALLBACK", "true")
    monkeypatch.setenv("MARGIN_KEYCLOAK_PROFILE_ENABLED", "false")
    monkeypatch.setenv("MARGIN_OIDC_ISSUER_URL", "")
    monkeypatch.setenv("MARGIN_OIDC_JWKS_URL", "")
    monkeypatch.setenv("MARGIN_OIDC_HS256_SECRET", "")
    monkeypatch.setenv("MARGIN_OIDC_VERIFY_SIGNATURE", "false")
    monkeypatch.setenv("MARGIN_EMBEDDING_MODEL_ID", "test-embedding-model-v1")
    monkeypatch.setenv("MARGIN_EMBEDDING_INDEX_VERSION", "test-embedding-index-v1")
