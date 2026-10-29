"""The tools the MCP server exposes over the Margin API.

Each tool is a thin, honest projection of one API capability. The tool never
decides what the caller may see: it asks the API, and the API applies the
access rules. When the API refuses, the refusal is reported as a refusal.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from margin_mcp.client import MarginApiClient

MAX_TOP_K = 20
DEFAULT_TOP_K = 8
MAX_NEIGHBOUR_LIMIT = 100
DEFAULT_NEIGHBOUR_LIMIT = 25


@dataclass(frozen=True, slots=True)
class McpTool:
    """One callable capability exposed to a model."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[MarginApiClient, dict[str, Any]], str]


def _text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' is required and must be a non-empty string")
    return value.strip()


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string when provided")
    return value.strip()


def _optional_str_list(arguments: dict[str, Any], key: str) -> list[str]:
    value = arguments.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"'{key}' must be a list of strings")
    return [item for item in value if item]


def _optional_int(arguments: dict[str, Any], key: str, *, default: int, maximum: int) -> int:
    value = arguments.get(key)
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"'{key}' must be an integer")
    return max(1, min(maximum, value))


def _search_body(arguments: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": _require_str(arguments, "query"),
        "filters": {"domain_ids": _optional_str_list(arguments, "domain_ids")},
        "top_k": _optional_int(arguments, "top_k", default=DEFAULT_TOP_K, maximum=MAX_TOP_K),
        "expand": {"relation_types": [], "depth": 0, "limit": 0},
    }
    return body


def _summarise_results(payload: Any) -> str:
    if not isinstance(payload, dict):
        return _text(payload)
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return _text({"message": "No readable records matched.", "warnings": payload.get("operational_warnings", [])})
    summarised = []
    for item in results:
        if not isinstance(item, dict):
            continue
        summarised.append(
            {
                "statement": item.get("statement"),
                "snippet": item.get("snippet"),
                "score": item.get("score"),
                "status": item.get("status"),
                "citations": [
                    citation.get("citation_id") for citation in item.get("citations", []) if isinstance(citation, dict)
                ],
                "related": item.get("related", []),
            }
        )
    return _text(
        {"fusion": payload.get("fusion"), "results": summarised, "warnings": payload.get("operational_warnings", [])}
    )


def _search(client: MarginApiClient, arguments: dict[str, Any]) -> str:
    payload = client.request("POST", "/api/search", body=_search_body(arguments))
    return _summarise_results(payload)


def _ask(client: MarginApiClient, arguments: dict[str, Any]) -> str:
    body = _search_body(arguments)
    thread_id = _optional_str(arguments, "thread_id")
    if thread_id:
        body["thread_id"] = thread_id
    payload = client.request("POST", "/api/ask", body=body)
    if not isinstance(payload, dict):
        return _text(payload)
    return _text(
        {
            "answer": payload.get("answer"),
            "answer_citation_ids": payload.get("answer_citation_ids"),
            "unavailable_reason": payload.get("answer_unavailable_reason"),
            "output_language": payload.get("output_language"),
            "results": _summarise_results(payload),
            "warnings": payload.get("operational_warnings", []),
        }
    )


def _get_entity(client: MarginApiClient, arguments: dict[str, Any]) -> str:
    entity_id = _require_str(arguments, "entity_id")
    return _text(client.request("GET", f"/api/entities/{entity_id}"))


def _get_neighborhood(client: MarginApiClient, arguments: dict[str, Any]) -> str:
    entity_id = _require_str(arguments, "entity_id")
    limit = _optional_int(arguments, "limit", default=DEFAULT_NEIGHBOUR_LIMIT, maximum=MAX_NEIGHBOUR_LIMIT)
    payload = client.request("GET", f"/api/relationships/neighbors/{entity_id}", params={"limit": limit})
    return _text(payload)


def _get_evidence(client: MarginApiClient, arguments: dict[str, Any]) -> str:
    idea_block_id = _require_str(arguments, "idea_block_id")
    return _text(client.request("GET", f"/api/evidence/{idea_block_id}"))


