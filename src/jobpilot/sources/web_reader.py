"""Public web reader — fetch a single job posting URL and recover its text.

Only public pages are fetched, once, with a normal user agent. Structured data
(JSON-LD ``JobPosting``) is preferred when present. No login walls or
anti-bot circumvention is attempted.
"""

from __future__ import annotations

import json
import re

from jobpilot.core.models import RawJob
from jobpilot.pipelines.extract import extract_job, strip_html
from jobpilot.sources.base import BaseSource

JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', re.I | re.S
)
OG_SITE_RE = re.compile(
    r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\'](.*?)["\']', re.I | re.S
)
REMOVE_RE = re.compile(
    r"<(script|style|noscript|nav|footer|header|svg)[^>]*>.*?</\1>", re.I | re.S
)


class WebReader(BaseSource):
    name = "web"
    priority = 3

    def _probe(self) -> None:
        self.get_text("https://example.com")

    def fetch(self, query: JobQuery) -> list[RawJob]:
        if not query.query.startswith("http"):
            return []
        return [self.read_url(query.query)]

    def read_url(self, url: str) -> RawJob:
        html_text = self.get_text(url)
        data = self._json_ld(html_text) or {}
        title = (
            data.get("title")
            or _first(OG_TITLE_RE, html_text)
            or strip_html(_first(TITLE_RE, html_text) or "")
        )
        company = (
            (data.get("hiringOrganization") or {}).get("name")
            if isinstance(data.get("hiringOrganization"), dict)
            else ""
        ) or _first(OG_SITE_RE, html_text) or ""
        description = data.get("description") or _readable(html_text)
        location = ""
        locations = data.get("jobLocation") or []
        if isinstance(locations, dict):
            locations = [locations]
        if locations:
            address = locations[0].get("address", {}) if isinstance(locations[0], dict) else {}
            if isinstance(address, dict):
                location = ", ".join(
                    str(address.get(key) or "")
                    for key in ("addressLocality", "addressRegion", "addressCountry")
                    if address.get(key)
                )
        salary = ""
        salary_data = data.get("baseSalary")
        if isinstance(salary_data, dict):
            value = salary_data.get("value", {})
            if isinstance(value, dict):
                salary = " ".join(
                    str(value.get(k) or "")
                    for k in ("minValue", "maxValue")
                    if value.get(k)
                )

        raw = RawJob(
            source=self.name,
            external_id=url[-200:],
            title=strip_html(str(title)) or "Imported job",
            company=strip_html(str(company)) or "Unknown",
            location=location,
            description=strip_html(str(description)),
            url=url,
            salary=salary,
            posted_at=data.get("datePosted"),
            raw={"jsonld": bool(data)},
        )
        return raw

    @staticmethod
    def _json_ld(html_text: str) -> dict:
        for block in JSONLD_RE.findall(html_text):
            try:
                payload = json.loads(block.strip())
            except json.JSONDecodeError:
                continue
            candidates = payload if isinstance(payload, list) else [payload]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                graph = candidate.get("@graph")
                if isinstance(graph, list):
                    candidates.extend(graph)
                if candidate.get("@type") in ("JobPosting", ["JobPosting"]):
                    return candidate
        return {}


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return strip_html(match.group(1)) if match else ""


def _readable(html_text: str) -> str:
    main = re.search(r"<(main|article)[^>]*>(.*?)</\1>", html_text, re.I | re.S)
    body = main.group(2) if main else html_text
    body = REMOVE_RE.sub(" ", body)
    text = strip_html(body)
    return text[:20000]


def import_url(url: str) -> RawJob:
    return WebReader().read_url(url)


def import_text(text: str, source: str = "manual") -> RawJob:
    """Build a raw job from pasted text (title-first convention)."""
    cleaned = strip_html(text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    title = lines[0] if lines else "Imported job"
    company = ""
    if len(lines) > 1:
        company = re.sub(
            r"^(company|employer)\s*[:\-]\s*", "", lines[1], flags=re.I
        )[:120]
    raw = RawJob(
        source=source,
        external_id=title[:80],
        title=title[:160],
        company=company or "Unknown",
        description=cleaned,
        raw={"imported": True},
    )
    return raw


def import_text_as_job(text: str):
    from jobpilot.pipelines.dedupe import dedupe_jobs

    return dedupe_jobs([extract_job(import_text(text))]).unique
