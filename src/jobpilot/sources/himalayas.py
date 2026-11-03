"""Himalayas — free remote-job API with structured eligibility data."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from jobpilot.core import locations as geo
from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt

API = "https://himalayas.app/jobs/api/search"
PAGE_SIZE = 20


def _money(value: object) -> str:
    if not isinstance(value, (int, float)) or not value:
        return ""
    return f"{value:,.0f}"


def _date(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


class HimalayasSource(BaseSource):
    name = "himalayas"
    priority = 2

    @property
    def enabled(self) -> bool:
        return self.settings.enable_himalayas

    def _probe(self) -> None:
        data = self.get_json(API, params={"q": "finance", "limit": 1})
        if "jobs" not in data:
            raise ValueError("unexpected Himalayas payload")

    def _term(self, query: JobQuery) -> str:
        if query.query:
            return query.query
        for term in query.search_terms:
            if term:
                return term
        return ""

    def _country(self, query: JobQuery) -> str:
        text = query.location or " ".join(
            part for part in (query.city, query.region, query.country) if part
        )
        if query.country and not text:
            return query.country
        place = geo.find_location(text)
        if place is None or place.kind in ("remote", "worldwide"):
            return ""
        return place.country

    def _fetch_page(self, term: str, country: str, page: int) -> list[RawJob]:
        params: dict[str, object] = {"q": term, "limit": PAGE_SIZE, "page": page}
        if country:
            params["country"] = country
        data = self.get_json(API, params=params)
        jobs: list[RawJob] = []
        for item in data.get("jobs", []):
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            restrictions = [
                str(value).strip()
                for value in item.get("locationRestrictions") or []
                if str(value).strip()
            ]
            eligible = restrictions or (["Worldwide"] if country else [])
            min_salary = _money(item.get("minSalary"))
            max_salary = _money(item.get("maxSalary"))
            currency = str(item.get("currency") or "").strip()
            period = str(item.get("salaryPeriod") or "").strip()
            salary = ""
            if min_salary or max_salary:
                span = f"{min_salary}–{max_salary}" if min_salary and max_salary else (min_salary or max_salary)
                salary = f"{currency} {span}".strip()
                if period:
                    salary = f"{salary}/{period}"
            categories = [str(value) for value in item.get("categories") or []]
            parents = [str(value) for value in item.get("parentCategories") or []]
            seniority = [str(value) for value in item.get("seniority") or []]
            guid = str(item.get("guid") or item.get("applicationLink") or "")
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=guid or title,
                    title=title,
                    company=str(item.get("companyName") or "Unknown"),
                    location="Remote",
                    job_location="Remote",
                    eligible_locations=eligible,
                    remote_scope="worldwide" if eligible == ["Worldwide"] else "unknown",
                    location_confidence="likely" if eligible else "unknown",
                    description=excerpt(str(item.get("description") or ""), 30000),
                    url=guid,
                    salary=salary,
                    employment_type=str(item.get("employmentType") or ""),
                    remote=True,
                    workplace_type="remote",
                    posted_at=_date(item.get("pubDate")),
                    expires_at=_date(item.get("expiryDate")),
                    tags=[*parents, *categories][:20],
                    department=parents[0] if parents else "",
                    seniority_hint=seniority[0] if seniority else "",
                    raw={
                        "country_filter": country,
                        "location_restrictions": restrictions,
                        "timezone_restrictions": item.get("timezoneRestrictions"),
                        "categories": categories,
                        "parent_categories": parents,
                        "application_link": item.get("applicationLink"),
                    },
                )
            )
        return jobs

    def fetch(self, query: JobQuery) -> list[RawJob]:
        term = self._term(query)
        if not term:
            return []
        country = self._country(query)
        pages = (1, 2) if self._limit(query, 40) > PAGE_SIZE else (1,)
        collected: list[RawJob] = []
        with ThreadPoolExecutor(max_workers=len(pages)) as pool:
            futures = [pool.submit(self._fetch_page, term, country, page) for page in pages]
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
