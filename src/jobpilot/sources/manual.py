"""Manual source — paste a job URL or description."""

from __future__ import annotations

from jobpilot.core.models import JobQuery, RawJob, ProbeResult
from jobpilot.pipelines.extract import extract_job
from jobpilot.sources.base import BaseSource
from jobpilot.sources.web_reader import import_text, import_url


class ManualSource(BaseSource):
    name = "manual"
    priority = 5

    def _probe(self) -> None:
        return None

    def probe(self) -> ProbeResult:
        return ProbeResult(name=self.name, ok=True, latency_ms=0.0, detail="always available")

    def fetch(self, query: JobQuery) -> list[RawJob]:
        if query.query.startswith("http"):
            try:
                return [import_url(query.query)]
            except Exception:
                return []
        if len(query.query) > 200 and "\n" in query.query:
            return [import_text(query.query)]
        return []

    @staticmethod
    def from_url(url: str):
        return extract_job(import_url(url))

    @staticmethod
    def from_text(text: str):
        return extract_job(import_text(text))
