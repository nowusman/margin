"""The Margin Model Context Protocol server.

Margin is reachable from any MCP client: the server exposes the governed
retrieval, knowledge graph and review capabilities as tools, and the readable
corpus as resources. Every request goes through the Margin API, so the
authorization, provenance and audit rules are the same ones the web
application obeys.
"""

from __future__ import annotations

from margin_mcp.client import MarginApiClient, MarginApiError, MarginIdentity
from margin_mcp.resources import read_resource, resource_templates
from margin_mcp.server import McpServer, serve_stdio
from margin_mcp.tools import TOOLS, call_tool

__all__ = [
    "McpServer",
    "MarginApiClient",
    "MarginApiError",
    "MarginIdentity",
    "TOOLS",
    "call_tool",
    "read_resource",
    "resource_templates",
    "serve_stdio",
]
