from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from margin_db.object_store_factory import (
    DEFAULT_OBJECT_STORE_BUCKET,
    DEFAULT_OBJECT_STORE_ENDPOINT,
    DEFAULT_OBJECT_STORE_LOCAL_PATH,
    DEFAULT_OBJECT_STORE_REGION,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MARGIN_", extra="ignore")

    env: str = "local"
    database_url: str = Field(
        default="postgresql+psycopg://margin:margin@localhost:5432/margin",
        description="PostgreSQL operational system-of-record URL.",
    )
    object_store_endpoint: str = DEFAULT_OBJECT_STORE_ENDPOINT
    object_store_bucket: str = DEFAULT_OBJECT_STORE_BUCKET
    object_store_region: str = DEFAULT_OBJECT_STORE_REGION
    object_store_access_key_id: str | None = None
    object_store_secret_access_key: str | None = None
    object_store_session_token: str | None = None
    object_store_local_path: str = DEFAULT_OBJECT_STORE_LOCAL_PATH
    redis_url: str = "redis://localhost:6379/0"
    celery_result_backend: str | None = None
    celery_task_always_eager: bool = False
    auth_mode: Literal["oidc"] = "oidc"
    auth_deployment_profile: Literal["external_oidc", "keycloak_profile"] = "external_oidc"
    oidc_issuer_url: str = ""
    oidc_audience: str | None = None
    oidc_client_id: str = "margin-web"
    oidc_jwks_url: str | None = None
    oidc_groups_claim: str = "groups"
    oidc_email_claim: str = "email"
    oidc_hs256_secret: str | None = None
    oidc_verify_signature: bool = False
    keycloak_profile_enabled: bool = False
    auth_header_fallback: bool = False

    @model_validator(mode="after")
    def validate_auth_posture(self) -> Settings:
        production = self.env.lower() in {"prod", "production"}
        if production and self.auth_header_fallback:
            raise ValueError("MARGIN_AUTH_HEADER_FALLBACK=true is not allowed in production")
        if production and not self.oidc_issuer_url:
            raise ValueError("MARGIN_OIDC_ISSUER_URL is required in production")
        if production and not (self.oidc_jwks_url or self.oidc_hs256_secret):
            raise ValueError("MARGIN_OIDC_JWKS_URL or MARGIN_OIDC_HS256_SECRET is required in production")
        if self.keycloak_profile_enabled and self.auth_deployment_profile != "keycloak_profile":
            raise ValueError(
                "MARGIN_AUTH_DEPLOYMENT_PROFILE=keycloak_profile is required when "
                "MARGIN_KEYCLOAK_PROFILE_ENABLED=true"
            )
        if self.auth_deployment_profile == "keycloak_profile" and not self.keycloak_profile_enabled:
            raise ValueError(
                "MARGIN_KEYCLOAK_PROFILE_ENABLED=true is required when "
                "MARGIN_AUTH_DEPLOYMENT_PROFILE=keycloak_profile"
            )
        return self


def get_settings() -> Settings:
    return Settings()
