from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.check_runtime_guards import BLOCKED_DEPENDENCY_NAMES

COMPOSE_PATH = Path("infra/docker-compose.yml")
API_REQUIREMENTS_PATH = Path("apps/api/requirements.txt")
OBJECT_STORE_INIT_REQUIREMENTS_PATH = Path("apps/api/requirements-object-store-init.txt")
WORKER_REQUIREMENTS_PATH = Path("workers/requirements.txt")
ENV_EXAMPLE_PATH = Path(".env.example")
RETRIEVAL_SERVICE_PATH = Path("packages/margin_retrieval/margin_retrieval/service.py")
RETRIEVAL_ANSWERING_PATH = Path("packages/margin_retrieval/margin_retrieval/answering.py")
RELATIONSHIP_QUERIES_PATH = Path("packages/margin_db/margin_db/relationship_queries.py")
SOURCE_INGESTION_PATH = Path("packages/margin_db/margin_db/source_ingestion.py")
CONVERTERS_PATH = Path("packages/margin_ingest/margin_ingest/converters.py")

DISALLOWED_REQUIREMENTS = {
    "anthropic",
    "cohere",
    "google-generativeai",
    "google-genai",
    "groq",
    "mistralai",
    "neo4j",
    "opensearch-py",
    "qdrant-client",
    "temporalio",
}
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _compose_services() -> dict[str, dict[str, Any]]:
    compose = _load_yaml(COMPOSE_PATH)
    return compose["services"]


def _default_service_names(services: dict[str, dict[str, Any]]) -> set[str]:
    return {name for name, service in services.items() if not service.get("profiles")}


def _requirement_name(line: str) -> str | None:
    requirement = line.split("#", 1)[0].strip()
    if not requirement or requirement.startswith(("-r", "--", "-e")):
        return None

    match = REQUIREMENT_NAME_RE.match(requirement)
    if match is None:
        return None

    return match.group(1).lower().replace("_", "-")


def test_compose_defaults_do_not_run_external_adapter_services() -> None:
    services = _compose_services()
    default_services = _default_service_names(services)

    assert default_services == {
        "postgres",
        "api",
        "web",
        "redis",
        "minio",
        "model-cache-init",
        "object-store-init",
        "worker",
    }


def test_compose_defaults_to_single_unified_worker() -> None:
    services = _compose_services()
    default_workers = sorted(
        service_name
        for service_name in _default_service_names(services)
        if service_name == "worker" or service_name.startswith("worker-")
    )

    assert default_workers == ["worker"]
    assert services["worker"]["environment"]["MARGIN_CELERY_CONCURRENCY"] == "${MARGIN_WORKER_CONCURRENCY:-1}"
    assert services["api"]["environment"]["WEB_CONCURRENCY"] == "${WEB_CONCURRENCY:-1}"
    assert services["api"]["volumes"] == ["model-cache:/models"]
    assert services["worker"]["volumes"] == ["model-cache:/models"]
    assert services["model-cache-init"]["volumes"] == ["model-cache:/models"]
    assert services["api"]["depends_on"]["model-cache-init"]["condition"] == "service_completed_successfully"
    assert services["worker"]["depends_on"]["model-cache-init"]["condition"] == "service_completed_successfully"


def test_unified_worker_consumes_retained_lifecycle_queues() -> None:
    from workers.main import WORKER_QUEUES

    assert WORKER_QUEUES == ("ingestion", "extraction", "embedding", "review", "maintenance", "export")


def test_unified_worker_validates_converter_dependencies(monkeypatch) -> None:
    import workers.main as worker_main

    calls = 0

    def record_validation() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(worker_main, "validate_converter_dependencies", record_validation)
    monkeypatch.setattr(worker_main, "_run_worker", lambda: None)

    worker_main.main()

    assert calls == 1


def test_compose_does_not_register_role_specific_workers() -> None:
    services = _compose_services()

    assert not {name for name in services if name.startswith("worker-")}


def test_compose_redis_and_minio_are_in_default_runtime() -> None:
    services = _compose_services()

    assert not services["redis"].get("profiles")
    assert not services["minio"].get("profiles")


def test_api_requirements_do_not_include_default_external_adapter_packages() -> None:
    requirement_names = {
        name
        for line in API_REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        if (name := _requirement_name(line)) is not None
    }

    assert requirement_names.isdisjoint(DISALLOWED_REQUIREMENTS)


def test_worker_requirements_do_not_duplicate_api_requirements() -> None:
    api_requirement_names = {
        name
        for line in API_REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        if (name := _requirement_name(line)) is not None
    }
    worker_requirement_names = {
        name
        for line in WORKER_REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        if (name := _requirement_name(line)) is not None
    }

    assert worker_requirement_names.isdisjoint(api_requirement_names)


def test_object_store_init_requirements_do_not_include_heavy_runtime_dependencies() -> None:
    requirement_names = {
        name
        for line in OBJECT_STORE_INIT_REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        if (name := _requirement_name(line)) is not None
    }

    assert "docling" not in requirement_names
    assert "markitdown" not in requirement_names
    assert "sentence-transformers" not in requirement_names
    assert "torch" not in requirement_names


def test_env_example_uses_only_supported_runtime_prefixes() -> None:
    env = dict(
        line.split("=", 1)
        for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )

    allowed_margin_prefixes = (
        "MARGIN_AGENT",
        "MARGIN_API",
        "MARGIN_AUTH",
        "MARGIN_CELERY",
        "MARGIN_CONTEXT",
        "MARGIN_DATABASE",
        "MARGIN_ENV",
        "MARGIN_EMBEDDING",
        "MARGIN_KEYCLOAK",
        "MARGIN_MCP",
        "MARGIN_PLAYBOOK",
        "MARGIN_OBJECT_STORE",
        "MARGIN_OIDC",
        "MARGIN_PLANTUML",
        "MARGIN_POSTGRES",
        "MARGIN_REDIS",
        "MARGIN_RERANKER",
        "MARGIN_WORKER",
    )
    unexpected = sorted(key for key in env if key.startswith("MARGIN_") and not key.startswith(allowed_margin_prefixes))
    assert unexpected == []


def test_blocked_provider_sdk_packages_are_not_permitted() -> None:
    blocked = set(BLOCKED_DEPENDENCY_NAMES)

    assert DISALLOWED_REQUIREMENTS.issubset(blocked)


def test_removed_legacy_runtime_paths_do_not_reappear() -> None:
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            RETRIEVAL_SERVICE_PATH,
            RETRIEVAL_ANSWERING_PATH,
            RELATIONSHIP_QUERIES_PATH,
            SOURCE_INGESTION_PATH,
            CONVERTERS_PATH,
        )
    )

    assert "SafeRetrievalAgent" not in runtime_source
    assert "agent_tool" not in runtime_source
    assert "local_similarity" not in runtime_source
    assert "article_index" not in runtime_source
    assert "_portable_lexical_search" not in runtime_source
    assert "_neighbors_portable" not in runtime_source
    assert "LocalDocumentConverter" not in runtime_source
    assert "convert_with_fallback" not in runtime_source
    assert "DeterministicFallbackConverter" not in runtime_source
    assert "MarkItDownFallbackConverter" not in runtime_source
