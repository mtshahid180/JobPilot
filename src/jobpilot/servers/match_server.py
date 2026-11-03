"""MCP server: match scoring and skill gaps."""

from __future__ import annotations

from jobpilot.servers.mcp_compat import create_server, run_server
from jobpilot.services.match import get_match_service

server = create_server(
    "jobpilot-match",
    instructions="Score jobs against the profile using embeddings and a transparent rubric.",
)


@server.tool()
def score_job(job_id: str) -> dict:
    """Score one job and return the rubric breakdown."""
    return {"match": get_match_service().score_by_id(job_id).model_dump(mode="json")}


@server.tool()
def rank_jobs(limit: int = 10) -> dict:
    """Rank all stored jobs by match score."""
    ranked = get_match_service().rank(limit=max(1, min(limit, 50)))
    return {
        "jobs": [
            {
                **item.job.card(score=item.match.score).model_dump(mode="json"),
                "rationale": item.match.rationale,
            }
            for item in ranked
        ]
    }


@server.tool()
def skill_gap(job_id: str) -> dict:
    """Compare job requirements with profile evidence."""
    return {"gap": get_match_service().skill_gap(job_id).model_dump(mode="json")}


def main() -> None:
    run_server(server)


if __name__ == "__main__":
    main()
