"""The Muse — free public jobs API (20 results per page)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from jobpilot.core import locations as geo
from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt

API = "https://www.themuse.com/api/public/jobs"
PAGE_SIZE = 20

CATEGORY_MAP: dict[str, str] = {
    "finance": "Accounting and Finance",
    "accounting": "Accounting and Finance",
    "banking": "Accounting and Finance",
    "marketing": "Advertising and Marketing",
    "sales": "Sales",
    "hr": "Human Resources and Recruitment",
    "customer_support": "Customer Service",
    "operations": "Business Operations",
    "management": "Management",
    "healthcare": "Healthcare",
    "education": "Education",
    "engineering": "Science and Engineering",
    "legal": "Legal Services",
    "design": "Advertising and Marketing",
    "product": "Product",
    "admin": "Business Operations",
    "technology": "Software Engineering",
    "data_ai": "Data and Analytics",
    "devops_cloud": "Computer and IT",
}


def _date(value: object) -> datetime | None:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


class TheMuseSource(BaseSource):
    name = "themuse"
    priority = 2

    @property
    def enabled(self) -> bool:
        return self.settings.enable_themuse

    def _probe(self) -> None:
        data = self.get_json(API, params={"page": 1, "category": "Accounting and Finance"})
        if "results" not in data:
            raise ValueError("unexpected The Muse payload")

    def _category(self, query: JobQuery) -> str:
        key = query.category
        if not key and query.query:
            from jobpilot.pipelines import taxonomy

            key, _confidence = taxonomy.classify_category(query.query)
            if key == "other":
                key = taxonomy._category_from_synonym(query.query)
        return CATEGORY_MAP.get(key or "", "")

    def _fetch_page(self, category: str, page: int) -> list[RawJob]:
        params: dict[str, object] = {"page": page}
        if category:
            params["category"] = category
        data = self.get_json(API, params=params)
        jobs: list[RawJob] = []
        for item in data.get("results", []):
            title = str(item.get("name") or "").strip()
            if not title:
                continue
            locations = [
                str(entry.get("name") or "").strip()
                for entry in item.get("locations") or []
                if isinstance(entry, dict) and str(entry.get("name") or "").strip()
            ]
            remote = any("remote" in value.lower() or "flexible" in value.lower() for value in locations)
            hybrid = any("hybrid" in value.lower() for value in locations)
            onsite_locations = [
                value
                for value in locations
                if "remote" not in value.lower() and "flexible" not in value.lower()
            ]
            if remote:
                job_location = "Remote"
                workplace = "hybrid" if hybrid else "remote"
            elif onsite_locations:
                job_location = " · ".join(onsite_locations[:3])
                workplace = "onsite"
            else:
                job_location = ""
                workplace = "unknown"
            categories = [
                str(entry.get("name") or "")
                for entry in item.get("categories") or []
                if isinstance(entry, dict)
            ]
            levels = [
                str(entry.get("name") or "")
                for entry in item.get("levels") or []
                if isinstance(entry, dict)
            ]
            company = item.get("company") if isinstance(item.get("company"), dict) else {}
            refs = item.get("refs") if isinstance(item.get("refs"), dict) else {}
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("id") or item.get("short_name") or ""),
                    title=title,
                    company=str(company.get("name") or "Unknown"),
                    location=job_location,
                    job_location=job_location,
                    description=excerpt(str(item.get("contents") or ""), 30000),
                    url=str(refs.get("landing_page") or ""),
                    employment_type=str(item.get("type") or ""),
                    remote=remote or None,
                    workplace_type=workplace,  # type: ignore[arg-type]
                    posted_at=_date(item.get("publication_date")),
                    tags=categories[:12],
                    department=categories[0] if categories else "",
                    seniority_hint=levels[0] if levels else "",
                    raw={
                        "locations": locations,
                        "categories": categories,
                        "levels": levels,
                        "model_type": item.get("model_type"),
                    },
                )
            )
        return jobs

    def fetch(self, query: JobQuery) -> list[RawJob]:
        category = self._category(query)
        pages = (1, 2) if self._limit(query, 40) > PAGE_SIZE else (1,)
        collected: list[RawJob] = []
        with ThreadPoolExecutor(max_workers=len(pages)) as pool:
            futures = [pool.submit(self._fetch_page, category, page) for page in pages]
            for future in futures:
                try:
                    collected.extend(future.result())
                except Exception:
                    continue
        seen: set[str] = set()
        unique: list[RawJob] = []
        for job in collected:
            if job.external_id in seen:
                continue
            seen.add(job.external_id)
            unique.append(job)
        return unique
