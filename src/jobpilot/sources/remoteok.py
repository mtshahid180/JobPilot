"""RemoteOK — free public JSON feed.

RemoteOK asks that consumers link back to the original posting. The UI always
shows the source and links out, and the raw payload is kept locally.
"""

from __future__ import annotations

from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt

API = "https://remoteok.com/api"


class RemoteOKSource(BaseSource):
    name = "remoteok"
    priority = 1

    def _probe(self) -> None:
        data = self.get_json(API)
        if not isinstance(data, list):
            raise ValueError("unexpected RemoteOK payload")

    def fetch(self, query: JobQuery) -> list[RawJob]:
        data = self.get_json(API)
        terms = [term.lower() for term in _terms(query) if term]
        jobs: list[RawJob] = []
        for item in data:
            if not isinstance(item, dict) or not item.get("position"):
                continue
            description = excerpt(item.get("description", ""), 20000)
            title = item.get("position", "")
            haystack = f"{title} {description} {' '.join(item.get('tags', []))}".lower()
            if terms and not any(term in haystack for term in terms):
                continue
            salary = ""
            if item.get("salary_min") or item.get("salary_max"):
                salary = f"${item.get('salary_min', 0):,} - ${item.get('salary_max', 0):,}"
            location = str(item.get("location") or "Remote")
            generic = location.lower() in ("remote", "worldwide", "anywhere", "global")
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("id", "")),
                    title=title,
                    company=item.get("company", "Unknown"),
                    location=location,
                    job_location="Remote",
                    eligible_locations=["Worldwide"] if generic else [location],
                    remote_scope="worldwide" if generic else "country",
                    location_confidence="likely",
                    description=description,
                    url=item.get("url") or item.get("apply_url", ""),
                    salary=salary,
                    remote=True,
                    workplace_type="remote",
                    posted_at=item.get("date"),
                    tags=list(item.get("tags", []) or [])[:20],
                    raw={"logo": item.get("company_logo"), "location": location},
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
