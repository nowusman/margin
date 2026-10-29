import json
import re
from pathlib import Path

import yaml

from app.config import Settings


def test_worker_image_contains_packages_and_dependencies() -> None:
    source = Path("workers/Dockerfile").read_text(encoding="utf-8")
    tasks = Path("workers/tasks.py").read_text(encoding="utf-8")
    assert "COPY packages /app/packages" in source
    assert "COPY integrations /app/integrations" not in source
    assert "apps/api/requirements.txt" in source
    assert "packages/margin_db" in source
    assert "packages/margin_auth" in source
    assert "libgomp1" in source
    assert "libglib2.0-0" in source
    assert "_EMBEDDING_PROVIDER" in tasks
    assert "provider = _embedding_provider()" in tasks


def test_compose_uses_one_unified_worker() -> None:
    compose = Path("infra/docker-compose.yml").read_text(encoding="utf-8")
    services = yaml.safe_load(compose)["services"]

    worker_services = sorted(
        service_name for service_name in services if service_name == "worker" or service_name.startswith("worker-")
    )
    assert worker_services == ["worker"]
    assert "scale-workers" not in compose
    assert "MARGIN_WORKER_ROLE" not in services["worker"]["environment"]


def test_compose_baseline_services_are_not_profile_gated() -> None:
    compose = yaml.safe_load(Path("infra/docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert "profiles" not in services["redis"]
    assert "profiles" not in services["minio"]
    assert services["keycloak"]["profiles"] == ["auth"]


def test_compose_runtime_does_not_include_oauth2_proxy() -> None:
    compose = Path("infra/docker-compose.yml").read_text(encoding="utf-8")
    nginx = Path("apps/web/nginx/default.conf").read_text(encoding="utf-8")
    api_dockerfile = Path("apps/api/Dockerfile").read_text(encoding="utf-8")
    object_store_init_dockerfile = Path("apps/api/Dockerfile.object-store-init").read_text(encoding="utf-8")
    object_store_init_requirements = Path("apps/api/requirements-object-store-init.txt").read_text(encoding="utf-8")
    web_dockerfile = Path("apps/web/Dockerfile").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")
    keycloak_realm = json.loads(Path("infra/keycloak/margin-realm.json").read_text(encoding="utf-8"))
    assert "keycloak:" in compose
    assert 'profiles: ["auth"]' in compose
    assert "oauth2-proxy" not in compose
    assert "OAUTH2_PROXY_" not in env_example
    assert "VITE_AUTH_AUTO_REDIRECT=true" in env_example
    assert "VITE_OIDC_ISSUER=http://localhost:8082/realms/margin" in env_example
    assert not Path(".env.example2").exists()
    parsed_compose = yaml.safe_load(compose)
    assert parsed_compose["services"]["minio"]["env_file"] == ["../.env"]
    assert "env_file" not in parsed_compose["services"]["postgres"]
    assert "proxy_pass http://api:8000" in nginx
    assert "auth_request" not in nginx
    assert "proxy_pass http://oauth2-proxy" not in nginx
    assert "proxy_set_header X-Margin-User" not in nginx
    assert "proxy_set_header X-Margin-Groups" not in nginx
    assert '"127.0.0.1:5173:80"' in compose
    assert "MARGIN_AUTH_HEADER_FALLBACK: ${MARGIN_COMPOSE_AUTH_HEADER_FALLBACK:-true}" in compose
    assert "MARGIN_AUTH_DEPLOYMENT_PROFILE: ${MARGIN_COMPOSE_AUTH_DEPLOYMENT_PROFILE:-external_oidc}" in compose
    assert "MARGIN_KEYCLOAK_PROFILE_ENABLED: ${MARGIN_COMPOSE_KEYCLOAK_PROFILE_ENABLED:-false}" in compose
    assert "alembic -c packages/margin_db/alembic.ini upgrade head" in api_dockerfile
    assert "plantuml" in api_dockerfile
    assert "libgomp1" in api_dockerfile
    assert "requirements-object-store-init.txt" in object_store_init_dockerfile
    assert "pgvector==0.3.6" in object_store_init_requirements
    assert "docling" not in object_store_init_dockerfile
    assert "sentence-transformers" not in object_store_init_dockerfile
    assert "ARG VITE_AUTH_AUTO_REDIRECT" in web_dockerfile
    assert "COPY integrations /app/integrations" not in api_dockerfile
    assert "packages/margin_auth" in api_dockerfile
    assert (
        parsed_compose["services"]["object-store-init"]["build"]["dockerfile"]
        == "apps/api/Dockerfile.object-store-init"
    )
    assert "VITE_AUTH_AUTO_REDIRECT: ${MARGIN_COMPOSE_VITE_AUTH_AUTO_REDIRECT:-true}" in compose
    assert "VITE_OIDC_ISSUER: ${MARGIN_COMPOSE_VITE_OIDC_ISSUER:-http://localhost:8082/realms/margin}" in compose
    assert parsed_compose["services"]["api"]["volumes"] == ["model-cache:/models"]
    assert parsed_compose["services"]["worker"]["volumes"] == ["model-cache:/models"]
    assert parsed_compose["services"]["model-cache-init"]["volumes"] == ["model-cache:/models"]
    profile_services = {
        name: service["profiles"] for name, service in parsed_compose["services"].items() if "profiles" in service
    }
    assert profile_services == {"keycloak": ["auth"]}
    assert keycloak_realm["clients"][0]["attributes"]["post.logout.redirect.uris"] == "+"
    assert "http://127.0.0.1:5173/*" in keycloak_realm["clients"][0]["redirectUris"]
    assert keycloak_realm["accessTokenLifespan"] == 28800
    assert keycloak_realm["ssoSessionIdleTimeout"] == 604800
    assert keycloak_realm["ssoSessionMaxLifespan"] == 2592000


def test_docker_deployment_docs_do_not_present_header_fallback_as_public_auth() -> None:
    deployment = Path("docs/deployment.md").read_text(encoding="utf-8")

    assert "# Margin Docker Deployment" in deployment
    assert "The Docker stack does not include an OAuth2 proxy" in deployment
    assert "Do not expose this Docker stack directly to the internet" in deployment
    assert "Confirm redirect to" not in deployment
    assert "docker-compose.contabo.yml" not in deployment
    assert "MARGIN_AUTH_HEADER_FALLBACK=true" in deployment


def test_alembic_revision_ids_fit_default_version_table() -> None:
    for migration in Path("packages/margin_db/alembic/versions").glob("*.py"):
        source = migration.read_text(encoding="utf-8")
        match = re.search(r'^revision:\s*str\s*=\s*"([^"]+)"', source, re.MULTILINE)
        if match is None:
            continue
        assert len(match.group(1)) <= 32, migration


def test_inference_benchmark_script_outputs_json_shape() -> None:
    script = Path("scripts/performance/inference_benchmark.py")
    assert script.exists()
    ns: dict[str, object] = {}
    exec(script.read_text(encoding="utf-8"), ns)
    run_benchmark_probe = ns["run_benchmark_probe"]
    assert callable(run_benchmark_probe)
    result = run_benchmark_probe(samples=3)
    assert result.extraction_tokens_per_sec > 0
    assert result.answer_latency_p50_ms > 0
    assert result.answer_latency_p95_ms > 0
    assert result.batch_throughput_docs_per_min > 0
    assert result.hardware_profile["platform"]


def test_dr_manifest_validator_accepts_restore_drill_fixture() -> None:
    ns: dict[str, object] = {}
    script = Path("scripts/operations/validate_dr_manifest.py")
    exec(script.read_text(encoding="utf-8"), ns)
    validate_manifest = ns["validate_manifest"]
    errors = validate_manifest(Path("tests/fixtures/operations/dr_manifest.json"))
    assert errors == []


def test_worker_entrypoint_has_required_roles() -> None:
    source = Path("workers/main.py").read_text(encoding="utf-8")
    assert "MARGIN_WORKER_ROLE" not in source
    assert 'WORKER_QUEUES = ("ingestion", "extraction", "embedding", "review", "maintenance", "export")' in source


def test_auth_deployment_profile_requires_matching_keycloak_flag() -> None:
    try:
        Settings(_env_file=None, keycloak_profile_enabled=True)
    except ValueError as exc:
        assert "MARGIN_AUTH_DEPLOYMENT_PROFILE=keycloak_profile" in str(exc)
    else:
        raise AssertionError("keycloak profile flag must require keycloak deployment profile")

    try:
        Settings(_env_file=None, auth_deployment_profile="keycloak_profile")
    except ValueError as exc:
        assert "MARGIN_KEYCLOAK_PROFILE_ENABLED=true" in str(exc)
    else:
        raise AssertionError("keycloak deployment profile must require keycloak profile enablement")

    settings = Settings(_env_file=None, auth_deployment_profile="keycloak_profile", keycloak_profile_enabled=True)
    assert settings.auth_deployment_profile == "keycloak_profile"
