"""The MCP server: JSON-RPC 2.0 over standard input and output.

The protocol layer is deliberately plain. A model calls a tool, the server
asks the Margin API, and the answer comes back with the citations the API
attached to it. There is no second source of truth and no local filtering.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from margin_mcp.client import MarginApiClient, MarginApiError, describe_refusal
from margin_mcp.resources import read_resource, resource_templates
from margin_mcp.tools import TOOLS, call_tool

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "margin"
SERVER_VERSION = "0.1.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class McpServer:
    """Serves the Margin capabilities over the Model Context Protocol."""

    def __init__(self, client: MarginApiClient) -> None:
        self._client = client

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one protocol message. Notifications return no response."""

        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "a request must be a JSON object")
        method = message.get("method")
        message_id = message.get("id")
        if not isinstance(method, str) or not method:
            return _error(message_id, INVALID_REQUEST, "a request must name a method")
        params = message.get("params")
        if params is not None and not isinstance(params, dict):
            return _error(message_id, INVALID_PARAMS, "params must be an object")

        if method.startswith("notifications/"):
            return None

        if method == "initialize":
            return _result(
                message_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"listChanged": False, "subscribe": False},
                    },
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
        if method == "ping":
            return _result(message_id, {})
        if method == "tools/list":
            return _result(
                message_id,
                {
                    "tools": [
                        {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
                        for tool in TOOLS
                    ]
                },
            )
        if method == "tools/call":
            return _result(message_id, self._call_tool(params or {}))
        if method == "resources/list":
            return _result(
                message_id,
                {
                    "resources": [
                        {
                            "uri": resource.uri,
                            "name": resource.name,
                            "description": resource.description,
                            "mimeType": resource.mime_type,
                        }
                        for resource in _listed_resources()
                    ]
                },
            )
        if method == "resources/templates/list":
            return _result(message_id, {"resourceTemplates": resource_templates()})
        if method == "resources/read":
            return _result(message_id, self._read_resource(params or {}))

        return _error(message_id, METHOD_NOT_FOUND, f"unknown method: {method}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _error_content("a tool call must name a tool")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _error_content("tool arguments must be an object")
        try:
            return {"content": [{"type": "text", "text": call_tool(self._client, name, arguments)}]}
        except ValueError as error:
            return _error_content(str(error))
        except MarginApiError as error:
            return _error_content(describe_refusal(error))

    def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            return _error_content("a resource read must name a uri")
        try:
            return {"contents": read_resource(self._client, uri)}
        except ValueError as error:
            return _error_content(str(error))
        except MarginApiError as error:
            return _error_content(describe_refusal(error))


def _listed_resources() -> tuple:
    from margin_mcp.resources import RESOURCES

    return RESOURCES


def _result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _error_content(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def serve_stdio(server: McpServer, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """Serve the protocol over standard input and output."""

    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    for line in source:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except ValueError:
            _write(sink, _error(None, PARSE_ERROR, "the message was not valid JSON"))
            continue
        response = server.handle(message)
        if response is not None:
            _write(sink, response)


def _write(sink: TextIO, payload: dict[str, Any]) -> None:
    sink.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sink.flush()
