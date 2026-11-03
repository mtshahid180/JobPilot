"""Greenhouse — public job-board API (no key required).

Every company that hosts its careers page on Greenhouse exposes a public board
at ``boards-api.greenhouse.io``. Boards are configured with company tokens such
as ``stripe`` or ``figma`` (the token is the path segment in
``boards.greenhouse.io/<token>``).

The list endpoint is called with ``content=false`` first; full descriptions are
only downloaded for the small set of postings that pass the cheap text filter.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from jobpilot.core import locations as geo
from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
DETAIL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"


class GreenhouseSource(BaseSource):
    name = "greenhouse"
    priority = 2

    @property
    def enabled(self) -> bool:
        return self.settings.enable_greenhouse and bool(self.settings.greenhouse_board_list)

    def _probe(self) -> None:
        boards = self.settings.greenhouse_board_list
        if not boards:
            raise RuntimeError("no GREENHOUSE_BOARDS configured")
        data = self.get_json(API.format(board=boards[0]), params={"content": "false"})
        if "jobs" not in data:
            raise ValueError("unexpected Greenhouse payload")

    def _fetch_board(self, board: str, query: JobQuery) -> list[RawJob]:
        data = self.get_json(API.format(board=board), params={"content": "false"})
        items = data.get("jobs", [])
        terms = _terms(query)
        candidates = []
        for item in items:
            title = str(item.get("title") or "")
            location = str((item.get("location") or {}).get("name") or "")
            departments = " ".join(
                str(department.get("name") or "")
                for department in item.get("departments", []) or []
                if isinstance(department, dict)
            )
            haystack = f"{title} {location} {departments}".lower()
            if terms and not any(term in haystack for term in terms):
                continue
            candidates.append(item)
            if len(candidates) >= max(8, min(query.limit, 24)):
                break

        company = board.replace("-", " ").replace("_", " ").title()
        if not candidates:
            return []

        def detail(item: dict) -> RawJob | None:
            job_id = item.get("id")
            content = ""
            if job_id is not None:
                try:
                    payload = self.get_json(
                        DETAIL.format(board=board, job_id=job_id), params={"questions": "false"}
                    )
                    content = str(payload.get("content") or item.get("content") or "")
                except Exception:
                    content = str(item.get("content") or "")
            title = str(item.get("title") or "")
            location = str((item.get("location") or {}).get("name") or "")
            departments = [
                str(department.get("name") or "")
                for department in item.get("departments", []) or []
                if isinstance(department, dict)
            ]
            workplace = "unknown"
            if re.search(r"\b(hybrid)\b", location, re.I):
                workplace = "hybrid"
            elif re.search(r"\b(remote)\b", location, re.I):
                workplace = "remote"
            elif location:
                workplace = "onsite"
            remote_info = (
                geo.extract_remote_info(location, content[:3000], remote_flag=True)
                if workplace in ("remote", "hybrid")
                else None
            )
            return RawJob(
                source=self.name,
                external_id=str(job_id or ""),
                title=title,
                company=str(item.get("company_name") or company),
                location=location,
                job_location="Remote" if workplace in ("remote", "hybrid") else location,
                eligible_locations=remote_info.eligible if remote_info else [],
                remote_scope=remote_info.scope if remote_info else "unknown",
                location_confidence=remote_info.confidence if remote_info else ("verified" if location else "unknown"),
                description=excerpt(content, 30000),
                url=str(item.get("absolute_url") or ""),
                employment_type="",
                remote=(workplace in ("remote", "hybrid")) if workplace != "unknown" else None,
                workplace_type=workplace,  # type: ignore[arg-type]
                posted_at=item.get("first_published") or item.get("updated_at"),
                tags=departments[:8],
                department=departments[0] if departments else "",
                raw={
                    "board": board,
                    "id": job_id,
                    "updated_at": item.get("updated_at"),
                },
            )

        jobs: list[RawJob] = []
        with ThreadPoolExecutor(max_workers=min(6, len(candidates))) as pool:
            for result in pool.map(detail, candidates):
                if result is not None:
                    jobs.append(result)
        return jobs

    def fetch(self, query: JobQuery) -> list[RawJob]:
        boards = self.settings.greenhouse_board_list
        if not boards:
            return []
        jobs: list[RawJob] = []
        with ThreadPoolExecutor(max_workers=min(4, len(boards))) as pool:
            futures = [pool.submit(self._fetch_board, board, query) for board in boards]
            for future in futures:
                try:
                    jobs.extend(future.result())
                except Exception:
                    # A single closed/renamed board must not break the search.
                    continue
        return jobs


def _terms(query: JobQuery) -> list[str]:
    values = [query.query, *query.search_terms]
    result: list[str] = []
    for value in values:
        for token in str(value or "").lower().split():
            if len(token) > 2 and token not in result:
                result.append(token)
    return result[:8]
