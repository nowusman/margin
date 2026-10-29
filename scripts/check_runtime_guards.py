"""Runtime guards for the model registry and blocked provider usage.

Two independent guards live here:

- the model registry must declare every model with an id, type, provider, and
  licence, and must not declare an OCR model, because document text extraction
  uses the primary configured extraction model;
- no source file may import a hosted provider or an external graph, vector, or
  workflow engine SDK.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

BLOCKED_DEPENDENCY_NAMES = (
    "external-proprietary-api",
    "anthropic",
    "openai",
    "google-generativeai",
    "google-genai",
    "groq",
    "cohere",
    "mistralai",
    "opensearch-py",
    "qdrant-client",
    "pymilvus",
    "weaviate-client",
    "temporalio",
    "pymongo",
    "neo4j",
)

BLOCKED_SOURCE_PATTERNS = (
    ("external-proprietary-api", "external proprietary API dependency/provider"),
    ("from anthropic", "Anthropic SDK import"),
    ("import anthropic", "Anthropic SDK import"),
    ("from openai", "OpenAI hosted SDK import"),
    ("import openai", "OpenAI hosted SDK import"),
    ("openai.api_key", "OpenAI hosted SDK configuration"),
    ("from google.generativeai", "Gemini SDK import"),
    ("import google.generativeai", "Gemini SDK import"),
    ("from google import genai", "Gemini SDK import"),
    ("import google.genai", "Gemini SDK import"),
    ("from groq", "Groq SDK import"),
    ("import groq", "Groq SDK import"),
    ("from cohere", "Cohere SDK import"),
    ("import cohere", "Cohere SDK import"),
    ("from mistralai", "Mistral SDK import"),
    ("import mistralai", "Mistral SDK import"),
    ("from opensearchpy", "OpenSearch runtime dependency"),
    ("import opensearchpy", "OpenSearch runtime dependency"),
    ("from pymilvus", "Milvus runtime dependency"),
    ("import pymilvus", "Milvus runtime dependency"),
    ("from weaviate", "Weaviate runtime dependency"),
    ("import weaviate", "Weaviate runtime dependency"),
    ("from temporalio", "Temporal runtime dependency"),
    ("import temporalio", "Temporal runtime dependency"),
    ("from neo4j", "Neo4j runtime dependency"),
    ("import neo4j", "Neo4j runtime dependency"),
    ("from pymongo", "MongoDB runtime dependency"),
    ("import pymongo", "MongoDB runtime dependency"),
    ("from qdrant_client", "Qdrant runtime dependency"),
    ("import qdrant_client", "Qdrant runtime dependency"),
)


def blocked_source_pattern_findings(text: str) -> tuple[tuple[str, str], ...]:
    lower = text.lower()
    return tuple(
        (pattern, reason)
        for pattern, reason in BLOCKED_SOURCE_PATTERNS
        if re.search(rf"\b{re.escape(pattern)}\b", lower)
    )


def model_registry_errors(path: Path) -> list[str]:
    registry = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for model in registry.get("models", []):
        for field in ("id", "type", "provider", "license"):
            if not model.get(field):
                errors.append(f"model_registry.yaml: model missing required field '{field}'")
        if model.get("type") == "ocr":
            errors.append(
                "model_registry.yaml: document text extraction must use the primary configured extraction model"
            )
    return errors


def source_scan_errors() -> list[str]:
    roots = [Path("apps"), Path("packages"), Path("workers")]
    violations: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".txt", ".toml", ".yaml", ".yml"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for pattern, reason in blocked_source_pattern_findings(text):
                    violations.append(f"{path}: blocked dependency/provider use '{pattern}' ({reason})")
    return violations


def main() -> int:
    violations = [*source_scan_errors(), *model_registry_errors(Path("model_registry.yaml"))]

    if violations:
        print("\n".join(violations))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
