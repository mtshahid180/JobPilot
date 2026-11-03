"""Two accounts must never see each other's workspace."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobpilot.core.context import user_scope
from jobpilot.harness.llm import Gateway
from jobpilot.harness.permissions import ApprovalService
from jobpilot.harness.tracing import RunStore
from jobpilot.memory.store import MemoryStore
from jobpilot.services.sessions import SessionService


def _matching_jobs(services):
    from jobpilot.core.models import Job

    return Job(
        source="unit",
        title="AI Engineer",
        company="Acme",
        location="Remote",
        description="Python, FastAPI and LLM systems.",
        remote=True,
        skills=["Python", "FastAPI", "LLMs"],
        posted_at=datetime.now(UTC),
        fingerprint="isolation-job",
    )


def test_jobs_and_saved_state_are_scoped_per_user(services, second_user):
    job = services.jobs.upsert(_matching_jobs(services))
    services.jobs.set_saved(job.id, True)

    assert services.jobs.get(job.id).saved is True
    assert services.jobs.stats()["total"] == 1

    with user_scope(second_user.id):
        assert services.jobs.get(job.id) is None
        assert services.jobs.list() == []
        assert services.jobs.stats()["total"] == 0
        assert services.jobs.all_fingerprints() == set()


def test_profile_is_scoped_per_user(services, second_user):
    profile = services.profiles.get()
    profile.full_name = "Local Owner"
    profile.skills = ["Python"]
    services.profiles.save(profile)

    assert services.profiles.get().full_name == "Local Owner"

    with user_scope(second_user.id):
        other = services.profiles.get()
        assert other.full_name != "Local Owner"
        assert other.skills == []


def test_applications_are_scoped_per_user(services, second_user):
    job = services.jobs.upsert(_matching_jobs(services))
    application = services.tracker.add_application(job.id, status="applied")

    assert services.tracker.by_job(job.id).id == application.id
    assert services.tracker.stats()["total"] == 1

    with user_scope(second_user.id):
        assert services.tracker.by_job(job.id) is None
        assert services.tracker.list() == []
        assert services.tracker.stats()["total"] == 0
        with pytest.raises(ValueError, match="job not found"):
            services.tracker.add_application(job.id, status="applied")


def test_memory_is_scoped_per_user(services, second_user):
    store = MemoryStore(services.db)
    store.remember("Prefers remote-first AI engineering roles.", kind="preference")

    assert len(store.list(status="all")) == 1

    with user_scope(second_user.id):
        assert store.list(status="all") == []
        assert store.count("active") == 0
        assert store.recall("remote engineering") == []


def test_sessions_and_messages_are_scoped_per_user(services, second_user):
    sessions = SessionService(services.db)
    session = sessions.create("Local session")
    sessions.add_message(session["id"], "user", "Find AI roles")

    assert sessions.get(session["id"]) is not None
    assert len(sessions.messages(session["id"])) == 1

    with user_scope(second_user.id):
        assert sessions.get(session["id"]) is None
        assert sessions.list() == []
        with pytest.raises(ValueError, match="session not found"):
            sessions.add_message(session["id"], "user", "sneaky")


def test_runs_and_approvals_are_scoped_per_user(services, second_user):
    runs = RunStore(services.db)
    runs.create("run_local", "local goal")
    runs.finish("run_local", "completed", summary="done")

    approvals = ApprovalService(services.db)
    approval = approvals.create("external_tool", {"x": 1}, reason="unit")

    assert runs.get("run_local") is not None
    assert approvals.get(approval.id) is not None
    assert approvals.pending_count() == 1
    assert runs.stats()["total_runs"] == 1

    with user_scope(second_user.id):
        assert runs.get("run_local") is None
        assert runs.list() == []
        assert runs.stats()["total_runs"] == 0
        assert approvals.get(approval.id) is None
        assert approvals.list(status="all") == []
        assert approvals.pending_count() == 0


def test_budget_is_scoped_per_user(services, second_user):
    gateway = Gateway(db=services.db)
    gateway._record_spend(0.25, 1000)

    assert gateway.spent_total == pytest.approx(0.25)
    assert gateway.spent_today == pytest.approx(0.25)

    with user_scope(second_user.id):
        assert gateway.spent_total == 0.0
        assert gateway.spent_today == 0.0
