"""Remotive — free, official remote-job API (no key required)."""

from __future__ import annotations

from typing import Any

from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt

API = "https://remotive.com/api/remote-jobs"

CATEGORY_MAP: dict[str, str] = {
    "technology": "software-dev",
    "data_ai": "data",
    "devops_cloud": "software-dev",
    "finance": "finance-legal",
    "accounting": "finance-legal",
    "banking": "finance-legal",
    "legal": "finance-legal",
    "marketing": "marketing",
    "sales": "sales",
    "hr": "hr",
    "customer_support": "customer-support",
    "design": "design",
    "product": "product",
    "management": "business",
    "operations": "business",
    "admin": "all-others",
    "healthcare": "all-others",
    "education": "all-others",
    "engineering": "engineering",
}


class RemotiveSource(BaseSource):
    name = "remotive"
    priority = 1

    def _probe(self) -> None:
        data = self.get_json(API, params={"limit": 1})
        if "jobs" not in data:
            raise ValueError("unexpected Remotive payload")

    def fetch(self, query: JobQuery) -> list[RawJob]:
        params: dict[str, Any] = {"limit": self._limit(query, 40)}
        if query.query:
            params["search"] = query.query
        category = query.category
        if not category and query.query:
            from jobpilot.pipelines import taxonomy

            category, _confidence = taxonomy.classify_category(query.query)
        slug = CATEGORY_MAP.get(category or "")
        if slug:
            params["category"] = slug
        data = self.get_json(API, params=params)
        jobs: list[RawJob] = []
        for item in data.get("jobs", []):
            candidate_location = str(item.get("candidate_required_location") or "Remote").strip()
            worldwide = candidate_location.lower() in ("worldwide", "anywhere", "global")
            eligible = ["Worldwide"] if worldwide else ([candidate_location] if candidate_location else [])
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("id", "")),
                    title=item.get("title", ""),
                    company=item.get("company_name", "Unknown"),
                    location=candidate_location,
                    job_location="Remote",
                    eligible_locations=eligible,
                    remote_scope="worldwide" if worldwide else ("country" if eligible else "unknown"),
                    location_confidence="verified" if worldwide else ("likely" if eligible else "unknown"),
                    description=excerpt(item.get("description", ""), 20000),
                    url=item.get("url", ""),
                    salary=item.get("salary", "") or "",
                    employment_type=item.get("job_type", "") or "",
                    remote=True,
                    workplace_type="remote",
                    posted_at=item.get("publication_date"),
                    tags=list(item.get("tags", []) or [])[:20],
                    department=str(item.get("category") or ""),
                    raw={
                        "category": item.get("category"),
                        "logo": item.get("company_logo"),
                        "candidate_required_location": candidate_location,
                    },
                )
            )
        return jobs
