"""Unified discovery pipeline: geography, occupation filters, paging, resumes.

These tests never touch the network. The fake registry returns already-extracted
``Job`` objects so the service's filtering, ranking and persistence layers are
exercised directly.
"""

from __future__ import annotations

import io
import time
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.core import locations as geo
from jobpilot.core.config import get_settings
from jobpilot.core.models import Job, JobQuery, RawJob
from jobpilot.pipelines import taxonomy
from jobpilot.pipelines.documents import validate_docx, validate_pdf
from jobpilot.services.jobs import JobsService
from jobpilot.services.profile import ProfileService
from jobpilot.services.resumes import ResumeError, ResumeService
from jobpilot.sources.registry import SearchOutcome, SourceRegistry, SourceRun

RESUME_TEXT = """Jane Ahmed
Finance Manager | Karachi, Pakistan
jane.ahmed@example.com | +92 300 1234567

Summary
Finance professional with nine years of accounting and reporting experience.

Experience
Finance Manager | Acme Pakistan | 2021 - Present
- Led monthly close and IFRS reporting for a PKR 2bn business
- Managed a team of five accountants

Education
MBA Finance | Karachi University | 2015

Skills
Excel, Financial Reporting, IFRS, Budgeting, SQL
"""


def make_job(**kwargs) -> Job:
    defaults = {
        "source": "fake-source",
        "title": "Finance Manager",
        "company": "Acme",
        "location": "Karachi, Pakistan",
        "description": "Financial reporting, IFRS, budgeting and audit support.",
        "category": "finance",
        "category_label": "Finance & Financial Analysis",
        "workplace_type": "onsite",
        "job_location": "Karachi, Pakistan",
        "eligible_locations": ["Karachi, Sindh, Pakistan"],
        "remote_scope": "city",
        "location_confidence": "verified",
        "posted_at": datetime.now(UTC),
        "fingerprint": "job-default",
    }
    return Job(**{**defaults, **kwargs})


@pytest.fixture()
def resumes(services) -> ResumeService:
    return ResumeService(db=services.db, profiles=ProfileService(services.db))


# ---------------------------------------------------------------------------
# Geography and remote eligibility
# ---------------------------------------------------------------------------
def test_onsite_city_match_rejects_other_city():
    karachi = geo.LocationInfo(
        workplace_type="onsite",
        job_location="Karachi, Pakistan",
        location_confidence="verified",
    )
    assert geo.location_match("Karachi", karachi).tier == "exact"
    assert geo.location_match("Karachi", karachi).verified is True
    rejected = geo.location_match("Lahore", karachi)
    assert rejected.accepted is False
    assert rejected.tier == "mismatch"


def test_remote_country_eligibility_is_not_a_city_match():
    remote = geo.LocationInfo(
        workplace_type="remote",
        company_location="New York, United States",
        job_location="Remote",
        eligible_locations=["Pakistan"],
        remote_scope="country",
        location_confidence="verified",
        remote=True,
    )
    # Country-wide eligibility does not prove the role is in Karachi.
    match = geo.location_match("Karachi", remote)
    assert match.accepted is False
    assert match.tier == "mismatch"
    assert "Pakistan" in match.reason and "Karachi" in match.reason

    # A US headquarters must never make a Pakistan-only remote role appear open.
    assert geo.location_match("Toronto", remote).accepted is False


def test_remote_without_eligibility_is_not_a_verified_city_match():
    unknown = geo.LocationInfo(
        workplace_type="remote",
        job_location="Remote",
        remote_scope="unknown",
        location_confidence="unknown",
        remote=True,
    )
    match = geo.location_match("Karachi", unknown)
    assert match.tier == "mismatch"
    assert match.verified is False
    assert match.accepted is False


