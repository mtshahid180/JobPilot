"""Cross-source deduplication."""

from __future__ import annotations

from datetime import UTC, datetime

from jobpilot.core.models import Job
from jobpilot.pipelines.dedupe import dedupe_jobs, loose_key, normalize_company, normalize_title


def make_job(**kwargs) -> Job:
    defaults = dict(
        title="AI Engineer",
        company="Acme Inc.",
        location="Remote",
        description="Build Python services.",
        url="https://acme.test/1",
        fingerprint="fp-1",
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return Job(**{**defaults, **kwargs})


def test_company_and_title_normalisation():
    assert normalize_company("Acme Technologies, Inc.") == "acme"
    assert normalize_company("Acme Labs Ltd") == "acme"
    assert normalize_title("Senior ML Engineer (Remote)") == "mlengineer"
    assert normalize_title("Sr. Backend Developer - Contract") == "backenddeveloper"


def test_loose_key_ignores_seniority_and_punctuation():
    first = make_job(title="Senior AI Engineer", company="Acme, Inc.", location="Remote")
    second = make_job(title="AI Engineer", company="Acme", location="remote")
    assert loose_key(first) == loose_key(second)


def test_same_fingerprint_is_deduplicated():
    first = make_job()
    second = make_job(title="Different title", fingerprint="fp-1")
    result = dedupe_jobs([first, second])
    assert len(result.unique) == 1
    assert result.duplicates == 1


def test_cross_source_duplicates_keep_the_richer_posting():
    short = make_job(
        source="a",
        fingerprint="a-1",
        description="Short.",
        url="",
    )
    rich = make_job(
        source="b",
        fingerprint="b-1",
        description="A much richer description with responsibilities, stack and benefits.",
        url="https://acme.test/2",
    )
    result = dedupe_jobs([short, rich])
    assert len(result.unique) == 1
    assert result.unique[0].description.startswith("A much richer")
    assert result.duplicates == 1


def test_distinct_roles_are_kept():
    ai = make_job(title="AI Engineer", fingerprint="a")
    design = make_job(title="Product Designer", company="Acme", fingerprint="b")
    other = make_job(title="AI Engineer", company="Globex", fingerprint="c")
    result = dedupe_jobs([ai, design, other])
    assert len(result.unique) == 3
    assert result.duplicates == 0
