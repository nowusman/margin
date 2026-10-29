from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import yaml

from app.config import Settings
from app.main import create_app


def test_prd_nfr_auth_posture_is_oidc_only() -> None:
    auth_mode_values = get_args(Settings.model_fields["auth_mode"].annotation)
    profile_values = get_args(Settings.model_fields["auth_deployment_profile"].annotation)
    settings = Settings(_env_file=None)

    assert auth_mode_values == ("oidc",)
    assert set(profile_values) == {"external_oidc", "keycloak_profile"}
    assert settings.auth_mode == "oidc"
    env_defaults = Path(".env.example").read_text(encoding="utf-8")
    assert "MARGIN_AUTH_HEADER_FALLBACK=true" in env_defaults

    routes = {route.path for route in create_app().routes}
    assert all("/login" not in path for path in routes)
    assert all("/password" not in path for path in routes)


def test_prd_nfr_production_rejects_dev_header_fallback_and_missing_verifier() -> None:
    try:
        Settings(_env_file=None, env="production", auth_header_fallback=True)
    except ValueError as exc:
        assert "MARGIN_AUTH_HEADER_FALLBACK=true is not allowed in production" in str(exc)
    else:
        raise AssertionError("production fallback guard must reject trusted dev header mode")

    try:
        Settings(
            _env_file=None,
            env="production",
            auth_header_fallback=False,
            oidc_issuer_url="https://idp.example/realms/margin",
        )
    except ValueError as exc:
        assert "MARGIN_OIDC_JWKS_URL or MARGIN_OIDC_HS256_SECRET is required" in str(exc)
    else:
        raise AssertionError("production OIDC must require a signature verifier")


def test_prd_nfr_inference_benchmark_script_emits_latency_metrics() -> None:
    script_path = Path("scripts/performance/inference_benchmark.py")
    assert script_path.exists()
    ns: dict[str, object] = {}
    exec(script_path.read_text(encoding="utf-8"), ns)
    run_benchmark_probe = ns["run_benchmark_probe"]
    result = run_benchmark_probe(samples=5)
    assert result.answer_latency_p95_ms > 0
    assert result.answer_latency_p95_ms < 2000


def test_prd_nfr_compose_defaults_match_auth_posture() -> None:
    compose = yaml.safe_load(Path("infra/docker-compose.yml").read_text(encoding="utf-8"))

    api_env = compose["services"]["api"]["environment"]
    assert api_env["MARGIN_ENV"] == "local"
    assert api_env["MARGIN_AUTH_HEADER_FALLBACK"] == "${MARGIN_COMPOSE_AUTH_HEADER_FALLBACK:-true}"
    assert api_env["MARGIN_AUTH_DEPLOYMENT_PROFILE"] == "${MARGIN_COMPOSE_AUTH_DEPLOYMENT_PROFILE:-external_oidc}"
    assert api_env["MARGIN_KEYCLOAK_PROFILE_ENABLED"] == "${MARGIN_COMPOSE_KEYCLOAK_PROFILE_ENABLED:-false}"
    assert compose["services"]["keycloak"]["profiles"] == ["auth"]


def test_prd_nfr_observability_signals_cover_required_operational_metrics() -> None:
    alerts = Path("docs/operations/alert-policies.md").read_text(encoding="utf-8")
    operations_text = alerts.lower()

    assert "margin application health data" in operations_text
    assert "postgresql records" in operations_text
    assert "worker job state" in operations_text
    for signal in (
        "failed ingestion jobs",
        "retrieval latency p95",
        "average answer faithfulness score",
        "oldest pending review task age",
        "/api/governance/review/tasks",
    ):
        assert signal in operations_text


def test_prd_008_closure_command_targets_exist() -> None:
    expected_paths = (
        "tests/test_prd_pipeline_e2e.py",
        "tests/test_prd_nfr_acceptance.py",
        "tests/test_atomic_extraction.py",
        "tests/test_entity_catalog.py",
        "tests/test_entity_resolution.py",
        "tests/test_entity_migration.py",
        "apps/api/tests/test_entity_routes.py",
        "apps/api/tests/test_rbac_middleware.py",
        "scripts/check_runtime_guards.py",
    )
    for rel in expected_paths:
        assert Path(rel).exists()

    package = json.loads(Path("apps/web/package.json").read_text(encoding="utf-8"))
    scripts = package.get("scripts", {})
    for script_name in ("test", "lint", "build"):
        assert script_name in scripts