def test_worldwide_remote_is_open_to_any_country_but_not_a_city():
    worldwide = geo.LocationInfo(
        workplace_type="remote",
        job_location="Remote",
        eligible_locations=["Worldwide"],
        remote_scope="worldwide",
        location_confidence="verified",
        remote=True,
    )
    # Country requests may accept worldwide remote, but a city request may not.
    assert geo.location_match("Canada", worldwide).tier == "remote"
    assert geo.location_match("Canada", worldwide).verified is True
    assert geo.location_match("Toronto", worldwide).accepted is False
    assert geo.location_match("Karachi", worldwide).accepted is False


def test_remote_only_filter_rejects_onsite_roles():
    onsite = geo.LocationInfo(
        workplace_type="onsite",
        job_location="Karachi, Pakistan",
        location_confidence="verified",
    )
    assert geo.location_match("", onsite, workplace_filter="remote").accepted is False


def test_infer_location_reads_country_restriction_and_workplace():
    info = geo.infer_location(
        title="Data Analyst",
        job_location="Remote",
        description="Candidates must be based in Pakistan and eligible to work in Pakistan.",
        remote_flag=True,
    )
    assert info.workplace_type == "remote"
    assert info.remote_scope == "country"
    assert info.eligible_locations == ["Pakistan"]
    assert info.remote is True


# ---------------------------------------------------------------------------
# Intent and taxonomy
# ---------------------------------------------------------------------------
def test_intent_understands_non_technology_roles_and_cities():
    intent = taxonomy.parse_intent("Finance Manager", "Karachi, Pakistan")
    assert intent.category == "finance"
    assert intent.city == "Karachi"
    assert intent.country == "Pakistan"
    assert intent.location_text
    assert any("financial" in term.lower() for term in intent.search_terms)

    nurse = taxonomy.parse_intent("registered nurse", "Toronto")
    assert nurse.category == "healthcare"
    assert nurse.city == "Toronto"
    assert nurse.country == "Canada"


def test_explicit_category_overrides_inferred_category():
    intent = taxonomy.parse_intent("analyst", category="banking")
    assert intent.category == "banking"
    assert intent.category_label


def test_relevance_keeps_related_occupations_and_rejects_unrelated():
    intent = taxonomy.parse_intent("Finance Manager")
    accountant = make_job(
        title="Senior Accountant",
        category="accounting",
        category_label="Accounting & Bookkeeping",
        description="IFRS reporting and month-end close.",
    )
    related = taxonomy.relevance_for_job(accountant, intent)
    assert related.accepted is True
    assert related.tier == "related"

    engineer = make_job(
        title="DevOps Engineer",
        category="devops_cloud",
        category_label="DevOps & Cloud",
        description="Kubernetes, Terraform and CI pipelines.",
    )
    assert taxonomy.relevance_for_job(engineer, intent).accepted is False


# ---------------------------------------------------------------------------
# Service filtering, pagination and cache
# ---------------------------------------------------------------------------
def test_search_filters_unrelated_categories_and_wrong_cities(services, registry):
    registry.fetched = [
        make_job(fingerprint="finance-karachi"),
        make_job(
            fingerprint="accountant-karachi",
            title="Senior Accountant",
            company="Ledger Co",
            category="accounting",
            category_label="Accounting & Bookkeeping",
            description="IFRS reporting, payroll and month-end close.",
        ),
        make_job(
            fingerprint="finance-lahore",
            title="Finance Manager",
            company="Lahore Textiles",
            location="Lahore, Pakistan",
            job_location="Lahore, Pakistan",
            eligible_locations=["Lahore, Punjab, Pakistan"],
        ),
        make_job(
            fingerprint="devops-karachi",
            title="DevOps Engineer",
            company="Cloud Co",
            category="devops_cloud",
            category_label="DevOps & Cloud",
            description="Kubernetes and Terraform.",
        ),
    ]

    jobs, meta = services.jobs.search(
        JobQuery(query="Finance Manager", location="Karachi, Pakistan", limit=20)
    )
    titles = {job.title for job in jobs}
    companies = {job.company for job in jobs}

    assert "Acme" in companies
    assert "Ledger Co" in companies
    assert "Lahore Textiles" not in companies
    assert "Cloud Co" not in companies
    assert meta["counts"]["category_rejected"] >= 1
    assert meta["counts"]["location_rejected"] >= 1
    assert meta["interpreted"]["city"] == "Karachi"
    assert meta["source_runs"][0]["name"] == "fake-source"
    assert titles == {"Finance Manager", "Senior Accountant"}


