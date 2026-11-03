"""HTTP API and page smoke tests (isolated database, fake sources)."""

from __future__ import annotations

from datetime import UTC, datetime

from jobpilot.core.models import Job
from jobpilot.harness.permissions import ApprovalService

PAGES = [
    "/",
    "/setup",
    "/jobs",
    "/agent",
    "/profile",
    "/tracker",
    "/memory",
    "/drafts",
    "/runs",
    "/settings",
]


def test_every_page_renders(client):
    for path in PAGES:
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200, path
        assert b"JobPilot" in response.data
    assert b"JobPilot" in client.get("/", follow_redirects=True).data


def test_new_account_setup_page_offers_resume_upload(anon_client):
    """First-run setup must expose the resume upload tab, not only paste-CV."""
    with anon_client.session_transaction() as sess:
        sess["csrf_token"] = "csrf-setup-test"
    created = anon_client.post(
        "/signup",
        data={
            "csrf_token": "csrf-setup-test",
            "name": "First Run",
            "email": "firstrun@example.com",
            "password": "correct-horse-1",
            "confirm": "correct-horse-1",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200

    setup = anon_client.get("/setup")
    assert setup.status_code == 200
    html = setup.get_data(as_text=True)
    assert 'data-setup-tab="upload"' in html
    assert 'id="resume-file"' in html
    assert 'id="resume-review"' in html
    assert "js/resume.js" in html


def test_unknown_routes_use_html_and_json_errors(client):
    page = client.get("/definitely-not-a-page")
    assert page.status_code == 404
    assert b"JobPilot" in page.data

    api = client.get("/api/definitely-not-a-page")
    assert api.status_code == 404
    assert api.get_json()["ok"] is False


def test_health_endpoint_reports_database_and_budget(client):
    payload = client.get("/api/health").get_json()
    assert payload["ok"] is True
    assert payload["app"] == "JobPilot"
    assert payload["database"]["ok"] is True
    assert payload["database"]["jobs"] == 0
    assert payload["budget"]["max_total"] > 0
    assert payload["llm_enabled"] is False


def test_dashboard_starts_empty_then_reflects_activity(client, services, sample_job):
    empty = client.get("/api/dashboard").get_json()
    assert empty["jobs"]["total"] == 1
    assert empty["has_activity"] is True

    services.tracker.add_application(sample_job.id, status="applied")
    payload = client.get("/api/dashboard").get_json()
    assert payload["applications"]["total"] == 1
    assert payload["recent_applications"][0]["job"]["id"] == sample_job.id


def test_profile_starts_blank_and_updates(client):
    payload = client.get("/api/profile").get_json()
    assert payload["profile"]["full_name"] == "Your Name"
    assert payload["completeness"] < 50

    assert client.post("/api/profile/demo").status_code == 404

    updated = client.put("/api/profile", json={"full_name": "Sam Rivera", "headline": "AI Engineer"}).get_json()
    assert updated["profile"]["headline"] == "AI Engineer"
    assert updated["profile"]["full_name"] == "Sam Rivera"

    invalid = client.put("/api/profile", json={"experience": "not-a-list"})
    assert invalid.status_code == 400
    assert invalid.get_json()["ok"] is False


def test_first_run_redirects_to_setup_then_dashboard(client):
    first = client.get("/")
    assert first.status_code in (302, 303)
    assert "/setup" in first.headers["Location"]

    setup = client.get("/setup")
    assert setup.status_code == 200
    assert b"Set up your job search" in setup.data

    client.put(
        "/api/profile",
        json={
            "full_name": "Sam Rivera",
            "headline": "AI Engineer",
            "email": "sam@example.com",
            "target_roles": ["AI Engineer"],
        },
    )
    home = client.get("/")
    assert home.status_code == 200
    assert b"Your job search" in home.data


def test_profile_cv_parser_validates_input(client):
    short = client.post("/api/profile/parse-cv", json={"text": "too short"})
    assert short.status_code == 400

    cv = (
        "Ayesha Khan\nAI Engineer | Python, FastAPI\nayesha@example.com\n\n"
        "Summary\nAI engineer building retrieval-augmented LLM applications.\n\n"
        "Skills\nPython, FastAPI, SQL, Docker, LLMs, RAG\n\n"
        "Experience\nAI Engineer at Nexus Labs | 2024 - present\n"
        "- Built a Python and FastAPI retrieval service used by 4 teams.\n"
    )
    parsed = client.post("/api/profile/parse-cv", json={"text": cv})
    assert parsed.status_code == 200
    profile = parsed.get_json()["profile"]
    assert profile["full_name"] == "Ayesha Khan"
    assert profile["email"] == "ayesha@example.com"
    assert "Python" in profile["skills"]


def test_jobs_import_detail_save_and_score(client):
    text = (
        "Senior AI Engineer\nAcme\nRemote\n\n"
        "Build Python, FastAPI, LLM and RAG services with PostgreSQL and Docker."
    )
    imported = client.post("/api/jobs/import", json={"value": text})
    assert imported.status_code == 200
    body = imported.get_json()
    assert body["ok"] is True
    job_id = body["job"]["id"]
    assert body["match"]["score"] > 0

    detail = client.get(f"/api/jobs/{job_id}").get_json()
    assert detail["job"]["id"] == job_id
    assert detail["match"]["job_id"] == job_id

    saved = client.post(f"/api/jobs/{job_id}/save", json={"saved": True}).get_json()
    assert saved["saved"] is True
    listing = client.get("/api/jobs?saved=1").get_json()
    assert [job["id"] for job in listing["jobs"]] == [job_id]

    rescored = client.post(f"/api/jobs/{job_id}/score").get_json()
    assert rescored["ok"] is True
    assert rescored["match"]["score"] > 0

    assert client.get("/api/jobs/job_missing").status_code == 404
    assert client.post("/api/jobs/import", json={"value": " "}).status_code == 400


def test_jobs_search_uses_registry_and_scores_results(client, registry):
    registry.fetched = [
        Job(
            title="AI Engineer",
            company="Acme",
            location="Remote",
            remote=True,
            skills=["Python", "LLMs", "RAG"],
            description="Build LLM applications in Python.",
            posted_at=datetime.now(UTC),
            fingerprint="search-1",
        ),
        Job(
            title="Data Analyst",
            company="Globex",
            location="Islamabad",
            remote=False,
            skills=["SQL", "Excel"],
            description="Reporting and dashboards.",
            posted_at=datetime.now(UTC),
            fingerprint="search-2",
        ),
    ]
    payload = client.post(
        "/api/jobs/search", json={"query": "AI engineer", "remote_only": False, "limit": 10}
    ).get_json()
    assert payload["ok"] is True
    assert payload["count"] == 2
    assert payload["meta"]["sources_used"] == ["fake-source"]
    assert payload["jobs"][0]["score"] >= payload["jobs"][-1]["score"]
    # The pipeline normalises the request before asking sources for listings.
    assert registry.queries[-1].query == "ai engineer"
    assert payload["meta"]["total"] == 2
    assert payload["meta"]["counts"]["exact"] >= 1
    assert payload["meta"]["source_runs"][0]["name"] == "fake-source"


def test_agent_chat_searches_and_preserves_followup_constraints(client, registry):
    registry.fetched = [
        Job(
            title="AI Engineer",
            company="Acme",
            location="Toronto, Canada",
            remote=True,
            skills=["Python", "LLMs"],
            description="Build retrieval-augmented LLM services in Python.",
            posted_at=datetime.now(UTC),
            fingerprint="chat-toronto-1",
        )
    ]

    first = client.post(
        "/api/agent/run",
        json={"message": "AI Engineer in Toronto, Canada", "mode": "local"},
    ).get_json()["reply"]
    assert first["status"] == "completed"
    assert first["steps"][0]["tool"] == "search_jobs"
    session_id = first["session_id"]

    initial = registry.queries[-1]
    assert initial.query == "ai engineer"
    assert initial.city == "Toronto"
    assert initial.country == "Canada"
    assert "Toronto" in initial.location
    assert initial.workplace_type in ("", "any")

    remote = client.post(
        "/api/agent/run",
        json={"message": "Only remote", "session_id": session_id, "mode": "local"},
    ).get_json()["reply"]
    assert remote["steps"][0]["tool"] == "search_jobs"
    remote_query = registry.queries[-1]
    assert remote_query.query == "ai engineer"
    assert remote_query.city == "Toronto"
    assert "Toronto" in remote_query.location
    assert remote_query.workplace_type == "remote"
    assert remote_query.remote_only is True

    moved = client.post(
        "/api/agent/run",
        json={"message": "In London instead", "session_id": session_id, "mode": "local"},
    ).get_json()["reply"]
    assert moved["steps"][0]["tool"] == "search_jobs"
    moved_query = registry.queries[-1]
    assert moved_query.query == "ai engineer"
    assert moved_query.city == "London"
    assert "Toronto" not in moved_query.location
    # "In London instead" changes only the city, so remote survives.
    assert moved_query.workplace_type == "remote"

    junior = client.post(
        "/api/agent/run",
        json={"message": "Show junior positions", "session_id": session_id, "mode": "local"},
    ).get_json()["reply"]
    assert junior["steps"][0]["tool"] == "search_jobs"
    junior_query = registry.queries[-1]
    assert junior_query.query == "ai engineer"
    assert junior_query.city == "London"
    assert junior_query.seniority == "junior"
    assert junior_query.workplace_type == "remote"


def test_application_api_lifecycle(client, sample_job):
    created = client.post(
        "/api/applications",
        json={"job_id": sample_job.id, "status": "applied", "notes": "api test"},
    )
    assert created.status_code == 200
    application = created.get_json()["application"]
    assert application["status"] == "applied"

    listing = client.get("/api/applications").get_json()
    assert listing["stats"]["total"] == 1
    assert "interview" in listing["statuses"]

    updated = client.patch(
        f"/api/applications/{application['id']}",
        json={"status": "interview", "note": "scheduled"},
    ).get_json()
    assert updated["application"]["status"] == "interview"
    assert len(updated["application"]["history"]) == 2

    assert client.post("/api/applications", json={"job_id": "job_missing"}).status_code == 400
    assert client.patch(
        f"/api/applications/{application['id']}", json={"status": "ghosted"}
    ).status_code == 400

    removed = client.delete(f"/api/applications/{application['id']}").get_json()
    assert removed["ok"] is True
    assert client.get("/api/applications").get_json()["stats"]["total"] == 0


def test_memory_api_create_confirm_and_forget(client):
    preference = client.post(
        "/api/memories", json={"content": "I prefer remote-first roles with async teams."}
    ).get_json()
    assert preference["ok"] is True
    assert preference["action"] == "created"

    pending = client.post("/api/memories", json={"content": "My name is Ayesha Khan."}).get_json()
    assert pending["action"] == "pending_confirmation"
    memory_id = pending["memory"]["id"]

    listing = client.get("/api/memories").get_json()
    assert listing["stats"]["active"] == 1
    assert listing["stats"]["pending"] == 1

    approved = client.post(f"/api/memories/{memory_id}/approve").get_json()
    assert approved["memory"]["status"] == "active"
    assert client.get("/api/memories").get_json()["stats"]["pending"] == 0

    assert client.post("/api/memories", json={"content": ""}).status_code == 400
    assert client.delete(f"/api/memories/{memory_id}").get_json()["ok"] is True
    assert client.delete(f"/api/memories/{memory_id}").get_json()["ok"] is False


def test_tailor_drafts_api(client, sample_profile, sample_job):
    cv_response = client.post("/api/tailor/cv", json={"job_id": sample_job.id})
    assert cv_response.status_code == 200
    body = cv_response.get_json()
    cv = body["cv"]
    assert cv["verified"] is True
    assert cv["unsupported_count"] == 0
    assert cv["job_id"] == sample_job.id
    assert cv["changes"]
    assert set(cv["files"]) == {"docx", "pdf"}
    downloads = {item["format"]: item["url"] for item in body["downloads"]}
    assert set(downloads) == {"docx", "pdf"}

    docx_response = client.get(downloads["docx"])
    assert docx_response.status_code == 200
    assert docx_response.data.startswith(b"PK")
    assert "wordprocessingml" in docx_response.headers["Content-Type"]

    pdf_response = client.get(downloads["pdf"])
    assert pdf_response.status_code == 200
    assert pdf_response.data.startswith(b"%PDF")
    assert pdf_response.headers["Content-Type"].startswith("application/pdf")

    letter_response = client.post("/api/tailor/cover-letter", json={"job_id": sample_job.id})
    assert letter_response.status_code == 200
    letter = letter_response.get_json()["cover_letter"]
    assert letter["verified"] is True

    drafts = client.get("/api/drafts").get_json()
    assert len(drafts["cvs"]) == 1
    assert len(drafts["letters"]) == 1
    assert drafts["cvs"][0]["files"]["docx"] == cv["files"]["docx"]
    assert client.get(f"/api/drafts/cv/{cv['id']}").get_json()["cv"]["id"] == cv["id"]
    assert client.get(f"/api/drafts/letter/{letter['id']}").get_json()["cover_letter"]["id"] == letter["id"]
    assert client.get("/api/drafts/cv/cv_missing").status_code == 404
    assert client.get("/api/drafts/letter/cl_missing").status_code == 404
    assert client.get("/api/tailor/cv/cv_missing/download/docx").status_code == 404
    assert client.get(f"/api/tailor/cv/{cv['id']}/download/zip").status_code == 400

    assert client.post("/api/tailor/cv", json={"job_id": "job_missing"}).status_code == 400


def test_approval_api_decides_and_executes_once(client, services):
    approvals = ApprovalService(services.db)
    approval = approvals.create(
        tool="remember",
        args={"content": "My name is Ayesha Khan.", "kind": "semantic"},
        reason="profile fact",
    )

    listing = client.get("/api/approvals").get_json()
    assert listing["pending"] == 1
    assert listing["approvals"][0]["tool"] == "remember"

    decided = client.post(f"/api/approvals/{approval.id}/decide", json={"approve": True}).get_json()
    assert decided["approval"]["status"] == "approved"
    assert decided["execution"]["ok"] is True

    again = client.post(f"/api/approvals/{approval.id}/decide", json={"approve": True})
    assert again.status_code == 409

    missing = client.post("/api/approvals/apr_missing/decide", json={"approve": True})
    assert missing.status_code == 404

    rejected = approvals.create(tool="remember", args={"content": "Another fact."})
    payload = client.post(f"/api/approvals/{rejected.id}/decide", json={"approve": False}).get_json()
    assert payload["approval"]["status"] == "rejected"
    assert payload["execution"] is None


def test_agent_run_local_planner_sessions_and_runs(client):
    reply = client.post(
        "/api/agent/run", json={"message": "What do you remember about my preferences?", "mode": "local"}
    )
    assert reply.status_code == 200
    body = reply.get_json()["reply"]
    assert body["status"] == "completed"
    assert body["mode"] == "local"
    assert body["steps"][0]["tool"] == "list_memories"
    assert body["session_id"].startswith("ses_")
    assert body["run_id"].startswith("run_")

    sessions = client.get("/api/sessions").get_json()["sessions"]
    assert len(sessions) == 1
    session_id = sessions[0]["id"]
    detail = client.get(f"/api/sessions/{session_id}").get_json()
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][1]["role"] == "assistant"

    runs = client.get("/api/runs").get_json()
    assert runs["stats"]["total_runs"] == 1
    run_id = runs["runs"][0]["id"]
    trace = client.get(f"/api/runs/{run_id}").get_json()
    assert trace["ok"] is True
    assert any(event["type"] == "run_finished" for event in trace["trace"])
    assert client.get("/api/runs/run_missing").status_code == 404

    assert client.post("/api/agent/run", json={"message": ""}).status_code == 400


def test_agent_reports_pending_approval_for_profile_fact(client):
    reply = client.post(
        "/api/agent/run",
        json={"message": "Remember that my name is Ayesha Khan.", "mode": "local"},
    ).get_json()["reply"]
    assert reply["status"] == "awaiting_approval"
    assert reply["approvals"][0]["tool"] == "remember"
    assert client.get("/api/approvals").get_json()["pending"] == 1


def test_sources_tools_and_session_management(client, registry):
    sources = client.get("/api/sources").get_json()
    assert sources["sources"][0]["name"] == "fake-source"
    assert sources["sources"][0]["status"] == "live"
    assert registry.calls

    assert client.post("/api/sources/check").get_json()["ok"] is True

    tools = client.get("/api/tools").get_json()["tools"]
    assert len(tools) == 22
    assert {tool["name"] for tool in tools} >= {"search_jobs", "tailor_cv", "request_submission"}

    created = client.post("/api/sessions", json={"title": "Manual session"}).get_json()["session"]
    assert created["title"] == "Manual session"
    assert client.get(f"/api/sessions/{created['id']}").status_code == 200
    assert client.delete(f"/api/sessions/{created['id']}").get_json()["ok"] is True
    assert client.get(f"/api/sessions/{created['id']}").status_code == 404

