"""MCP server: local application tracker."""

from __future__ import annotations

from jobpilot.servers.mcp_compat import create_server, run_server
from jobpilot.services.tracker import TrackerService

server = create_server(
    "jobpilot-tracker",
    instructions="Track applications locally. This server never submits anything externally.",
)


@server.tool()
def add_application(job_id: str, status: str = "applied", notes: str = "") -> dict:
    """Create or update a tracked application."""
    app = TrackerService().add_application(job_id, status=status, notes=notes)
    return {"application": app.model_dump(mode="json")}


@server.tool()
def update_status(application_id: str, status: str, note: str = "") -> dict:
    """Update an application status with a history entry."""
    app = TrackerService().update_status(application_id, status, note)
    return {"application": app.model_dump(mode="json")}


@server.tool()
def list_followups(within_days: int = 7) -> dict:
    """List applications whose follow-up is due."""
    apps = TrackerService().followups(within_days)
    return {"followups": [app.model_dump(mode="json") for app in apps]}


@server.tool()
def list_applications(status: str = "") -> dict:
    """List tracked applications."""
    apps = TrackerService().list(status=status, limit=100)
    return {"applications": [app.model_dump(mode="json") for app in apps]}


def main() -> None:
    run_server(server)


if __name__ == "__main__":
    main()
