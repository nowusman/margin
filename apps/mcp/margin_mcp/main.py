"""Entry point for the Margin MCP server.

The server speaks the Model Context Protocol over standard input and output,
so it can be launched by any MCP client. It presents one identity to the Margin
API, chosen at startup, and the API decides what that identity may read.
"""

from __future__ import annotations

import argparse
import os

from margin_mcp.client import MarginApiClient, MarginIdentity
from margin_mcp.server import McpServer, serve_stdio

DEFAULT_API_URL = "http://127.0.0.1:8000"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="margin-mcp", description="Serve Margin over the Model Context Protocol.")
    parser.add_argument(
        "--api-url", default=os.environ.get("MARGIN_API_URL", DEFAULT_API_URL), help="Base URL of the Margin API."
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("MARGIN_MCP_USER", ""),
        help="Principal to present when no bearer token is configured.",
    )
    parser.add_argument(
        "--groups",
        default=os.environ.get("MARGIN_MCP_GROUPS", ""),
        help="Comma-separated groups to present when no bearer token is configured.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("MARGIN_MCP_BEARER_TOKEN", ""),
        help="Bearer token to present to the Margin API.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("MARGIN_MCP_TIMEOUT", "60")),
        help="Request timeout in seconds.",
    )
    return parser


def identity_from(arguments: argparse.Namespace) -> MarginIdentity:
    groups = tuple(group.strip() for group in arguments.groups.split(",") if group.strip())
    return MarginIdentity(token=arguments.token, user=arguments.user, groups=groups)


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    client = MarginApiClient(arguments.api_url, identity_from(arguments), timeout=arguments.timeout)
    serve_stdio(McpServer(client))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
