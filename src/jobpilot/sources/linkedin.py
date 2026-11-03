"""LinkedIn adapter — intentionally disabled.

LinkedIn has no public jobs API. Automated scraping violates its Terms of
Service and can get an account restricted, so this adapter never fetches job
data. The supported alternative is to paste a LinkedIn job URL into Import a
job, or to track the application manually.

This module exists so the policy is explicit and auditable, and so status
surfaces report the truth instead of implying LinkedIn search works.
"""

from __future__ import annotations

from jobpilot.core.models import JobQuery, ProbeResult, RawJob
from jobpilot.sources.base import BaseSource

HONEST_DETAIL = (
    "disabled: LinkedIn has no public jobs API and scraping violates its Terms; "
    "paste a LinkedIn job URL into Import a job instead"
)


class LinkedInSource(BaseSource):
    name = "linkedin"
    priority = 4

    @property
    def enabled(self) -> bool:
        return self.settings.enable_linkedin

    def _probe(self) -> None:
        if not self.enabled:
            raise RuntimeError(HONEST_DETAIL)
        raise RuntimeError(
            "LinkedIn search is not implemented: no supported jobs API and "
            "scraping is disallowed. Use manual import."
        )

    def probe(self) -> ProbeResult:
        return ProbeResult(
            name=self.name,
            ok=False,
            latency_ms=0.0,
            detail=HONEST_DETAIL,
        )

    def fetch(self, query: JobQuery) -> list[RawJob]:
        return []