def test_search_does_not_pad_a_city_with_country_wide_remote(services, registry):
    registry.fetched = [
        make_job(fingerprint="remote-unknown", location="Remote", workplace_type="remote",
                 job_location="Remote", eligible_locations=[], remote_scope="unknown",
                 location_confidence="unknown", remote=True),
        make_job(fingerprint="remote-pk", company="Remote PK", location="Remote",
                 workplace_type="remote", job_location="Remote",
                 eligible_locations=["Pakistan"], remote_scope="country", remote=True),
    ]

    kept, meta = services.jobs.search(
        JobQuery(
            query="Finance Manager",
            location="Karachi, Pakistan",
            include_unverified=False,
            limit=20,
        )
    )
    # Neither listing names Karachi, so neither is presented as a city match.
    assert [job.company for job in kept] == []
    assert meta["counts"]["location_rejected"] == 2
    assert any("Exact-city filter" in note for note in meta["notes"])


def test_search_paginates_without_losing_the_total(services, registry):
    registry.fetched = [
        make_job(fingerprint="page-a", company="Alpha"),
        make_job(fingerprint="page-b", company="Beta"),
        make_job(fingerprint="page-c", company="Gamma"),
    ]
    first, meta_first = services.jobs.search(
        JobQuery(query="Finance Manager", page=1, page_size=1)
    )
    second, meta_second = services.jobs.search(
        JobQuery(query="Finance Manager", page=2, page_size=1)
    )

    assert len(first) == 1 and len(second) == 1
    assert first[0].id != second[0].id
    assert meta_first["total"] == 3
    assert meta_first["total_pages"] == 3
    assert meta_first["page"] == 1
    assert meta_second["page"] == 2


def test_cache_slices_pages_without_refetching_sources(services, registry):
    services.jobs.clear_cache()
    services.jobs.settings = services.jobs.settings.model_copy(
        update={"environment": "development", "search_cache_ttl_seconds": 60}
    )
    registry.fetched = [
        make_job(fingerprint="cache-a", company="Alpha"),
        make_job(fingerprint="cache-b", company="Beta"),
    ]

    first, _ = services.jobs.search(JobQuery(query="Finance Manager", page=1, page_size=1))
    calls_after_first = len(registry.queries)
    assert calls_after_first == 1

    second, meta_second = services.jobs.search(
        JobQuery(query="Finance Manager", page=2, page_size=1)
    )
    assert len(registry.queries) == calls_after_first  # page 2 is sliced from the cache
    assert meta_second["cache"]["hit"] is True
    assert second[0].id != first[0].id

    repeat, meta = services.jobs.search(JobQuery(query="Finance Manager", page=1, page_size=1))
    assert len(registry.queries) == calls_after_first
    assert [job.id for job in repeat] == [job.id for job in first]
    assert meta["cache"]["hit"] is True


