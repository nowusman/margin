# Keycloak

The optional local Keycloak profile imports `margin-realm.json`.

Default `docker compose` does not start Keycloak. Use the auth profile when a
local OIDC provider is required:

```bash
docker compose --env-file .env -f infra/docker-compose.yml --profile auth up -d
```

On Windows PowerShell, run the same command from the repository root. Rebuild
the web image after changing the browser-facing issuer values.

When this profile is enabled, set:

```bash
MARGIN_AUTH_DEPLOYMENT_PROFILE=keycloak_profile
MARGIN_KEYCLOAK_PROFILE_ENABLED=true
MARGIN_OIDC_ISSUER_URL=http://localhost:8082/realms/margin
MARGIN_OIDC_JWKS_URL=http://keycloak:8080/realms/margin/protocol/openid-connect/certs
MARGIN_OIDC_VERIFY_SIGNATURE=true
VITE_AUTH_AUTO_REDIRECT=true
MARGIN_COMPOSE_AUTH_HEADER_FALLBACK=false
MARGIN_COMPOSE_AUTH_DEPLOYMENT_PROFILE=keycloak_profile
MARGIN_COMPOSE_KEYCLOAK_PROFILE_ENABLED=true
MARGIN_COMPOSE_OIDC_ISSUER_URL=http://localhost:8082/realms/margin
MARGIN_COMPOSE_OIDC_JWKS_URL=http://keycloak:8080/realms/margin/protocol/openid-connect/certs
MARGIN_COMPOSE_OIDC_VERIFY_SIGNATURE=true
MARGIN_COMPOSE_VITE_AUTH_AUTO_REDIRECT=true
MARGIN_COMPOSE_VITE_OIDC_ISSUER=http://localhost:8082/realms/margin
```
