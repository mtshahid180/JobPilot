"""Extraction pipeline: HTML cleanup, skills, seniority, remote, salary, ids."""

from __future__ import annotations

from datetime import UTC, datetime

from jobpilot.core.models import RawJob
from jobpilot.pipelines.extract import (
    clean_title,
    detect_remote,
    detect_salary,
    detect_seniority,
    extract_job,
    extract_links,
    extract_skills,
    fingerprint,
    strip_html,
)


def test_strip_html_cleans_markup_and_entities():
    raw = "<div><p>Hello&nbsp;<b>World</b> &amp; friends</p>\n\n\n<p>Second</p></div>"
    text = strip_html(raw)
    assert "<" not in text
    assert "Hello World & friends" in text
    assert "\n\n\n" not in text


def test_strip_html_handles_escaped_markup():
    raw = "&lt;p&gt;Build LLM services.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
    text = strip_html(raw)
    assert text == "Build LLM services. Python"


def test_clean_title_removes_remote_markers():
    assert clean_title("Senior ML Engineer (Remote)") == "Senior ML Engineer"
    assert clean_title("  Data Scientist - Hybrid  ") == "Data Scientist"


def test_extract_skills_detects_aliases_without_substring_false_positives():
    text = "Build Python APIs with FastAPI, PostgreSQL, Docker and scikit-learn."
    skills = extract_skills(text)
    assert {"Python", "FastAPI", "PostgreSQL", "Docker", "scikit-learn"} <= set(skills)
    # "Go" must not be detected inside "Google" or "going".
    assert "Go" not in extract_skills("Google is going to launch a new product.")


def test_seniority_detection_prefers_title_then_years():
    assert detect_seniority("Senior Machine Learning Engineer") == "senior"
    assert detect_seniority("Engineering Manager") == "manager"
    assert detect_seniority("Data Analyst", "3+ years of experience") == "mid"
    assert detect_seniority("Software Engineer", "1 year of experience") == "junior"
    assert detect_seniority("Software Engineer", "10+ years of experience") == "lead"
    assert detect_seniority("Software Engineer") == "unspecified"


def test_remote_detection_respects_explicit_flag_and_location():
    assert detect_remote("Engineer", "", "office based role", True) is True
    assert detect_remote("Engineer", "Remote - Worldwide", "great role", None) is True
    assert detect_remote("Engineer", "Berlin, Germany", "on-site team", None) is False


def test_salary_detection_extracts_ranges():
    salary = detect_salary("Compensation: USD 90,000 - 120,000 per year plus equity.")
    assert "90,000" in salary
    assert "120,000" in salary


def test_salary_detection_ignores_currency_like_word_endings():
    assert detect_salary("Join ours, and build with us across teams.") == ""
    assert detect_salary("Rs, is not a salary.") == ""


def test_fingerprint_is_stable_and_case_insensitive():
    first = fingerprint("Senior AI Engineer", "Acme Inc.", "Remote", "https://x.test/j/1")
    second = fingerprint("senior ai engineer", "acme inc", "remote", "https://x.test/j/1/")
    assert first == second
    assert first != fingerprint("Junior AI Engineer", "Acme Inc.", "Remote")


def test_extract_links_dedupes_and_trims_punctuation():
    links = extract_links("See https://a.test/job and https://a.test/job, plus https://b.test.")
    assert links == ["https://a.test/job", "https://b.test"]


def test_extract_job_builds_a_complete_job():
    raw = RawJob(
        source="unit",
        title="Senior AI Engineer (Remote)",
        company="Acme",
        location="Remote",
        description="<p>Python, FastAPI and LLMs. Salary $100,000 per year.</p>",
        url="https://acme.test/jobs/1",
        tags=["RAG"],
        posted_at=datetime(2026, 1, 5, tzinfo=UTC),
    )
    job = extract_job(raw)
    assert job.title == "Senior AI Engineer"
    assert job.remote is True
    assert job.seniority == "senior"
    assert "Python" in job.skills
    assert "LLMs" in job.skills
    assert "RAG" in job.skills
    assert "100,000" in job.salary
    assert "<p>" not in job.description
    assert job.posted_at == datetime(2026, 1, 5, tzinfo=UTC)
    assert len(job.fingerprint) == 40
