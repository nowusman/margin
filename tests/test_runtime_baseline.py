from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


def test_required_runtime_artifacts_exist() -> None:
    required_paths = [
        "Makefile",
        ".env.example",
        "infra/docker-compose.yml",
        "model_registry.yaml",
        ".github/workflows/ci.yml",
    ]

    for relative_path in required_paths:
        assert Path(relative_path).exists(), f"Missing runtime artifact: {relative_path}"


def test_model_registry_declares_no_local_ocr_models() -> None:
    registry = yaml.safe_load(Path("model_registry.yaml").read_text(encoding="utf-8"))
    model_types = {model["type"] for model in registry["models"]}

    assert model_types == set()
    assert "ocr" not in model_types
    assert "inference" not in model_types


def test_runtime_model_execution_is_not_configured_by_environment() -> None:
    env_lines = Path(".env.example").read_text(encoding="utf-8").splitlines()
    env = dict(line.split("=", 1) for line in env_lines if line and not line.startswith("#"))
    compose = yaml.safe_load(Path("infra/docker-compose.yml").read_text(encoding="utf-8"))

    env_keys = set(env)
    worker_env_keys = set(compose["x-worker-environment"])
    runtime_model_keys = {"MARGIN_MODEL_URL", "MARGIN_MODEL_NAME", "MARGIN_MODEL_API_KEY"}
    assert env_keys.isdisjoint(runtime_model_keys)
    assert worker_env_keys.isdisjoint(runtime_model_keys)


def test_env_example_uses_oidc_only_auth_modes() -> None:
    env_lines = Path(".env.example").read_text(encoding="utf-8").splitlines()
    env = dict(line.split("=", 1) for line in env_lines if line and not line.startswith("#"))

    assert env["MARGIN_AUTH_MODE"] == "oidc"
    assert "MARGIN_OIDC_ISSUER_URL" in env
    assert env["MARGIN_AUTH_DEPLOYMENT_PROFILE"] == "external_oidc"
    assert env["MARGIN_KEYCLOAK_PROFILE_ENABLED"] == "false"
    assert env["MARGIN_AUTH_HEADER_FALLBACK"] == "true"
    assert "MARGIN_AUTH_HEADER_FALLBACK" in env
    assert "MARGIN_ALLOW_HEADER_AUTH" not in env


def test_env_example_sets_minio_endpoint_for_baseline_runtime() -> None:
    env_lines = Path(".env.example").read_text(encoding="utf-8").splitlines()
    env = dict(line.split("=", 1) for line in env_lines if line and not line.startswith("#"))

    assert env["MARGIN_OBJECT_STORE_ENDPOINT"] == "http://minio:9000"


def test_runtime_guards_pass_for_local_openai_compatible_naming() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_runtime_guards.py"], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stdout + result.stderr
