"""Adzuna — official API, free developer key required."""

from __future__ import annotations

from typing import Any

from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


class AdzunaSource(BaseSource):
    name = "adzuna"
    priority = 1
    requires_key = True

    @property
    def enabled(self) -> bool:
        return bool(self.settings.adzuna_app_id and self.settings.adzuna_app_key)

    def _probe(self) -> None:
        if not self.enabled:
            raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
        self.get_json(
            API.format(country=self.settings.adzuna_country),
            params={
                "app_id": self.settings.adzuna_app_id,
                "app_key": self.settings.adzuna_app_key,
                "results_per_page": 1,
                "what": "engineer",
            },
        )

    def fetch(self, query: JobQuery) -> list[RawJob]:
        if not self.enabled:
            return []
        params: dict[str, Any] = {
            "app_id": self.settings.adzuna_app_id,
            "app_key": self.settings.adzuna_app_key,
            "results_per_page": self._limit(query, 30),
            "content-type": "application/json",
        }
        if query.query:
            params["what"] = query.query
        if query.location:
            params["where"] = query.location
        data = self.get_json(API.format(country=self.settings.adzuna_country), params=params)
        jobs: list[RawJob] = []
        for item in data.get("results", []):
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("id", "")),
                    title=item.get("title", ""),
                    company=(item.get("company") or {}).get("display_name", "Unknown"),
                    location=(item.get("location") or {}).get("display_name", ""),
                    description=excerpt(item.get("description", ""), 20000),
                    url=item.get("redirect_url", ""),
                    salary=_salary(item),
                    employment_type=item.get("contract_time", "") or "",
                    posted_at=item.get("created"),
                    tags=list(item.get("category", {}).values())[:4]
                    if isinstance(item.get("category"), dict)
                    else [],
                    raw={},
                )
            )
        return jobs


def _salary(item: dict[str, Any]) -> str:
    low, high = item.get("salary_min"), item.get("salary_max")
    if low and high:
        return f"{low:,.0f} - {high:,.0f} {item.get('salary_currency', '')}".strip()
    if low:
        return f"from {low:,.0f} {item.get('salary_currency', '')}".strip()
    return ""
