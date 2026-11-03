"""Shared fixtures.

Every test runs against a throwaway SQLite file and a deterministic offline
configuration: no API key, local embeddings and a fake source registry. No test
may reach the network.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Must be set before ``get_settings`` is imported/cached.
os.environ["OPENAI_API_KEY"] = ""
os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ["ENVIRONMENT"] = "test"

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="jobpilot-tests-"))
os.environ["DATA_DIR"] = str(_TEST_ROOT / "data")
os.environ["TRACE_DIR"] = str(_TEST_ROOT / "traces")

import pytest  # noqa: E402

from jobpilot.core.config import get_settings  # noqa: E402
from jobpilot.core.db import Database  # noqa: E402
from jobpilot.core.models import JobQuery, ProbeResult, RawJob, SourceStatus  # noqa: E402
from jobpilot.pipelines.extract import extract_job  # noqa: E402
from jobpilot.sources.registry import SearchOutcome, SourceRun  # noqa: E402


class FakeRegistry:
    """Deterministic stand-in for the live source registry."""

    def __init__(self, jobs: list | None = None) -> None:
        self.fetched = list(jobs or [])
        self.queries: list[JobQuery] = []
        self.calls: list[dict] = []

    def search(self, query: JobQuery) -> SearchOutcome:
        self.queries.append(query)
        jobs = list(self.fetched)
        runs = {
            "fake-source": SourceRun(
                name="fake-source",
                status="ok" if jobs else "empty",
                count=len(jobs),
                elapsed_ms=1.0,
            )
        }
        return SearchOutcome(
            jobs=jobs,
            sources_used=["fake-source"] if jobs else [],
            errors={},
            duplicates=0,
            tiers_tried=1,
            elapsed_ms=1.0,
            raw_count=len(jobs),
            runs=runs,
        )

    def doctor(self, force: bool = False) -> list[SourceStatus]:
        self.calls.append({"force": force})
        return [
            SourceStatus(
                name="fake-source",
                priority=1,
                enabled=True,
                requires_key=False,
                last_probe=ProbeResult(
                    name="fake-source", ok=True, latency_ms=1.0, detail="test fixture"
                ),
            )
        ]


def reset_singletons() -> None:
    import jobpilot.harness.llm as llm_module
    import jobpilot.harness.loop as loop_module
    import jobpilot.services.jobs as jobs_module
    import jobpilot.services.match as match_module
    import jobpilot.services.resumes as resumes_module
    import jobpilot.services.sessions as sessions_module
    import jobpilot.services.tools as tools_module
    import jobpilot.services.tracker as tracker_module
    import jobpilot.sources.registry as registry_module

    for module, name in (
        (llm_module, "_gateway"),
        (loop_module, "_loop"),
        (jobs_module, "_jobs_service"),
        (match_module, "_match_service"),
        (resumes_module, "_resume_service"),
        (sessions_module, "_sessions"),
        (tools_module, "_tools"),
        (tracker_module, "_tracker"),
        (registry_module, "_registry"),
    ):
        setattr(module, name, None)


@pytest.fixture()
def db(tmp_path):
    """A fresh database injected into every service singleton."""
    import jobpilot.core.db as db_module

    get_settings.cache_clear()
    reset_singletons()
    previous = db_module._db
    database = Database(tmp_path / "jobpilot.sqlite3")
    database.init()
    db_module._db = database
    try:
        yield database
    finally:
        db_module._db = previous
        reset_singletons()


@pytest.fixture()
def registry():
    return FakeRegistry()


@pytest.fixture()
def services(db, registry, monkeypatch):
    """Wired services that share the test database and fake sources."""
    from jobpilot.harness.llm import Gateway
    from jobpilot.services.jobs import JobsService
    from jobpilot.services.match import MatchService
    from jobpilot.services.memory import MemoryService
    from jobpilot.services.profile import ProfileService
    from jobpilot.services.tailor import TailorService
    from jobpilot.services.tools import ToolRegistry
    from jobpilot.services.tracker import TrackerService

    import jobpilot.services.jobs as jobs_module
    import jobpilot.services.match as match_module
    import jobpilot.services.tools as tools_module
    import jobpilot.services.tracker as tracker_module

    jobs = JobsService(db, registry=registry)
    profiles = ProfileService(db)
    match = MatchService(db, jobs, profiles)
    tailor = TailorService(db, jobs, profiles, gateway=Gateway(db=db))
    tracker = TrackerService(db, jobs)
    memory = MemoryService(db)
    tools = ToolRegistry(
        db, jobs=jobs, profiles=profiles, match=match, tailor=tailor, tracker=tracker, memory=memory
    )

    monkeypatch.setattr(jobs_module, "_jobs_service", jobs)
    monkeypatch.setattr(match_module, "_match_service", match)
    monkeypatch.setattr(tracker_module, "_tracker", tracker)
    monkeypatch.setattr(tools_module, "_tools", tools)

    class Services:
        pass

    bundle = Services()
    bundle.jobs = jobs
    bundle.profiles = profiles
    bundle.match = match
    bundle.tailor = tailor
    bundle.tracker = tracker
    bundle.memory = memory
    bundle.tools = tools
    bundle.registry = registry
    bundle.db = db
    return bundle


@pytest.fixture()
def sample_profile(services):
    from eval.fixtures import load_eval_profile

    return services.profiles.save(load_eval_profile())


@pytest.fixture()
def sample_job(services):
    """A realistic AI-engineer posting stored in the test database."""
    raw = RawJob(
        source="fake-source",
        external_id="job-1",
        title="Senior AI Engineer",
        company="Acme Technologies",
        location="Remote",
        description=(
            "<p>We are hiring a Senior AI Engineer to build retrieval-augmented "
            "LLM applications in Python and FastAPI. You will work with "
            "PostgreSQL, Docker and modern evaluation harnesses.</p>"
        ),
        url="https://example.com/jobs/senior-ai-engineer",
        salary="$140,000 - $170,000 per year",
        remote=True,
        tags=["Python", "LLMs"],
    )
    job = extract_job(raw)
    return services.jobs.upsert(job)


@pytest.fixture()
def client(services, db):
    from jobpilot.core.context import user_scope
    from jobpilot.services.users import UserService

    users = UserService(db)
    if users.get("local") is None:
        with user_scope("local"):
            users.create(
                "tester@example.com",
                "test-password-123",
                "Test User",
                user_id="local",
            )

    from jobpilot.ui.app import create_app

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "WTF_CSRF_ENABLED": False,
        }
    )
    test_client = app.test_client()
    with test_client.session_transaction() as sess:
        sess["user_id"] = "local"
        sess["csrf_token"] = "test-csrf"
    return test_client


@pytest.fixture()
def anon_client(services):
    """An unauthenticated browser session, for signup/login tests."""
    from jobpilot.ui.app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
    return app.test_client()


@pytest.fixture()
def second_user(db):
    """A second isolated account, separate from the local test workspace."""
    from jobpilot.core.context import user_scope
    from jobpilot.services.users import UserService

    with user_scope("secondary"):
        return UserService(db).create(
            "second@example.com",
            "second-password-123",
            "Second User",
            user_id="secondary",
        )
