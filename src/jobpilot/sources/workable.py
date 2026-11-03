"""Workable — public account widgets (no key required).

Workable publishes each customer's open roles as a JSON widget. The old
``www.workable.com/api/accounts/<subdomain>`` URL redirects to the canonical
``apply.workable.com/api/v1/widget/accounts/<subdomain>`` endpoint, which is
what this adapter calls directly.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt

API = "https://apply.workable.com/api/v1/widget/accounts/{account}"


class WorkableSource(BaseSource):
    name = "workable"
    priority = 1

    @property
    def enabled(self) -> bool:
        return self.settings.enable_workable and bool(self.settings.workable_account_list)

    def _probe(self) -> None:
        accounts = self.settings.workable_account_list
        if not accounts:
            raise RuntimeError("no WORKABLE_ACCOUNTS configured")
        data = self.get_json(API.format(account=accounts[0]), params={"details": "false"})
        if "jobs" not in data:
            raise ValueError("unexpected Workable payload")

    def _fetch_account(self, account: str, query: JobQuery) -> list[RawJob]:
        data = self.get_json(API.format(account=account), params={"details": "true"})
        items = data.get("jobs", [])
        terms = _terms(query)
        company = data.get("name") or account.replace("-", " ").title()
        jobs: list[RawJob] = []
        for item in items:
            title = item.get("title", "")
            description = item.get("description", "") or ""
            location = _location(item)
            haystack = f"{title} {location} {description}".lower()
            if terms and not any(term in haystack for term in terms):
                continue
            remote = bool(item.get("telecommuting"))
            workplace = "remote" if remote else ("onsite" if location else "unknown")
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("shortcode") or item.get("code") or ""),
                    title=title,
                    company=company,
                    location=location,
                    job_location="Remote" if remote else location,
                    description=excerpt(description, 20000),
                    url=item.get("url") or item.get("shortlink", ""),
                    employment_type=item.get("employment_type", ""),
                    remote=remote or None,
                    workplace_type=workplace,  # type: ignore[arg-type]
                    posted_at=item.get("published_on") or item.get("created_at"),
                    tags=[item.get("department", "")] if item.get("department") else [],
                    department=str(item.get("department") or ""),
                    raw={
                        "account": account,
                        "shortcode": item.get("shortcode"),
                        "department": item.get("department"),
                    },
                )
            )
            if len(jobs) >= max(10, query.limit):
                break
        return jobs

    def fetch(self, query: JobQuery) -> list[RawJob]:
        accounts = self.settings.workable_account_list
        if not accounts:
            return []
        jobs: list[RawJob] = []
        with ThreadPoolExecutor(max_workers=min(4, len(accounts))) as pool:
            futures = [
                pool.submit(self._fetch_account, account, query) for account in accounts
            ]
            for future in futures:
                try:
                    jobs.extend(future.result())
                except Exception:
                    # A closed or renamed account must not break the search.
                    continue
        return jobs


def _location(item: dict) -> str:
    parts: list[str] = []
    for entry in item.get("locations") or []:
        if not isinstance(entry, dict):
            continue
        pieces = [
            str(entry.get(key) or "").strip()
            for key in ("city", "region", "country")
        ]
        value = ", ".join(piece for piece in pieces if piece)
        if value and value not in parts:
            parts.append(value)
    if not parts:
        pieces = [
            str(item.get(key) or "").strip()
            for key in ("city", "state", "country")
        ]
        value = ", ".join(piece for piece in pieces if piece)
        if value:
            parts.append(value)
    return " · ".join(parts)


def _terms(query: JobQuery) -> list[str]:
    values = [query.query, *query.search_terms]
    result: list[str] = []
    for value in values:
        for token in str(value or "").lower().split():
            if len(token) > 2 and token not in result:
                result.append(token)
    return result[:8]
