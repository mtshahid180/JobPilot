"""Free ATS source adapters: Greenhouse, Workable and the Indeed policy stub."""

from __future__ import annotations

from jobpilot.core.config import get_settings
from jobpilot.core.models import JobQuery
from jobpilot.pipelines.extract import extract_job
from jobpilot.sources.greenhouse import GreenhouseSource
from jobpilot.sources.indeed import IndeedSource
from jobpilot.sources.registry import SourceRegistry
from jobpilot.sources.workable import WorkableSource

GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 101,
            "title": "Senior AI Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
            "updated_at": "2026-10-20T10:00:00-04:00",
            "first_published": "2026-10-10T10:00:00-04:00",
            "location": {"name": "Remote — Europe"},
            "company_name": "Acme",
            "departments": [{"name": "Engineering"}],
            "content": (
                "&lt;p&gt;Build LLM and RAG services in Python and FastAPI. "
                "Work with PostgreSQL and Docker.&lt;/p&gt;"
            ),
        },
        {
            "id": 102,
            "title": "Account Executive",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/102",
            "updated_at": "2026-10-19T10:00:00-04:00",
            "location": {"name": "New York"},
            "company_name": "Acme",
            "departments": [{"name": "Sales"}],
            "content": "&lt;p&gt;Sell the platform.&lt;/p&gt;",
        },
    ]
}

WORKABLE_PAYLOAD = {
    "name": "Devsinc",
    "jobs": [
        {
            "title": "Machine Learning Engineer",
            "shortcode": "ABC123",
            "url": "https://apply.workable.com/j/ABC123",
            "employment_type": "Full-time",
            "telecommuting": True,
            "department": "Data",
            "created_at": "2026-09-15",
            "description": "<p>Python, LLMs, RAG and data pipelines.</p>",
            "locations": [
                {"city": "Lahore", "region": "Punjab", "country": "Pakistan"},
                {"city": "", "region": "", "country": "Remote"},
            ],
        },
        {
            "title": "Sales Manager",
            "shortcode": "XYZ999",
            "url": "https://apply.workable.com/j/XYZ999",
            "employment_type": "Full-time",
            "telecommuting": False,
            "department": "Sales",
            "created_at": "2026-09-10",
            "description": "<p>Own the pipeline.</p>",
            "locations": [{"city": "Karachi", "region": "Sindh", "country": "Pakistan"}],
        },
    ],
}


def test_greenhouse_parses_public_board_payload(monkeypatch):
    settings = get_settings().model_copy(
        update={"greenhouse_boards": "acme", "enable_greenhouse": True}
    )
    adapter = GreenhouseSource(settings)

    def fake_get_json(url, params=None):
        assert "boards-api.greenhouse.io" in url
        return GREENHOUSE_PAYLOAD

    monkeypatch.setattr(adapter, "get_json", fake_get_json)
    jobs = adapter.fetch(JobQuery(query="AI engineer", limit=10))

    assert len(jobs) == 1
    raw = jobs[0]
    assert raw.source == "greenhouse"
    assert raw.external_id == "101"
    assert raw.company == "Acme"
    assert raw.location == "Remote — Europe"
    assert raw.remote is True

    extracted = extract_job(raw)
    assert "Python" in extracted.skills
    assert "LLMs" in extracted.skills
    assert "RAG" in extracted.skills
    assert "Senior" in extracted.title
    assert extracted.posted_at is not None


def test_workable_parses_account_payload_and_locations(monkeypatch):
    settings = get_settings().model_copy(
        update={"workable_accounts": "devsinc-17", "enable_workable": True}
    )
    adapter = WorkableSource(settings)
    monkeypatch.setattr(adapter, "get_json", lambda url, params=None: WORKABLE_PAYLOAD)

    jobs = adapter.fetch(JobQuery(query="machine learning", limit=10))

    assert len(jobs) == 1
    raw = jobs[0]
    assert raw.source == "workable"
    assert raw.external_id == "ABC123"
    assert raw.company == "Devsinc"
    assert raw.location == "Lahore, Punjab, Pakistan · Remote"
    assert raw.remote is True
    assert raw.employment_type == "Full-time"

    extracted = extract_job(raw)
    assert "Python" in extracted.skills
    assert "RAG" in extracted.skills
    assert extracted.remote is True


def test_source_boards_are_configurable():
    settings = get_settings()
    assert settings.greenhouse_board_list
    assert settings.workable_account_list
    assert settings.enable_greenhouse is True
    assert settings.enable_workable is True


def test_registry_registers_free_sources_and_disabled_policy_stubs():
    names = {adapter.name for adapter in SourceRegistry(get_settings()).adapters}
    assert {"greenhouse", "workable"}.issubset(names)
    assert "indeed" in names
    assert "linkedin" in names


def test_indeed_is_always_disabled_by_policy():
    adapter = IndeedSource(get_settings())
    assert adapter.enabled is False
    assert adapter.fetch(JobQuery(query="engineer")) == []
    probe = adapter.probe()
    assert probe.ok is False
    assert "policy" in probe.detail.lower()
