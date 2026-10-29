from __future__ import annotations

import json
from typing import Any

from margin_mcp.client import MarginApiError, MarginIdentity
from margin_mcp.server import McpServer, serve_stdio


class FakeApiClient:
    """A stand-in for the Margin API that records what was asked of it."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []
        self.identity = MarginIdentity(user="reader", groups=("architect",))

    def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None
    ) -> Any:
        self.calls.append((method, path, params, body))
        if path in self.responses:
            value = self.responses[path]
            if isinstance(value, MarginApiError):
                raise value
            return value
        return {}


def _server(responses: dict[str, Any] | None = None) -> tuple[McpServer, FakeApiClient]:
    client = FakeApiClient(responses)
    return McpServer(client), client


def test_initialize_reports_tools_and_resources_capabilities() -> None:
    server, _ = _server()

    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

    assert response is not None
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]
    assert "resources" in result["capabilities"]
    assert result["serverInfo"]["name"] == "margin"


def test_notifications_receive_no_response() -> None:
    server, _ = _server()

    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_methods_are_protocol_errors() -> None:
    server, _ = _server()

    response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "does/not/exist"})

    assert response is not None
    assert response["error"]["code"] == -32601


def test_tools_list_exposes_the_governed_capabilities() -> None:
    server, _ = _server()

    response = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})

    assert response is not None
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == {
        "margin_search",
        "margin_ask",
        "margin_get_entity",
        "margin_get_neighborhood",
        "margin_get_evidence",
        "margin_get_graph",
        "margin_raise_review",
    }
    for tool in response["result"]["tools"]:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_search_tool_calls_the_api_and_returns_cited_results() -> None:
    server, client = _server(
        {
            "/api/search": {
                "fusion": {"mode": "hybrid", "channels": {"lexical": 2, "dense": 1}},
                "results": [
                    {
                        "statement": "The payment API writes settled payments to the ledger.",
                        "snippet": "The payment API writes settled payments",
                        "score": 0.9,
                        "status": "verified",
                        "citations": [{"id": "citation-1"}],
                        "related": [{"kind": "entity", "id": "ent-1", "label": "Payment API"}],
                    }
                ],
                "warnings": [],
            }
        }
    )

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "margin_search", "arguments": {"query": "payments", "top_k": 3}},
        }
    )

    assert response is not None
    text = response["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["results"][0]["citations"] == ["citation-1"]
    assert payload["fusion"]["mode"] == "hybrid"

    method, path, _params, body = client.calls[0]
    assert method == "POST"
    assert path == "/api/search"
    assert body is not None
    assert body["query"] == "payments"
    assert body["top_k"] == 3


def test_ask_tool_continues_a_thread_when_one_is_named() -> None:
    server, client = _server(
        {
            "/api/ask": {
                "answer": "The payment API writes settled payments to the ledger.",
                "answer_citation_ids": ["citation-1"],
                "output_language": "English",
                "results": [],
                "warnings": [],
            }
        }
    )

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "margin_ask", "arguments": {"query": "who writes?", "thread_id": "thread-1"}},
        }
    )

    assert response is not None
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["answer_citation_ids"] == ["citation-1"]

    _method, _path, _params, body = client.calls[0]
    assert body is not None
    assert body["thread_id"] == "thread-1"


def test_an_access_refusal_is_reported_as_a_refusal_and_not_filtered() -> None:
    server, _ = _server({"/api/search": MarginApiError(403, "missing read access")})

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "margin_search", "arguments": {"query": "secret"}},
        }
    )

    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "refused" in text
    assert "missing read access" in text


def test_resources_list_and_read_expose_the_citable_corpus() -> None:
    server, client = _server(
        {
            "/api/domains": {"domains": [{"id": "OSS", "name": "Operating Systems"}]},
            "/api/domains/OSS/charter": {"active": {"version": 2}},
            "/api/domains/OSS/narrative": {"markdown": "# Operating Systems"},
            "/api/entities/ent-1": {"id": "ent-1", "preferred_label": "Payment API"},
        }
    )

    listed = server.handle({"jsonrpc": "2.0", "id": 7, "method": "resources/list"})
    assert listed is not None
    assert listed["result"]["resources"][0]["uri"] == "margin://domains"

    templates = server.handle({"jsonrpc": "2.0", "id": 8, "method": "resources/templates/list"})
    assert templates is not None
    uris = {template["uriTemplate"] for template in templates["result"]["resourceTemplates"]}
    assert "margin://domain/{domain_id}" in uris
    assert "margin://entity/{entity_id}" in uris

    read = server.handle(
        {"jsonrpc": "2.0", "id": 9, "method": "resources/read", "params": {"uri": "margin://entity/ent-1"}}
    )
    assert read is not None
    contents = read["result"]["contents"][0]
    assert contents["uri"] == "margin://entity/ent-1"
    assert json.loads(contents["text"])["preferred_label"] == "Payment API"

    domain = server.handle(
        {"jsonrpc": "2.0", "id": 10, "method": "resources/read", "params": {"uri": "margin://domain/OSS"}}
    )
    assert domain is not None
    payload = json.loads(domain["result"]["contents"][0]["text"])
    assert payload["charter"]["active"]["version"] == 2
    assert payload["narrative"]["markdown"] == "# Operating Systems"

    paths = [path for _method, path, _params, _body in client.calls]
    assert "/api/entities/ent-1" in paths
    assert "/api/domains/OSS/narrative" in paths


def test_an_unknown_resource_is_reported_as_an_error_not_a_silent_empty() -> None:
    server, _ = _server()

    response = server.handle(
        {"jsonrpc": "2.0", "id": 11, "method": "resources/read", "params": {"uri": "margin://nothing/here"}}
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert "unknown resource" in response["result"]["content"][0]["text"]


def test_a_missing_required_argument_is_reported_clearly() -> None:
    server, _ = _server()

    response = server.handle(
        {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "margin_search", "arguments": {}}}
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert "query" in response["result"]["content"][0]["text"]


def test_serving_over_stdio_answers_each_request_in_order() -> None:
    import io

    client = FakeApiClient({"/api/domains": {"domains": []}})
    server = McpServer(client)
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + "\n"
    )
    stdout = io.StringIO()

    serve_stdio(server, stdin=stdin, stdout=stdout)

    lines = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["id"] == 1
    assert lines[1]["id"] == 2
    assert "tools" in lines[1]["result"]
