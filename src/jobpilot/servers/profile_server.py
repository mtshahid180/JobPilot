"""MCP server: the single source of truth for profile facts (read-only)."""

from __future__ import annotations

from jobpilot.servers.mcp_compat import create_server, run_server
from jobpilot.services.profile import ProfileService

server = create_server(
    "jobpilot-profile",
    instructions="Read-only access to the candidate profile. Never invent missing facts.",
)


@server.tool()
def get_profile() -> dict:
    """Return the full candidate profile."""
    return {"profile": ProfileService().get().model_dump(mode="json")}


@server.tool()
def search_experience(query: str, limit: int = 5) -> dict:
    """Retrieve the most relevant experience bullets for a query."""
    return {"results": ProfileService().search_experience(query, limit=max(1, min(limit, 15)))}


@server.tool()
def list_skills() -> dict:
    """List skills with evidence counts."""
    return {"skills": ProfileService().list_skills()}


def main() -> None:
    run_server(server)


if __name__ == "__main__":
    main()
