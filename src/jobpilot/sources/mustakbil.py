"""Mustakbil — Pakistan's largest public job board (server-rendered HTML).

Robots policy allows public ``/jobs`` listing pages.  We read the public
``<article>`` cards only; individual postings are linked out to, never scraped
for contact details.  The site rejects ``?page=`` query strings (HTTP 410), so
extra coverage comes from lawful path variants (city + category) instead.
"""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from jobpilot.core import locations as geo
from jobpilot.core.models import JobQuery, RawJob
from jobpilot.pipelines.extract import strip_html
from jobpilot.pipelines import taxonomy
from jobpilot.sources.base import BaseSource, excerpt

BASE = "https://www.mustakbil.com"
NATIONWIDE = "/jobs/pakistan"

# Mustakbil publishes no finance/banking-specific category, so those searches
# use the nearest lawful category and are filtered by title/description later.
CATEGORY_PATHS: dict[str, tuple[str, ...]] = {
    "finance": ("accounting",),
    "accounting": ("accounting",),
    "banking": ("accounting", "sales"),
    "marketing": ("advertising-marketing-public-relations",),
    "sales": ("sales",),
    "hr": ("human-resources-recruiting",),
    "customer_support": ("customer-service-and-call-centre",),
    "operations": ("supply-chain", "procurement", "production"),
    "management": ("administrative",),
    "healthcare": ("healthcare", "healthcare-administration-health-it"),
    "education": ("education",),
    "technology": ("information-technology", "computer-software"),
    "data_ai": ("information-technology", "computer-software"),
    "devops_cloud": ("information-technology",),
    "engineering": ("engineering", "construction"),
    "design": ("media", "advertising-marketing-public-relations"),
    "product": ("information-technology",),
    "admin": ("administrative",),
    "legal": ("administrative",),
}

PAKISTAN_CITIES = {
    "karachi": "karachi",
    "lahore": "lahore",
    "islamabad": "islamabad",
    "rawalpindi": "rawalpindi",
    "faisalabad": "faisalabad",
    "multan": "multan",
    "peshawar": "peshawar",
    "quetta": "quetta",
    "hyderabad": "hyderabad",
    "gujranwala": "gujranwala",
    "sialkot": "sialkot",
}

CITY_SWEEP = ("karachi", "lahore", "islamabad", "rawalpindi")

ARTICLE_RE = re.compile(r"<article[\s\S]*?</article>", re.I)
TITLE_RE = re.compile(r'class="jc-title__link"[^>]*>(.*?)</a>', re.S)
COMPANY_RE = re.compile(r'class="jc-byline__name">(.*?)</span>', re.S)
PLACE_RE = re.compile(r'class="jc-byline__place">.*?<span[^>]*>([\s\S]*?)</span>', re.S)
PAY_RE = re.compile(r'class="jc-pay__fig">(.*?)</span>', re.S)
CURRENCY_RE = re.compile(r'class="jc-pay__cur">(.*?)</span>', re.S)
DESC_RE = re.compile(r'class="jc-desc">(.*?)</p>', re.S)
FACT_RE = re.compile(r'class="jc-fact(?: jc-fact--time)?">(.*?)</span>', re.S)
LINK_RE = re.compile(r'href="(/jobs/job/\d+)"')
ID_RE = re.compile(r'<article[^>]*\bid="(\d+)"', re.I)

_RELATIVE_RE = re.compile(
    r"(\d+)\s*(minute|hour|day|week|month|year)s?\s+ago|just now|today|yesterday",
    re.I,
)


def _clean(value: str) -> str:
    return " ".join(strip_html(html.unescape(value or "")).split())


def _relative_date(value: str) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    now = datetime.now(UTC)
    if re.search(r"\bjust now\b|\btoday\b", text, re.I):
        return now
    if re.search(r"\byesterday\b", text, re.I):
        return now - timedelta(days=1)
    match = _RELATIVE_RE.search(text)
    if not match:
        return None
    count = int(match.group(1))
    unit = (match.group(2) or "").lower()
    delta = {
        "minute": timedelta(minutes=count),
        "hour": timedelta(hours=count),
        "day": timedelta(days=count),
        "week": timedelta(weeks=count),
        "month": timedelta(days=count * 30),
        "year": timedelta(days=count * 365),
    }.get(unit)
    return now - delta if delta else None


def _salary(article: str) -> str:
    fig = _clean(PAY_RE.search(article).group(1)) if PAY_RE.search(article) else ""
    if not fig:
        return ""
    currency = _clean(CURRENCY_RE.search(article).group(1)) if CURRENCY_RE.search(article) else ""
    fig = fig.replace("\ufffd", "–").replace("–", "-").replace("—", "-")
    return f"{currency} {fig}".strip()


def _facts(article: str) -> list[str]:
    return [_clean(value) for value in FACT_RE.findall(article) if _clean(value)]


