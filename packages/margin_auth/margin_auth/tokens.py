from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Protocol

import jwt


class AuthSettings(Protocol):
    oidc_jwks_url: str | None
    oidc_hs256_secret: str | None
    oidc_verify_signature: bool
    oidc_issuer_url: str
    oidc_audience: str | None
    oidc_client_id: str


def _b64url_decode(value: str) -> bytes:
    value += "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value.encode("utf-8"))


def _is_signature_acceptable(parts: list[str], settings: AuthSettings) -> bool:
    secret = (settings.oidc_hs256_secret or "").strip()
    if not secret:
        return not settings.oidc_verify_signature
    try:
        header = json.loads(_b64url_decode(parts[0]).decode("utf-8"))
    except Exception:
        return False
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        return False
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        actual = _b64url_decode(parts[2])
    except Exception:
        return False
    return hmac.compare_digest(expected, actual)


def _decode_jwks_jwt_payload(token: str, settings: AuthSettings) -> dict[str, object]:
    jwks_url = settings.oidc_jwks_url
    if not jwks_url:
        return {}
    try:
        signing_key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
        data = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
            issuer=settings.oidc_issuer_url or None,
            options={"verify_aud": False},
        )
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def decode_jwt_payload(token: str, settings: AuthSettings) -> dict[str, object]:
    if settings.oidc_jwks_url:
        return _decode_jwks_jwt_payload(token, settings)

    parts = token.split(".")
    if len(parts) != 3 or not _is_signature_acceptable(parts, settings):
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_epoch(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def validate_claims(payload: dict[str, object], settings: AuthSettings) -> bool:
    now = int(datetime.now(UTC).timestamp())
    exp = _as_epoch(payload.get("exp"))
    nbf = _as_epoch(payload.get("nbf"))
    iat = _as_epoch(payload.get("iat"))

    if exp is not None and exp <= now:
        return False
    if nbf is not None and nbf > now:
        return False
    if iat is not None and iat > now + 300:
        return False

    expected_issuer = settings.oidc_issuer_url.strip()
    if expected_issuer and str(payload.get("iss", "")).strip() != expected_issuer:
        return False

    expected_audiences = {value for value in (settings.oidc_audience, settings.oidc_client_id) if value}
    if expected_audiences:
        audience = payload.get("aud")
        if isinstance(audience, str):
            valid_audience = audience in expected_audiences
        elif isinstance(audience, list):
            valid_audience = bool(expected_audiences & {str(value) for value in audience})
        else:
            valid_audience = False
        authorized_party = str(payload.get("azp", "")).strip()
        valid_client = bool(settings.oidc_client_id and authorized_party == settings.oidc_client_id)
        if not (valid_audience or valid_client):
            return False

    return True


def parse_groups(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        values = [str(item).strip() for item in value]
    else:
        values = [str(value).strip()]
    return tuple(value for value in values if value)


def keycloak_roles(payload: dict[str, object]) -> tuple[str, ...]:
    groups: list[str] = []
    realm_access = payload.get("realm_access")
    if isinstance(realm_access, dict):
        groups.extend(parse_groups(realm_access.get("roles")))
    resource_access = payload.get("resource_access")
    if isinstance(resource_access, dict):
        for client_roles in resource_access.values():
            if isinstance(client_roles, dict):
                groups.extend(parse_groups(client_roles.get("roles")))
    return tuple(dict.fromkeys(groups))
