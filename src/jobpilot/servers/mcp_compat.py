"""Compatibility shim for MCP SDK v1 (FastMCP) and v2 (MCPServer).

JobPilot targets the installed SDK without pinning users to one major version.
"""

from __future__ import annotations

from typing import Any


def create_server(name: str, instructions: str = "") -> Any:
    try:
        from mcp.server.mcpserver import MCPServer  # MCP SDK >= 2

        return MCPServer(name=name, instructions=instructions)
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP  # MCP SDK 1.x

        return FastMCP(name=name, instructions=instructions)
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "The MCP Python SDK is not installed. Install it with `pip install mcp`."
        ) from exc


def run_server(server: Any, transport: str = "stdio") -> None:
    # Headless transports have no web session; bind the configured account (or
    # the local workspace) so every tool call is scoped correctly.
    from jobpilot.core.context import set_current_user_id
    from jobpilot.services.users import resolve_workspace_user

    set_current_user_id(resolve_workspace_user())
    try:
        server.run(transport=transport)
    except TypeError:  # pragma: no cover - very old SDK
        server.run()