class MustakbilSource(BaseSource):
    name = "mustakbil"
    priority = 2

    @property
    def enabled(self) -> bool:
        return self.settings.enable_mustakbil

    def _probe(self) -> None:
        text = self.get_text(f"{BASE}/jobs/pakistan/accounting")
        if "jc-title__link" not in text and "<article" not in text:
            raise ValueError("unexpected Mustakbil payload")

    # --- query routing -----------------------------------------------------
    def _location(self, query: JobQuery) -> tuple[str, str, str]:
        text = query.location or " ".join(
            part for part in (query.city, query.region, query.country) if part
        )
        place = geo.find_location(text)
        if place is None:
            return "", "Pakistan", ""
        if place.kind == "remote":
            return "", "Pakistan", ""
        if place.country and place.country != "Pakistan":
            return "", place.country, ""
        if place.kind == "city":
            return PAKISTAN_CITIES.get(place.city.lower(), ""), "Pakistan", place.city
        return "", "Pakistan", ""

    def _category(self, query: JobQuery) -> str:
        if query.category:
            return query.category
        key, _confidence = taxonomy.classify_category(query.query or "")
        if key != "other":
            return key
        return taxonomy._category_from_synonym(query.query or "")

    def _paths(self, query: JobQuery) -> list[str]:
        city_slug, country, _city = self._location(query)
        if country and country != "Pakistan":
            return []
        category = self._category(query)
        categories = CATEGORY_PATHS.get(category, ())
        paths: list[str] = []
        if city_slug:
            if categories:
                paths.extend(f"{NATIONWIDE}/{city_slug}/{cat}" for cat in categories)
            else:
                paths.append(f"{NATIONWIDE}/{city_slug}")
        else:
            if categories:
                paths.extend(f"{NATIONWIDE}/{cat}" for cat in categories)
                # One page per category is thin; lawful city paths widen the net.
                for city in CITY_SWEEP:
                    paths.extend(f"{NATIONWIDE}/{city}/{cat}" for cat in categories[:1])
            else:
                paths.append(NATIONWIDE)
                paths.extend(f"{NATIONWIDE}/{city}" for city in CITY_SWEEP[:2])
        seen: set[str] = set()
        unique = []
        for path in paths:
            if path not in seen:
                seen.add(path)
                unique.append(path)
        return unique[:7]

    # --- parsing -----------------------------------------------------------
    def _parse(self, article: str, page_url: str) -> RawJob | None:
        title_match = TITLE_RE.search(article)
        link_match = LINK_RE.search(article)
        if not title_match or not link_match:
            return None
        title = _clean(title_match.group(1))
        if not title:
            return None
        place = _clean(PLACE_RE.search(article).group(1)) if PLACE_RE.search(article) else ""
        description = _clean(DESC_RE.search(article).group(1)) if DESC_RE.search(article) else ""
        facts = _facts(article)
        relative = next((fact for fact in facts if _RELATIVE_RE.search(fact)), "")
        employment = next(
            (
                fact
                for fact in facts
                if re.search(r"\b(full|part)[- ]time|contract|internship|temporary\b", fact, re.I)
            ),
            "",
        )
        path = link_match.group(1)
        article_id = ID_RE.search(article)
        external_id = article_id.group(1) if article_id else path.rsplit("/", 1)[-1]
        workplace = ""
        lowered = description.lower()
        if re.search(r"\bremote\b|work from home", lowered):
            workplace = "remote"
        elif re.search(r"\bhybrid\b", lowered):
            workplace = "hybrid"
        elif re.search(r"\bon[- ]site\b|in[- ]office\b", lowered):
            workplace = "onsite"
        tags = [value for value in facts if value and not _RELATIVE_RE.search(value)][:6]
        return RawJob(
            source=self.name,
            external_id=external_id,
            title=title,
            company=(
                _clean(COMPANY_RE.search(article).group(1))
                if COMPANY_RE.search(article)
                else "Unknown"
            ),
            company_location=place,
            location=place,
            job_location=place,
            description=excerpt(description, 20000),
            url=f"{BASE}{path}",
            salary=_salary(article),
            employment_type=employment,
            remote=(workplace in ("remote", "hybrid")) if workplace else None,
            workplace_type=workplace or "unknown",  # type: ignore[arg-type]
            posted_at=_relative_date(relative),
            tags=tags,
            raw={
                "page": page_url,
                "facts": facts,
                "relative_date": relative,
                "listing_id": external_id,
            },
        )

    def _fetch_path(self, path: str) -> list[RawJob]:
        url = f"{BASE}{path}"
        body = self.get_text(url)
        jobs: list[RawJob] = []
        for article in ARTICLE_RE.findall(body):
            parsed = self._parse(article, url)
            if parsed is not None:
                jobs.append(parsed)
        return jobs

    def fetch(self, query: JobQuery) -> list[RawJob]:
        paths = self._paths(query)
        if not paths:
            return []
        collected: list[RawJob] = []
        with ThreadPoolExecutor(max_workers=min(4, len(paths))) as pool:
            futures = [pool.submit(self._fetch_path, path) for path in paths]
            for future in futures:
                try:
                    collected.extend(future.result())
                except Exception:
                    # A single retired path must not discard the other results.
                    continue
        seen: set[str] = set()
        unique: list[RawJob] = []
        for job in collected:
            if job.external_id in seen:
                continue
            seen.add(job.external_id)
            unique.append(job)
        return unique[: max(self._limit(query, 40) * 3, 40)]
