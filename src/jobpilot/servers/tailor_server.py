"""MCP server: verified CV and cover-letter generation."""

from __future__ import annotations

from jobpilot.servers.mcp_compat import create_server, run_server
from jobpilot.services.tailor import TailorService

server = create_server(
    "jobpilot-tailor",
    instructions=(
        "Generate job-specific CVs and cover letters strictly from profile facts. "
        "Every claim is verified before it is returned."
    ),
)


@server.tool()
def tailor_cv(job_id: str) -> dict:
    """Generate a verified, job-specific CV."""
    cv = TailorService().tailor_cv(job_id)
    return {"cv": cv.model_dump(mode="json"), "verified": cv.verified, "unsupported": cv.unsupported_count}


@server.tool()
def draft_cover_letter(job_id: str) -> dict:
    """Draft a verified cover letter."""
    letter = TailorService().draft_cover_letter(job_id)
    return {
        "cover_letter": letter.model_dump(mode="json"),
        "verified": letter.verified,
        "unsupported": letter.unsupported_count,
    }


def main() -> None:
    run_server(server)


if __name__ == "__main__":
    main()
