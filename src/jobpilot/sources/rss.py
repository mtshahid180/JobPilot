"""RSS job feeds — company career pages and niche job boards."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import feedparser

from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt


class RSSSource(BaseSource):
    name = "rss"
    priority = 2

    @property
    def enabled(self) -> bool:
        return self.settings.enable_rss_sources

    def _probe(self) -> None:
        feeds = self.settings.rss_feed_list
        if not feeds:
            raise RuntimeError("no RSS_FEEDS configured")
        parsed = feedparser.parse(feeds[0])
        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise RuntimeError(str(parsed.get("bozo_exception", "feed unreachable")))
        if not parsed.entries:
            raise RuntimeError("feed returned no entries")

    def fetch(self, query: JobQuery) -> list[RawJob]:
        jobs: list[RawJob] = []
        needle = (query.query or "").lower()
        for feed_url in self.settings.rss_feed_list:
            try:
                parsed = feedparser.parse(feed_url)
            except Exception:
                continue
            for entry in parsed.entries:
                title = entry.get("title", "")
                summary = excerpt(entry.get("summary", "") or entry.get("description", ""), 12000)
                if needle and needle not in f"{title} {summary}".lower():
                    continue
                company = _company_from_title(title) or parsed.feed.get("title", "Unknown")
                jobs.append(
                    RawJob(
                        source=self.name,
                        external_id=entry.get("id", entry.get("link", title))[:200],
                        title=title,
                        company=company,
                        location=entry.get("location", "") or "See posting",
                        description=summary,
                        url=entry.get("link", ""),
                        posted_at=_entry_date(entry),
                        tags=[tag.get("term", "") for tag in entry.get("tags", []) if tag.get("term")],
                        raw={"feed": feed_url},
                    )
                )
                if len(jobs) >= self._limit(query, 20):
                    return jobs
        return jobs


def _company_from_title(title: str) -> str:
    match = re.search(r"\bat\s+([A-Z][\w&.\- ]{1,60})", title or "")
    return match.group(1).strip() if match else ""


def _entry_date(entry: dict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None