def test_registry_reports_slow_source_as_timeout_without_blocking(monkeypatch):
    class Sleepy:
        name = "sleepy"
        priority = 1
        enabled = True
        requires_key = False

        def fetch(self, query):
            time.sleep(0.5)
            return []

    class Quick:
        name = "quick"
        priority = 2
        enabled = True
        requires_key = False

        def fetch(self, query):
            return [
                RawJob(
                    source="quick",
                    external_id="1",
                    title="Finance Manager",
                    company="Quick Co",
                    location="Karachi, Pakistan",
                )
            ]

    settings = get_settings().model_copy(update={"source_budget_seconds": 0.1})
    registry = SourceRegistry(settings=settings)
    registry.adapters = [Sleepy(), Quick()]

    outcome = registry.search(JobQuery(query="Finance Manager", location="Karachi"))

    assert outcome.runs["sleepy"].status == "timeout"
    assert "no response" in outcome.runs["sleepy"].error
    assert outcome.runs["quick"].status == "ok"
    assert len(outcome.jobs) == 1
    assert outcome.sources_used == ["quick"]


# ---------------------------------------------------------------------------
# Resume upload, review-before-apply and storage
# ---------------------------------------------------------------------------
def test_resume_upload_builds_a_draft_without_saving(resumes):
    draft = resumes.upload("resume.txt", RESUME_TEXT.encode("utf-8"), "text/plain")

    assert draft.resume.status == "needs_review"
    assert draft.resume.text_chars > 40
    assert "full_name" in draft.changed_fields
    assert draft.profile_draft.full_name == "Jane Ahmed"
    # The role/location header line feeds both fields, not a single headline.
    assert draft.profile_draft.headline == "Finance Manager"
    assert draft.profile_draft.location == "Karachi, Pakistan"
    assert "Financial Reporting" in draft.profile_draft.skills
    assert any(experience.bullets for experience in draft.profile_draft.experience)

    saved = ProfileService(resumes.db).get()
    assert saved.full_name == "Your Name"
    assert saved.skills == []


def test_resume_apply_merges_only_selected_fields(resumes):
    draft = resumes.upload("resume.txt", RESUME_TEXT.encode("utf-8"), "text/plain")
    profile = resumes.apply(
        draft.resume.id,
        fields=["full_name", "skills", "headline"],
        patch={"headline": "Finance Manager · IFRS"},
    )

    assert profile.full_name == "Jane Ahmed"
    assert "IFRS" in profile.skills
    assert profile.email == ""  # not selected, so the verified profile is untouched
    assert profile.headline == "Finance Manager · IFRS"

    current = resumes.current()
    assert current is not None
    assert current.id == draft.resume.id
    assert current.is_current is True


def test_resume_rejects_unsupported_types_and_reports_empty_files(resumes):
    with pytest.raises(ResumeError):
        resumes.upload("resume.exe", b"binary")

    empty = resumes.upload("empty.txt", b"    ", "text/plain")
    assert empty.resume.status == "failed"
    assert "too little text" in empty.resume.error


