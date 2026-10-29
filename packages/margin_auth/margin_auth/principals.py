from __future__ import annotations

from dataclasses import dataclass

from margin_auth.tokens import AuthSettings, decode_jwt_payload, keycloak_roles, parse_groups, validate_claims


@dataclass(frozen=True)
class PrincipalContext:
    principal_id: str
    group_ids: tuple[str, ...]
    auth_source: str


def resolve_principal(headers: dict[str, str], settings: AuthSettings) -> PrincipalContext:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = decode_jwt_payload(auth.split(" ", 1)[1].strip(), settings)
        if payload and validate_claims(payload, settings):
            principal = str(
                payload.get("preferred_username")
                or payload.get(settings.oidc_email_claim)
                or payload.get("sub")
                or "anonymous"
            )
            groups = parse_groups(payload.get(settings.oidc_groups_claim))
            if not groups:
                groups = keycloak_roles(payload)
            if not groups:
                groups = parse_groups(
                    headers.get("x-forwarded-groups")
                    or headers.get("x-auth-request-groups")
                    or headers.get("x-auth-request-group")
                    or headers.get("x-forwarded-group")
                    or ""
                )
            if not groups and settings.auth_header_fallback:
                groups = parse_groups(headers.get("x-margin-groups"))
            return PrincipalContext(principal_id=principal, group_ids=groups, auth_source="oidc_bearer")

        return PrincipalContext(principal_id="anonymous", group_ids=(), auth_source="invalid_bearer")

    if not settings.auth_header_fallback:
        return PrincipalContext(principal_id="anonymous", group_ids=(), auth_source="fallback_disabled")
    principal = headers.get("x-margin-user", "anonymous")
    groups = parse_groups(headers.get("x-margin-groups"))
    return PrincipalContext(principal_id=principal, group_ids=groups, auth_source="header_fallback")