def _get_graph(client: MarginApiClient, arguments: dict[str, Any]) -> str:
    params: dict[str, Any] = {}
    root_entity_id = _optional_str(arguments, "root_entity_id")
    if root_entity_id:
        params["root_entity_id"] = root_entity_id
    params["depth"] = _optional_int(arguments, "depth", default=1, maximum=3)
    payload = client.request("GET", "/api/graph/subgraph", params=params)
    if not isinstance(payload, dict):
        return _text(payload)
    nodes = payload.get("nodes")
    edges = payload.get("edges")
    return _text(
        {
            "node_count": len(nodes) if isinstance(nodes, list) else 0,
            "edge_count": len(edges) if isinstance(edges, list) else 0,
            "communities": payload.get("communities", []),
            "nodes": nodes if isinstance(nodes, list) else [],
            "edges": edges if isinstance(edges, list) else [],
            "capped": payload.get("capped"),
        }
    )


def _raise_review(client: MarginApiClient, arguments: dict[str, Any]) -> str:
    body: dict[str, Any] = {
        "target_type": _require_str(arguments, "target_type"),
        "target_id": _require_str(arguments, "target_id"),
        "reason": _require_str(arguments, "reason"),
        "task_type": _optional_str(arguments, "task_type") or "manual_review",
    }
    recommendation = _optional_str(arguments, "recommendation")
    if recommendation:
        body["recommendation"] = recommendation
    domain_id = _optional_str(arguments, "domain_id")
    if domain_id:
        body["domain_id"] = domain_id
    return _text(client.request("POST", "/api/governance/review/tasks", body=body))


TOOLS: tuple[McpTool, ...] = (
    McpTool(
        name="margin_search",
        description=(
            "Search the readable Margin knowledge base. Returns verified statements with the "
            "citations that justify them, the fusion breakdown, and the related records."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "domain_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict the search to these domains.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TOP_K,
                    "description": "How many results to return.",
                },
            },
            "required": ["query"],
        },
        handler=_search,
    ),
    McpTool(
        name="margin_ask",
        description=(
            "Ask a question and receive a cited answer grounded only in evidence the caller may "
            "read. The answer cites the records it used."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The question to answer."},
                "domain_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict the answer to these domains.",
                },
                "top_k": {"type": "integer", "minimum": 1, "maximum": MAX_TOP_K},
                "thread_id": {
                    "type": "string",
                    "description": "Continue an earlier conversation instead of starting a new one.",
                },
            },
            "required": ["query"],
        },
        handler=_ask,
    ),
    McpTool(
        name="margin_get_entity",
        description="Read one canonical entity, with its aliases, sources and relationships.",
        input_schema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
        handler=_get_entity,
    ),
    McpTool(
        name="margin_get_neighborhood",
        description=(
            "Read the verified relationships directly around one entity. Every returned edge carries "
            "the sentence that justifies it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_NEIGHBOUR_LIMIT,
                    "description": "How many relationships to return.",
                },
            },
            "required": ["entity_id"],
        },
        handler=_get_neighborhood,
    ),
    McpTool(
        name="margin_get_evidence",
        description="Read the evidence behind one claim, including the quoted source passage.",
        input_schema={
            "type": "object",
            "properties": {"idea_block_id": {"type": "string"}},
            "required": ["idea_block_id"],
        },
        handler=_get_evidence,
    ),
    McpTool(
        name="margin_get_graph",
        description=(
            "Read the knowledge graph: the records, the evidenced relationships between them, and "
            "the communities they resolve into. Without a root record it returns the whole readable "
            "graph."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "root_entity_id": {
                    "type": "string",
                    "description": "The record whose neighbourhood to read. Omit for the whole graph.",
                },
                "depth": {"type": "integer", "minimum": 1, "maximum": 3},
            },
        },
        handler=_get_graph,
    ),
    McpTool(
        name="margin_raise_review",
        description=(
            "Raise a review task so a human resolves something you found. The record itself is " "never changed."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_type": {"type": "string", "description": "What kind of record is affected."},
                "target_id": {"type": "string"},
                "reason": {"type": "string", "description": "Why a human should look at this."},
                "recommendation": {"type": "string"},
                "task_type": {"type": "string"},
                "domain_id": {"type": "string"},
            },
            "required": ["target_type", "target_id", "reason"],
        },
        handler=_raise_review,
    ),
)

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def call_tool(client: MarginApiClient, name: str, arguments: dict[str, Any]) -> str:
    """Run a tool by name.

    An access refusal is raised rather than absorbed, so the caller can tell a
    model that the call failed instead of handing it a sentence that looks like
    an answer.
    """

    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ValueError(f"unknown tool: {name}")
    return tool.handler(client, arguments)
