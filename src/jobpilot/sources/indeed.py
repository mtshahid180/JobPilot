"""Indeed adapter — intentionally disabled by policy.

Indeed retired its public Job Search (Publisher) API; the endpoint was shut
down in 2024 and no free replacement exists. Scraping indeed.com is prohibited
by its Terms of Service, so JobPilot does not do it.

This module stays as an explicit, auditable policy stub. It can never fetch
jobs, and it tells the operator why in the source doctor output.
"""

from __future__ import annotations

from jobpilot.core.models import JobQuery, ProbeResult, RawJob
from jobpilot.sources.base import BaseSource


class IndeedSource(BaseSource):
    name = "indeed"
    priority = 3
    requires_key = True

    @property
    def enabled(self) -> bool:
        # Always false: there is no compliant public API to call.
        return False

    def _probe(self) -> None:
        raise RuntimeError(
            "Indeed has no public job-search API; scraping violates its Terms of Service"
        )

    def probe(self) -> ProbeResult:
        return ProbeResult(
            name=self.name,
            ok=False,
            latency_ms=0.0,
            detail=(
                "disabled by policy — Indeed retired its public Job Search API "
                "and scraping violates its ToS"
            ),
        )

    def fetch(self, query: JobQuery) -> list[RawJob]:
        return []
