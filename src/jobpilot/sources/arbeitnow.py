"""Arbeitnow — free public job board API (no key required)."""

from __future__ import annotations

from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt

API = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowSource(BaseSource):
    name = "arbeitnow"
    priority = 1

    def _probe(self) -> None:
        data = self.get_json(API)
        if "data" not in data:
            raise ValueError("unexpected Arbeitnow payload")

    def fetch(self, query: JobQuery) -> list[RawJob]:
        data = self.get_json(API)
        items = data.get("data", [])
        jobs: list[RawJob] = []
        terms = [term.lower() for term in _terms(query) if term]
        for item in items:
            title = item.get("title", "")
            description = excerpt(item.get("description", ""), 20000)
            haystack = f"{title} {description}".lower()
            if terms and not any(term in haystack for term in terms):
                continue
            tags = list(item.get("tags", []) or [])
            location = str(item.get("location", "") or "")
            remote = bool(item.get("remote", False))
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("slug", "")),
                    title=title,
                    company=item.get("company_name", "Unknown"),
                    location=location,
                    job_location="Remote" if remote else location,
                    eligible_locations=["Worldwide"] if remote and not location else ([location] if location else []),
                    remote_scope="worldwide" if remote and not location else ("country" if location else "unknown"),
                    location_confidence="likely" if location else "unknown",
                    description=description,
                    url=item.get("url", ""),
                    remote=remote,
                    workplace_type="remote" if remote else ("onsite" if location else "unknown"),
                    posted_at=item.get("created_at"),
                    tags=tags[:20],
                    employment_type=", ".join(item.get("job_types", []) or []),
                    raw={"tags": tags, "location": location},
                )
            )
            if len(jobs) >= self._limit(query, 40):
                break
        return jobs


def _terms(query: JobQuery) -> list[str]:
    values = [query.query, *query.search_terms]
    result: list[str] = []
    for value in values:
        for token in str(value or "").lower().split():
            if len(token) > 2 and token not in result:
                result.append(token)
    return result[:6]
