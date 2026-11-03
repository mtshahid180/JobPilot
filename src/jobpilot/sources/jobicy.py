"""Jobicy — free remote-job API with industry and geo filters."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from jobpilot.core import locations as geo
from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt

API = "https://jobicy.com/api/v2/remote-jobs"
PAGE_SIZE = 50

INDUSTRY_MAP: dict[str, str] = {
    "finance": "business",
    "accounting": "business",
    "banking": "business",
    "management": "business",
    "operations": "business",
    "product": "business",
    "sales": "marketing",
    "marketing": "marketing",
    "design": "marketing",
    "technology": "engineering",
    "engineering": "engineering",
    "devops_cloud": "engineering",
    "data_ai": "data-science",
    "customer_support": "supporting",
    "hr": "hr",
    "legal": "legal",
    "healthcare": "healthcare",
    "education": "education",
    "admin": "business",
}

GEO_MAP = {
    "Pakistan": "anywhere",
    "Canada": "canada",
    "United States": "usa",
    "United Kingdom": "uk",
    "Europe": "europe",
    "Asia Pacific": "apac",
    "Australia": "anywhere",
}


def _date(value: object) -> datetime | None:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _salary(item: dict) -> str:
    low = item.get("annualSalaryMin")
    high = item.get("annualSalaryMax")
    currency = str(item.get("salaryCurrency") or "").strip()
    if not low and not high:
        return ""
    span = f"{low:,}–{high:,}" if isinstance(low, (int, float)) and isinstance(high, (int, float)) else str(low or high)
    return f"{currency} {span}".strip()


class JobicySource(BaseSource):
    name = "jobicy"
    priority = 3

    @property
    def enabled(self) -> bool:
        return self.settings.enable_jobicy

    def _probe(self) -> None:
        data = self.get_json(API, params={"count": 1, "industry": "business"})
        if "jobs" not in data:
            raise ValueError("unexpected Jobicy payload")

    def _industry(self, query: JobQuery) -> str:
        key = query.category
        if not key and query.query:
            from jobpilot.pipelines import taxonomy

            key, _confidence = taxonomy.classify_category(query.query)
            if key == "other":
                key = taxonomy._category_from_synonym(query.query)
        return INDUSTRY_MAP.get(key or "", "")

    def _geo(self, query: JobQuery) -> str:
        text = query.location or " ".join(
            part for part in (query.city, query.region, query.country) if part
        )
        place = geo.find_location(text)
        if place is None or place.kind in ("remote", "worldwide"):
            return ""
        country = place.country or (place.name if place.kind == "macro" else "")
        return GEO_MAP.get(country, "anywhere")

    def fetch(self, query: JobQuery) -> list[RawJob]:
        params: dict[str, object] = {"count": min(100, max(20, self._limit(query, PAGE_SIZE)))}
        industry = self._industry(query)
        if industry:
            params["industry"] = industry
        geo_slug = self._geo(query)
        if geo_slug:
            params["geo"] = geo_slug
        try:
            data = self.get_json(API, params=params)
        except httpx.HTTPStatusError as exc:
            # A rejected industry/geo combination must not take the whole
            # search down; retry once with the broad feed. Transport failures
            # (timeouts, resets) are not retried here: a second 20-second wait
            # would blow the search budget, so they are reported honestly.
            if exc.response.status_code >= 500 or not params.get("industry"):
                raise
            params.pop("industry", None)
            params.pop("geo", None)
            data = self.get_json(API, params=params)

        jobs: list[RawJob] = []
        for item in data.get("jobs", []):
            title = str(item.get("jobTitle") or "").strip()
            if not title:
                continue
            industries = [str(value) for value in item.get("jobIndustry") or []]
            job_types = [str(value) for value in item.get("jobType") or []]
            job_geo = str(item.get("jobGeo") or "Anywhere")
            restrictions = [] if job_geo.lower() in ("anywhere", "global", "worldwide") else [job_geo]
            eligible = ["Worldwide"] if not restrictions else restrictions
            workplace = "hybrid" if "hybrid" in title.lower() else "remote"
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("id") or item.get("jobSlug") or ""),
                    title=title,
                    company=str(item.get("companyName") or "Unknown"),
                    location=job_geo,
                    job_location="Remote",
                    eligible_locations=eligible,
                    remote_scope="worldwide" if not restrictions else "unknown",
                    location_confidence="likely" if eligible else "unknown",
                    description=excerpt(str(item.get("jobDescription") or ""), 30000),
                    url=str(item.get("url") or ""),
                    salary=_salary(item),
                    employment_type=", ".join(job_types),
                    remote=True,
                    workplace_type=workplace,  # type: ignore[arg-type]
                    posted_at=_date(item.get("pubDate")),
                    tags=[*industries, *job_types][:12],
                    department=industries[0] if industries else "",
                    seniority_hint=str(item.get("jobLevel") or ""),
                    raw={
                        "geo": job_geo,
                        "industries": industries,
                        "job_types": job_types,
                        "excerpt": item.get("jobExcerpt"),
                    },
                )
            )
        return jobs
