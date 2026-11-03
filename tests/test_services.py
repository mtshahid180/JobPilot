"""Service layer: storage, scoring, tracking, memory and verified tailoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.core.models import Job, JobQuery, Profile
from jobpilot.harness.permissions import (
    CONFIRMATION,
    EXTERNAL,
    READ,
    WRITE_LOCAL,
    ApprovalService,
    PermissionPolicy,
)
from jobpilot.pipelines.extract import extract_job
from jobpilot.services.tools import ToolContext, ToolError, execute_approval


def make_job(**kwargs) -> Job:
    defaults = dict(
        source="unit",
        title="AI Engineer",
        company="Acme",
        location="Remote",
        description="Python, FastAPI, LLMs and RAG.",
        remote=True,
        workplace_type="remote",
        job_location="Remote",
        seniority="mid",
        skills=["Python", "FastAPI", "LLMs", "RAG"],
        posted_at=datetime.now(UTC),
        fingerprint="unit-fingerprint",
    )
    return Job(**{**defaults, **kwargs})


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------
def test_upsert_updates_existing_fingerprint_instead_of_duplicating(services):
    first = services.jobs.upsert(make_job())
    second = services.jobs.upsert(make_job(title="AI Engineer II", description="Updated description"))
    assert first.id == second.id
    assert services.jobs.count() == 1
    assert services.jobs.get(first.id).description == "Updated description"


def test_get_sanitizes_legacy_escaped_html_in_descriptions(services):
    job = services.jobs.upsert(
        make_job(description="&lt;p&gt;Build LLM tools.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;")
    )
    fetched = services.jobs.get(job.id)
    assert fetched is not None
    assert "<" not in fetched.description
    assert "Build LLM tools. Python" in fetched.description


def test_search_stores_fake_results_and_reports_meta(services, registry):
    registry.fetched = [
        make_job(fingerprint="a", title="AI Engineer"),
        make_job(fingerprint="b", title="ML Engineer", company="Globex"),
    ]
    stored, meta = services.jobs.search(JobQuery(query="AI"))
    assert len(stored) == 2
    assert meta["sources_used"] == ["fake-source"]
    assert meta["raw_count"] == 2
    assert services.jobs.count() == 2


def test_list_filters_search_location_remote_and_saved(services):
    remote = services.jobs.upsert(make_job(fingerprint="r", title="Remote ML Engineer"))
    onsite = services.jobs.upsert(
        make_job(
            fingerprint="o",
            title="Data Analyst",
            company="Local Co",
            location="Islamabad",
            remote=False,
            workplace_type="onsite",
            job_location="Islamabad, Pakistan",
            description="SQL and Excel reporting.",
        )
    )
    services.jobs.set_saved(onsite.id, True)

    assert [job.id for job in services.jobs.list(search="ML")] == [remote.id]
    assert [job.id for job in services.jobs.list(location="Islamabad")] == [onsite.id]
    assert [job.id for job in services.jobs.list(remote_only=True)] == [remote.id]
    assert [job.id for job in services.jobs.list(saved_only=True)] == [onsite.id]
    assert services.jobs.stats()["total"] == 2


def test_import_job_from_pasted_text_and_rejects_empty(services):
    text = (
        "Senior Platform Engineer\nGlobex\nRemote\n\n"
        "We build Python services with Docker, Kubernetes and PostgreSQL."
    )
    job = services.jobs.import_job(text)
    assert job.title
    assert services.jobs.get(job.id) is not None
    with pytest.raises(ValueError):
        services.jobs.import_job("   ")


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------
def test_scoring_prefers_relevant_recent_roles(services, sample_profile):
    strong = services.jobs.upsert(
        make_job(
            fingerprint="strong",
            title="AI Engineer",
            skills=["Python", "LLMs", "RAG", "FastAPI", "PostgreSQL"],
            description="Build retrieval-augmented LLM applications in Python and FastAPI.",
            posted_at=datetime.now(UTC),
        )
    )
    weak = services.jobs.upsert(
        make_job(
            fingerprint="weak",
            title="Graphic Designer",
            company="Studio",
            location="Karachi",
            remote=False,
            seniority="senior",
            skills=["Figma", "Photoshop"],
            description="Brand illustration and print design.",
            posted_at=datetime.now(UTC) - timedelta(days=200),
        )
    )
    strong_match = services.match.score_job(strong, sample_profile)
    weak_match = services.match.score_job(weak, sample_profile)

    assert 0 <= strong_match.score <= 100
    assert strong_match.score > weak_match.score
    assert strong_match.matched_skills
    assert strong_match.rationale
    assert "Python" in strong_match.matched_skills
    assert strong_match.breakdown.total() == pytest.approx(strong_match.score, abs=0.05)


def test_breakdown_weights_sum_to_one():
    from jobpilot.core.models import ScoreBreakdown

    full = ScoreBreakdown(skills=1, role=1, location=1, seniority=1, recency=1, semantic=1)
    assert full.total() == pytest.approx(100.0)
    empty = ScoreBreakdown()
    assert empty.total() == 0.0


def test_skill_gap_lists_missing_skills(services, sample_profile):
    job = services.jobs.upsert(
        make_job(
            fingerprint="gap",
            title="ML Platform Engineer",
            skills=["Python", "Kubernetes", "Terraform"],
        )
    )
    gap = services.match.skill_gap(job.id)
    assert "Python" in gap.have
    assert "Kubernetes" in gap.missing
    assert gap.coverage == pytest.approx(1 / 3, abs=0.01)
    assert gap.suggestions


def test_rank_orders_by_score(services, sample_profile):
    services.jobs.upsert(make_job(fingerprint="rank-a", title="AI Engineer"))
    services.jobs.upsert(
        make_job(
            fingerprint="rank-b",
            title="Graphic Designer",
            company="Studio",
            skills=["Figma"],
            remote=False,
            location="Karachi",
        )
    )
    ranked = services.match.rank(limit=10)
    assert len(ranked) == 2
    assert ranked[0].match.score >= ranked[1].match.score


# ---------------------------------------------------------------------------
# tracker
# ---------------------------------------------------------------------------
def test_application_lifecycle_and_stats(services, sample_job):
    app = services.tracker.add_application(sample_job.id, status="applied", notes="via test")
    assert app.status == "applied"
    assert app.applied_at is not None
    assert app.follow_up_at is not None
    assert len(app.history) == 1
    assert services.jobs.get(sample_job.id).status == "applied"

    updated = services.tracker.update_status(app.id, "interview", note="first round booked")
    assert updated.status == "interview"
    assert len(updated.history) == 2
    assert updated.history[-1].note == "first round booked"

    stats = services.tracker.stats()
    assert stats["total"] == 1
    assert stats["interviews"] == 1
    assert stats["active"] == 1

    assert services.tracker.followups(7)
    assert services.tracker.by_job(sample_job.id).id == app.id

    assert services.tracker.delete(app.id) is True
    assert services.tracker.get(app.id) is None
    assert services.jobs.get(sample_job.id).status == "new"


def test_application_rejects_unknown_status_and_job(services, sample_job):
    with pytest.raises(ValueError):
        services.tracker.add_application(sample_job.id, status="ghosted")
    with pytest.raises(ValueError):
        services.tracker.add_application("job_missing")


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------
def test_preference_is_stored_immediately_and_can_be_recalled(services):
    result = services.memory.remember(
        "I prefer remote-first roles with async teams.", provenance="test"
    )
    assert result.action == "created"
    assert result.memory.status == "active"
    assert result.memory.kind == "preference"

    recalled = services.memory.recall("do I want remote work?", limit=3)
    assert any("remote-first" in memory.content for memory in recalled)
    assert services.memory.stats()["active"] == 1


def test_profile_fact_waits_for_confirmation_then_activates(services):
    result = services.memory.remember("My name is Ayesha Khan.")
    assert result.action == "pending_confirmation"
    assert result.memory.status == "pending"
    assert services.memory.count("pending") == 1

    approved = services.memory.approve(result.memory.id)
    assert approved.status == "active"
    assert services.memory.count("pending") == 0


def test_conflicting_preference_is_flagged_not_overwritten(services):
    first = services.memory.remember("I only want fully remote roles.", provenance="test")
    conflict = services.memory.remember("I only want onsite only roles.", provenance="test")
    assert first.action == "created"
    assert conflict.action == "conflict"
    assert conflict.memory.status == "pending"
    assert conflict.memory.supersedes == first.memory.id

    services.memory.reject(conflict.memory.id)
    assert services.memory.count("pending") == 0


def test_forget_reports_missing_memories(services):
    result = services.memory.remember("I prefer contract roles.", provenance="test")
    assert services.memory.forget(result.memory.id) is True
    assert services.memory.forget(result.memory.id) is False
    assert services.memory.forget("mem_missing") is False


# ---------------------------------------------------------------------------
# tailoring
# ---------------------------------------------------------------------------
def test_local_cv_is_fully_verified_and_uses_profile_evidence(services, sample_profile, sample_job):
    cv = services.tailor.tailor_cv(sample_job.id)
    assert cv.verified is True
    assert cv.unsupported_count == 0
    assert 1 <= len(cv.bullets) <= 6
    allowed = set(sample_profile.all_bullets) | {sample_profile.summary}
    for bullet in cv.bullets:
        assert bullet.text in allowed
        assert bullet.supported is True
    assert cv.job is not None and cv.job.id == sample_job.id
    assert cv.headline

    stored = services.tailor.get_cv(cv.id)
    assert stored is not None
    assert stored.verified is True
    assert services.tailor.list_cvs()


def test_cover_letter_is_verified_and_signed(services, sample_profile, sample_job):
    letter = services.tailor.draft_cover_letter(sample_job.id)
    assert letter.verified is True
    assert letter.unsupported_count == 0
    assert sample_profile.full_name in letter.body
    assert sample_job.title in letter.body
    assert letter.checks
    assert all(check.supported for check in letter.checks)
    assert services.tailor.get_letter(letter.id) is not None


def test_tailoring_requires_profile_facts(services, sample_job):
    services.profiles.save(Profile())
    with pytest.raises(ValueError):
        services.tailor.tailor_cv(sample_job.id)
    with pytest.raises(ValueError):
        services.tailor.draft_cover_letter(sample_job.id)


def test_tailoring_unknown_job_raises(services, sample_profile):
    with pytest.raises(ValueError):
        services.tailor.tailor_cv("job_missing")


# ---------------------------------------------------------------------------
# permissions and tools
# ---------------------------------------------------------------------------
def test_permission_tiers():
    policy = PermissionPolicy(
        {
            "search_jobs": READ,
            "tailor_cv": WRITE_LOCAL,
            "forget": CONFIRMATION,
            "request_submission": EXTERNAL,
        }
    )
    assert policy.decide("search_jobs").allowed is True
    assert policy.decide("tailor_cv").allowed is True
    write = policy.decide("forget")
    assert write.allowed is False and write.requires_approval is True
    external = policy.decide("request_submission")
    assert external.allowed is False and external.tier == EXTERNAL
    assert policy.decide("unknown_tool").requires_approval is True


def test_tool_registry_validates_and_executes(services, sample_profile):
    assert len(services.tools.specs) == 22
    with pytest.raises(ToolError):
        services.tools.get("does_not_exist")
    with pytest.raises(ToolError):
        services.tools.call("get_job", {})
    profile_result = services.tools.call("get_profile", {}, ToolContext())
    assert profile_result["profile"]["full_name"] == sample_profile.full_name
    catalog = {tool["name"]: tool for tool in services.tools.tools_by_namespace()["jobs"]}
    assert "search_jobs" in catalog


def test_approved_memory_write_is_replayed(services):
    approvals = ApprovalService(services.db)
    approval = approvals.create(
        tool="remember",
        args={"content": "My name is Ayesha Khan.", "kind": "semantic"},
        reason="profile fact",
        tier=CONFIRMATION,
    )
    execution = execute_approval(approval)
    assert execution["ok"] is True
    assert execution["result"]["action"] == "created"
    active = services.memory.list(status="active")
    assert any("Ayesha" in memory.content for memory in active)

