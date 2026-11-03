"""Cross-source deduplication helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobpilot.core.models import Job

COMPANY_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|plc|technologies|technology|solutions|labs)\b",
    re.I,
)


def normalize_company(company: str) -> str:
    cleaned = COMPANY_SUFFIXES.sub("", company or "")
    return re.sub(r"[^a-z0-9]", "", cleaned.lower())


def normalize_title(title: str) -> str:
    cleaned = re.sub(r"\b(senior|sr|junior|jr|lead|remote|hybrid|onsite|contract)\b", "", title or "", flags=re.I)
    cleaned = re.sub(r"[\(\[].*?[\)\]]", "", cleaned)
    return re.sub(r"[^a-z0-9]", "", cleaned.lower())


def loose_key(job: Job) -> tuple[str, str, str]:
    return (
        normalize_company(job.company),
        normalize_title(job.title),
        re.sub(r"[^a-z]", "", (job.location or "").lower())[:24],
    )


@dataclass
class DedupeResult:
    unique: list[Job]
    duplicates: int = 0


def dedupe_jobs(jobs: list[Job]) -> DedupeResult:
    """Keep the first occurrence, preferring richer descriptions and direct URLs."""
    seen_fingerprints: set[str] = set()
    seen_loose: set[tuple[str, str, str]] = set()
    unique: list[Job] = []
    duplicates = 0
    ranked = sorted(
        jobs,
        key=lambda j: (
            bool(j.url),
            len(j.description),
            j.posted_at.timestamp() if j.posted_at else 0,
        ),
        reverse=True,
    )
    for job in ranked:
        key = loose_key(job)
        if job.fingerprint in seen_fingerprints or (job.company and key in seen_loose):
            duplicates += 1
            continue
        seen_fingerprints.add(job.fingerprint)
        if job.company:
            seen_loose.add(key)
        unique.append(job)
    return DedupeResult(unique=unique, duplicates=duplicates)