def test_resume_docx_extraction(resumes):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body>"
            "<w:p><w:r><w:t>Ali Khan</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Software Engineer</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>ali@example.com</w:t></w:r></w:p>"
            "</w:body></w:document>",
        )
    draft = resumes.upload(
        "cv.docx",
        buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert draft.profile_draft.full_name == "Ali Khan"
    assert draft.profile_draft.email == "ali@example.com"


def test_resume_delete_removes_row_and_file(resumes):
    draft = resumes.upload("resume.txt", RESUME_TEXT.encode("utf-8"), "text/plain")
    path = resumes.settings.data_dir / draft.resume.storage_path
    assert path.exists()

    assert resumes.delete(draft.resume.id) is True
    assert resumes.list() == []
    assert not path.exists()


def test_resume_api_upload_review_apply_and_delete(client, services):
    response = client.post(
        "/api/resumes",
        data={"file": (io.BytesIO(RESUME_TEXT.encode("utf-8")), "resume.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    resume_id = body["resume"]["id"]
    assert body["resume"]["status"] == "needs_review"
    assert "full_name" in body["changed_fields"]
    # Drafts are never exposed with a stale profile.
    assert body["resume"]["profile_draft"]["full_name"] == "Jane Ahmed"

    listing = client.get("/api/resumes").get_json()
    assert [item["id"] for item in listing["resumes"]] == [resume_id]

    applied = client.post(
        f"/api/resumes/{resume_id}/apply", json={"fields": ["full_name", "skills"]}
    ).get_json()
    assert applied["ok"] is True
    assert applied["profile"]["full_name"] == "Jane Ahmed"
    assert services.profiles.get().full_name == "Jane Ahmed"

    current = client.post(f"/api/resumes/{resume_id}/current", json={}).get_json()
    assert current["resume"]["is_current"] is True

    assert client.delete(f"/api/resumes/{resume_id}").get_json()["ok"] is True
    assert client.get("/api/resumes").get_json()["resumes"] == []
    assert client.post("/api/resumes", data={}, content_type="multipart/form-data").status_code == 400


# ---------------------------------------------------------------------------
# Exact geography and strict workplace regressions
# ---------------------------------------------------------------------------
CITY_GROUPS = [
    ("Pakistan", ["Karachi", "Lahore", "Islamabad"]),
    ("Canada", ["Toronto", "Vancouver", "Montreal"]),
    ("Germany", ["Berlin", "Hamburg", "Munich"]),
]


@pytest.mark.parametrize("country,cities", CITY_GROUPS)
def test_exact_city_is_never_expanded(country, cities):
    for requested in cities:
        for candidate in cities:
            info = geo.LocationInfo(
                workplace_type="onsite",
                job_location=f"{candidate}, {country}",
                location_confidence="verified",
            )
            match = geo.location_match(requested, info)
            if candidate == requested:
                assert match.accepted is True
                assert match.tier == "exact"
            else:
                assert match.accepted is False, (requested, candidate)
                assert match.tier == "mismatch", (requested, candidate)


def test_city_request_never_expands_to_region_country_or_company_hq():
    region_only = geo.LocationInfo(
        workplace_type="onsite",
        job_location="Punjab, Pakistan",
        location_confidence="verified",
    )
    country_only = geo.LocationInfo(
        workplace_type="onsite",
        job_location="Pakistan",
        location_confidence="verified",
    )
    hq_only = geo.LocationInfo(
        workplace_type="onsite",
        company_location="Toronto, Canada",
        job_location="",
        location_confidence="unknown",
    )

    assert geo.location_match("Lahore", region_only).accepted is False
    assert geo.location_match("Lahore", country_only).accepted is False
    hq_match = geo.location_match("Toronto", hq_only)
    assert hq_match.accepted is False
    assert "does not state a work location" in hq_match.reason


def test_strict_workplace_filter_and_no_description_inference():
    # A perk mentioned in prose is not evidence of the arrangement.
    assert (
        geo.detect_workplace_type(
            "Data Analyst", "Karachi, Pakistan", "We offer remote work and hybrid flexibility."
        )
        == "unknown"
    )
    assert geo.detect_workplace_type("Data Analyst (Remote)", "Karachi") == "remote"
    assert geo.detect_workplace_type("Data Analyst", "Hybrid - Karachi") == "hybrid"

    remote = geo.LocationInfo(workplace_type="remote", job_location="Remote", remote=True)
    hybrid = geo.LocationInfo(workplace_type="hybrid", job_location="Karachi", remote=True)
    onsite = geo.LocationInfo(workplace_type="onsite", job_location="Karachi")
    unknown = geo.LocationInfo(workplace_type="unknown", job_location="Karachi")

    assert geo.location_match("", remote, workplace_filter="remote").accepted is True
    assert geo.location_match("", hybrid, workplace_filter="remote").accepted is False
    assert geo.location_match("", onsite, workplace_filter="remote").accepted is False
    unknown_match = geo.location_match("", unknown, workplace_filter="remote")
    assert unknown_match.accepted is False
    assert "verifiable workplace type" in unknown_match.reason
    assert geo.location_match("", hybrid, workplace_filter="hybrid").accepted is True
    assert geo.location_match("", remote, workplace_filter="hybrid").accepted is False
    assert geo.location_match("", onsite, workplace_filter="onsite").accepted is True


def test_germany_employer_remote_role_is_not_excluded_for_canadian_search():
    # Employer HQ (Berlin) is irrelevant; the advertised/eligible place decides.
    toronto_remote = geo.LocationInfo(
        workplace_type="remote",
        company_location="Berlin, Germany",
        job_location="Toronto, Canada",
        eligible_locations=["Toronto, Canada"],
        remote_scope="city",
        location_confidence="verified",
        remote=True,
    )
    assert geo.location_match("Toronto", toronto_remote).accepted is True

    canada_remote = geo.LocationInfo(
        workplace_type="remote",
        company_location="Berlin, Germany",
        job_location="Remote",
        eligible_locations=["Canada"],
        remote_scope="country",
        location_confidence="verified",
        remote=True,
    )
    assert geo.location_match("Canada", canada_remote).accepted is True


def test_onsite_job_is_not_hidden_for_unknown_work_authorization(services, registry):
    registry.fetched = [
        make_job(
            fingerprint="toronto-onsite",
            company="Maple Finance",
            location="Toronto, Canada",
            job_location="Toronto, Canada",
            eligible_locations=["Toronto, Ontario, Canada"],
            remote_scope="city",
        )
    ]
    assert services.profiles.get().work_authorization == ""

    jobs, meta = services.jobs.search(
        JobQuery(query="Finance Manager", location="Toronto, Canada", limit=10)
    )
    assert [job.company for job in jobs] == ["Maple Finance"]
    assert meta["failed_sources"] == 0


def test_one_source_failure_does_not_hide_another_sources_results(services):
    class PartialRegistry:
        def search(self, query):
            return SearchOutcome(
                jobs=[make_job(fingerprint="partial-ok")],
                sources_used=["ok-source"],
                errors={"bad-source": "connection reset"},
                duplicates=0,
                tiers_tried=1,
                elapsed_ms=2.0,
                raw_count=1,
                runs={
                    "ok-source": SourceRun(name="ok-source", status="ok", count=1),
                    "bad-source": SourceRun(
                        name="bad-source", status="error", error="connection reset"
                    ),
                },
            )

    jobs_service = JobsService(services.db, registry=PartialRegistry())
    kept, meta = jobs_service.search(
        JobQuery(query="Finance Manager", location="Karachi, Pakistan", limit=10)
    )

    assert [job.company for job in kept] == ["Acme"]
    assert meta["errors"] == {"bad-source": "connection reset"}
    assert meta["failed_sources"] == 1
    assert any("source" in note and "failed" in note for note in meta["notes"])


def test_pakistan_results_are_not_limited_to_one_employer(services, registry):
    companies = ["Devsinc", "Systems Limited", "10Pearls", "Careem", "Devsinc", "Arbisoft"]
    registry.fetched = [
        make_job(
            fingerprint=f"pk-{index}",
            company=company,
            posted_at=datetime.now(UTC) - timedelta(minutes=index),
        )
        for index, company in enumerate(companies)
    ]

    jobs, meta = services.jobs.search(
        JobQuery(query="Finance Manager", location="Karachi, Pakistan", limit=20)
    )

    assert len(jobs) == len(companies)
    assert {job.company for job in jobs} == set(companies)
    assert meta["total"] == len(companies)
    ordered = [job.company for job in jobs]
    # The diversification pass never allows more than two consecutive roles
    # from the same employer when alternatives exist.
    for index in range(len(ordered) - 2):
        assert not (ordered[index] == ordered[index + 1] == ordered[index + 2])


# ---------------------------------------------------------------------------
# Resume mapping, preservation and generated documents
# ---------------------------------------------------------------------------
PROJECT_RESUME = """Samina Iqbal
AI Engineer | Lahore, Pakistan
samina@example.com

Summary
AI engineer with five years of experience building retrieval systems.

Experience
ML Engineer | Systems Ltd | 2022 - Present
- Built RAG pipelines in Python and FastAPI.

Projects
Resume Ranker | Python, FastAPI | https://example.com/ranker
- Ranked 10,000 resumes with embeddings.

Certifications
AWS Certified Machine Learning - Specialty | Amazon Web Services | 2023

Achievements
- Reduced inference latency by 40%.

Skills
Python, FastAPI, PyTorch, SQL
"""


def test_resume_maps_projects_certifications_and_achievements(resumes):
    draft = resumes.upload("samina.txt", PROJECT_RESUME.encode("utf-8"), "text/plain")
    profile = draft.profile_draft

    assert profile.full_name == "Samina Iqbal"
    assert profile.projects and profile.projects[0].name == "Resume Ranker"
    assert {"Python", "FastAPI"}.issubset(set(profile.projects[0].technologies))
    assert profile.certifications[0].name.startswith("AWS Certified")
    assert profile.certifications[0].issuer == "Amazon Web Services"
    assert profile.certifications[0].year == "2023"
    assert any("40%" in item for item in profile.achievements)


def test_resume_apply_preserves_existing_values_for_missing_fields(resumes):
    profiles = ProfileService(resumes.db)
    current = profiles.get()
    current.full_name = "Existing Name"
    current.email = "keep@example.com"
    current.summary = "Existing summary that must survive."
    current.skills = ["Existing Skill"]
    profiles.save(current)

    draft = resumes.upload(
        "minimal.txt",
        (
            "New Name\n\nExperience\nAnalyst | Example Co | 2020 - 2021\n"
            "- Did work with Python and SQL.\n"
        ).encode("utf-8"),
        "text/plain",
    )
    assert draft.profile_draft.email == ""
    assert draft.profile_draft.summary == ""

    resumes.apply(
        draft.resume.id,
        fields=["full_name", "email", "summary", "skills"],
    )

    final = profiles.get()
    assert final.full_name == "New Name"  # present in the resume
    assert final.email == "keep@example.com"  # missing, so preserved
    assert final.summary == "Existing summary that must survive."
    assert final.skills == ["Existing Skill"]


def test_resume_reports_corrupt_pdf_and_docx_clearly(resumes):
    bad_pdf = resumes.upload("broken.pdf", b"this is not a pdf", "application/pdf")
    assert bad_pdf.resume.status == "failed"
    assert "PDF" in bad_pdf.resume.error

    bad_docx = resumes.upload(
        "broken.docx",
        b"this is not a zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert bad_docx.resume.status == "failed"
    assert "DOCX" in bad_docx.resume.error


def test_tailoring_writes_valid_job_specific_files_and_keeps_the_upload(
    services, sample_profile, sample_job, resumes
):
    draft = resumes.upload("resume.txt", RESUME_TEXT.encode("utf-8"), "text/plain")
    resumes.apply(
        draft.resume.id,
        fields=["full_name", "email", "phone", "location", "summary", "skills", "experience"],
    )
    original_path = resumes.settings.data_dir / draft.resume.storage_path
    original_bytes = original_path.read_bytes()

    cv = services.tailor.tailor_cv(sample_job.id)

    assert cv.job_id == sample_job.id
    assert cv.resume_id == draft.resume.id
    assert cv.changes
    assert set(cv.files) == {"docx", "pdf"}

    data_dir = resumes.settings.data_dir
    docx_bytes = (data_dir / cv.files["docx"]).read_bytes()
    pdf_bytes = (data_dir / cv.files["pdf"]).read_bytes()
    assert validate_docx(docx_bytes) == []
    assert validate_pdf(pdf_bytes) == []
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    assert "Jane Ahmed" in document
    assert "EXPERIENCE" in document

    # The uploaded original is never overwritten by a tailored file.
    assert original_path.read_bytes() == original_bytes
    assert cv.files["docx"] != draft.resume.storage_path

