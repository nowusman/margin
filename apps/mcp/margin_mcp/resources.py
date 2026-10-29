"""The resources the MCP server exposes over the Margin API.

A resource is something a model can read as a document rather than call as a
function. Margin exposes its corpus this way because its content is citable: a
reader that opens an entity resource sees the records and the quotes that
support them, not a summary written by a server.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from margin_mcp.client import MarginApiClient

RESOURCE_PREFIX = "margin://"
_ENTITY_PATTERN = re.compile(r"^margin://entity/(?P<entity_id>[^/]+)$")
_EVIDENCE_PATTERN = re.compile(r"^margin://evidence/(?P<idea_block_id>[^/]+)$")
_DOMAIN_PATTERN = re.compile(r"^margin://domain/(?P<domain_id>[^/]+)$")
_BRIEFING_PATTERN = re.compile(r"^margin://domain/(?P<domain_id>[^/]+)/briefing$")


@dataclass(frozen=True, slots=True)
class McpResource:
    """One addressable document exposed to a model."""

    uri: str
    name: str
    description: str
    mime_type: str = "application/json"


DOMAINS_RESOURCE = McpResource(
    uri="margin://domains",
    name="Readable domains",
    description="The domains this identity may read, with the charter in force for each.",
)

RESOURCES: tuple[McpResource, ...] = (DOMAINS_RESOURCE,)


def resource_templates() -> list[dict[str, Any]]:
    """Return the URI templates a client can use to address resources."""

    return [
        {
            "uriTemplate": "margin://domains",
            "name": "Readable domains",
            "description": DOMAINS_RESOURCE.description,
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "margin://domain/{domain_id}",
            "name": "Domain",
            "description": "A domain's charter and the narrative composed from its records.",
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "margin://domain/{domain_id}/briefing",
            "name": "Domain briefing",
            "description": "A bounded summary of what the corpus currently holds for a domain.",
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "margin://entity/{entity_id}",
            "name": "Entity",
            "description": "A canonical entity with its aliases, sources and relationships.",
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "margin://evidence/{idea_block_id}",
            "name": "Evidence",
            "description": "The evidence behind one claim, including the quoted source passage.",
            "mimeType": "application/json",
        },
    ]


def read_resource(client: MarginApiClient, uri: str) -> list[dict[str, Any]]:
    """Read one resource and return its contents."""

    if uri == DOMAINS_RESOURCE.uri:
        domains = client.request("GET", "/api/domains", params={"permission": "read"})
        entries = domains.get("domains", []) if isinstance(domains, dict) else []
        return [_contents(uri, {"domains": entries})]

    match = _DOMAIN_PATTERN.match(uri)
    if match:
        domain_id = match.group("domain_id")
        charter = client.request("GET", f"/api/domains/{domain_id}/charter")
        narrative = client.request("GET", f"/api/domains/{domain_id}/narrative")
        return [_contents(uri, {"charter": charter, "narrative": narrative})]

    match = _BRIEFING_PATTERN.match(uri)
    if match:
        domain_id = match.group("domain_id")
        briefing = client.request("GET", f"/api/domains/{domain_id}/briefing")
        return [_contents(uri, briefing)]

    match = _ENTITY_PATTERN.match(uri)
    if match:
        entity_id = match.group("entity_id")
        return [_contents(uri, client.request("GET", f"/api/entities/{entity_id}"))]

    match = _EVIDENCE_PATTERN.match(uri)
    if match:
        idea_block_id = match.group("idea_block_id")
        return [_contents(uri, client.request("GET", f"/api/evidence/{idea_block_id}"))]

    raise ValueError(f"unknown resource: {uri}")


def _contents(uri: str, payload: Any) -> dict[str, Any]:
    return {
        "uri": uri,
        "mimeType": "application/json",
        "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
    }
