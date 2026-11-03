"""Aggregated dashboard data."""

from __future__ import annotations

from jobpilot.core.db import get_db
from jobpilot.harness.llm import get_gateway
from jobpilot.harness.permissions import ApprovalService
from jobpilot.harness.tracing import RunStore
from jobpilot.services.jobs import get_jobs_service
from jobpilot.services.memory import MemoryService
from jobpilot.services.profile import ProfileService
from jobpilot.services.tracker import get_tracker_service


def dashboard_stats() -> dict:
    db = get_db()
    jobs_service = get_jobs_service()
    tracker = get_tracker_service()
    memory = MemoryService(db)
    profile = ProfileService(db).get()
    gateway = get_gateway()
    run_store = RunStore(db)
    approvals = ApprovalService(db)

    recent_apps = tracker.list(limit=5)
    recent_runs = run_store.list(limit=5)
    return {
        "jobs": jobs_service.stats(),
        "applications": tracker.stats(),
        "memory": memory.stats(),
        "profile_completeness": profile.completeness(),
        "profile_name": profile.full_name,
        "budget": gateway.budget_snapshot(),
        "llm_enabled": gateway.available,
        "runs": run_store.stats(),
        "recent_applications": [
            {
                "id": app.id,
                "status": app.status,
                "job": app.job.model_dump(mode="json") if app.job else None,
                "updated_at": app.updated_at.isoformat(),
            }
            for app in recent_apps
        ],
        "recent_runs": [
            {
                "id": run["id"],
                "goal": run["goal"],
                "status": run["status"],
                "mode": run["mode"],
                "started_at": run["started_at"],
                "summary": run["summary"],
            }
            for run in recent_runs
        ],
        "pending_approvals": approvals.pending_count(),
        "top_skills": jobs_service.top_skills(12),
        "followups": [
            {
                "id": app.id,
                "status": app.status,
                "follow_up_at": app.follow_up_at.isoformat() if app.follow_up_at else None,
                "job": app.job.model_dump(mode="json") if app.job else None,
            }
            for app in tracker.followups(7)
        ],
        "sources": [
            {
                "source": row["source"],
                "count": row["count"],
            }
            for row in jobs_service.stats()["sources"]
        ],
    }
