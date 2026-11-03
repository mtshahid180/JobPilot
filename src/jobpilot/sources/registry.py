"""Source registry: concurrent fan-out, health probing and honest reporting.

Search never stops early because a tier looked "full enough": a banking search
that finds five engineering roles is not a successful banking search.  Every
enabled source is contacted in parallel, its own outcome is recorded, and the
unified search service applies the mandatory filters afterwards.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from jobpilot.core.config import Settings, get_settings
from jobpilot.core.models import (
    JobQuery,
    ProbeResult,
    RawJob,
    SourceStatus,
)
from jobpilot.pipelines.dedupe import dedupe_jobs
from jobpilot.pipelines.extract import extract_job
from jobpilot.sources.adzuna import AdzunaSource
from jobpilot.sources.arbeitnow import ArbeitnowSource
from jobpilot.sources.base import SourceAdapter
from jobpilot.sources.greenhouse import GreenhouseSource
from jobpilot.sources.himalayas import HimalayasSource
from jobpilot.sources.indeed import IndeedSource
from jobpilot.sources.jobicy import JobicySource
from jobpilot.sources.linkedin import LinkedInSource
from jobpilot.sources.manual import ManualSource
from jobpilot.sources.mustakbil import MustakbilSource
from jobpilot.sources.remoteok import RemoteOKSource
from jobpilot.sources.remotive import RemotiveSource
from jobpilot.sources.rss import RSSSource
from jobpilot.sources.themuse import TheMuseSource
from jobpilot.sources.workable import WorkableSource

PROBE_TTL_SECONDS = 300
MAX_COLLECTED = 400


@dataclass
class SourceRun:
    """What one source did during one search."""

    name: str
    status: str = "ok"  # ok | empty | error | disabled | skipped
    count: int = 0
    error: str = ""
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "count": self.count,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class SearchOutcome:
    jobs: list[Any] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    duplicates: int = 0
    tiers_tried: int = 0
    elapsed_ms: float = 0.0
    raw_count: int = 0
    runs: dict[str, SourceRun] = field(default_factory=dict)


class SourceRegistry:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.adapters: list[SourceAdapter] = sorted(
            [
                RemotiveSource(self.settings),
                HimalayasSource(self.settings),
                TheMuseSource(self.settings),
                JobicySource(self.settings),
                MustakbilSource(self.settings),
                ArbeitnowSource(self.settings),
                RemoteOKSource(self.settings),
                GreenhouseSource(self.settings),
                WorkableSource(self.settings),
                AdzunaSource(self.settings),
                RSSSource(self.settings),
                IndeedSource(self.settings),
                LinkedInSource(self.settings),
                ManualSource(self.settings),
            ],
            key=lambda adapter: adapter.priority,
        )
        self._probe_cache: dict[str, tuple[float, ProbeResult]] = {}
        self._lock = threading.Lock()

    # --- health ------------------------------------------------------------
    def probe(self, name: str) -> ProbeResult:
        adapter = self.get(name)
        if adapter is None:
            return ProbeResult(name=name, ok=False, detail="unknown source")
        result = adapter.probe()
        with self._lock:
            self._probe_cache[name] = (time.monotonic(), result)
        return result

    def probe_all(self, force: bool = False) -> dict[str, ProbeResult]:
        adapters = [a for a in self.adapters if a.name != "manual"]
        results: dict[str, ProbeResult] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {}
            for adapter in adapters:
                cached = self._probe_cache.get(adapter.name)
                if not force and cached and time.monotonic() - cached[0] < PROBE_TTL_SECONDS:
                    results[adapter.name] = cached[1]
                    continue
                if not adapter.enabled:
                    result = ProbeResult(
                        name=adapter.name,
                        ok=False,
                        detail="disabled",
                    )
                    results[adapter.name] = result
                    continue
                futures[pool.submit(adapter.probe)] = adapter.name
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    result = ProbeResult(name=name, ok=False, detail=str(exc))
                results[name] = result
        with self._lock:
            now = time.monotonic()
            for name, result in results.items():
                self._probe_cache[name] = (now, result)
        return results

    def doctor(self, force: bool = False) -> list[SourceStatus]:
        probes = self.probe_all(force=force)
        statuses: list[SourceStatus] = []
        for adapter in self.adapters:
            cached = self._probe_cache.get(adapter.name)
            statuses.append(
                SourceStatus(
                    name=adapter.name,
                    priority=adapter.priority,
                    enabled=adapter.enabled,
                    requires_key=adapter.requires_key,
                    last_probe=probes.get(adapter.name) or (cached[1] if cached else None),
                )
            )
        return statuses

    def get(self, name: str) -> SourceAdapter | None:
        for adapter in self.adapters:
            if adapter.name == name:
                return adapter
        return None

    # --- search ------------------------------------------------------------
    def search(self, query: JobQuery) -> SearchOutcome:
        started = time.perf_counter()
        outcome = SearchOutcome()
        wanted = [name for name in (query.sources or []) if name]

        manual = self.get("manual")
        manual_request = bool(
            manual is not None
            and (
                query.query.startswith("http")
                or "\n" in query.query
            )
        )
        allowed: list[SourceAdapter] = []
        for adapter in self.adapters:
            if adapter.name == "manual":
                continue
            if wanted and adapter.name not in wanted:
                continue
            if not adapter.enabled:
                outcome.runs[adapter.name] = SourceRun(
                    name=adapter.name,
                    status="skipped" if wanted else "disabled",
                    error="disabled",
                )
                continue
            allowed.append(adapter)
        if manual is not None and manual.enabled and manual_request:
            allowed.append(manual)

        if not allowed:
            outcome.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            return outcome

        per_source = max(24, min(max(query.limit, 24) * 2, 80))
        source_query = query.model_copy(update={"limit": per_source})
        collected: list[RawJob] = []

        budget = max(0.1, float(self.settings.source_budget_seconds))
        pool = ThreadPoolExecutor(max_workers=min(8, len(allowed)))
        futures = {pool.submit(_timed_fetch, adapter, source_query): adapter for adapter in allowed}
        try:
            for future in as_completed(futures, timeout=budget):
                adapter = futures[future]
                try:
                    run, jobs = future.result()
                except Exception as exc:  # pragma: no cover - _timed_fetch catches
                    run, jobs = (
                        SourceRun(name=adapter.name, status="error", error=str(exc)[:300]),
                        [],
                    )
                outcome.runs[adapter.name] = run
                if run.error:
                    outcome.errors[adapter.name] = run.error
                if jobs:
                    collected.extend(jobs)
                    outcome.sources_used.append(adapter.name)
        except TimeoutError:
            # A single slow source must not hold the whole request.  Report it
            # as a timeout instead of pretending the search was complete.
            for adapter in allowed:
                if adapter.name in outcome.runs:
                    continue
                message = f"no response within {budget:.0f}s"
                outcome.runs[adapter.name] = SourceRun(
                    name=adapter.name, status="timeout", error=message
                )
                outcome.errors[adapter.name] = message
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        extracted = []
        for raw in collected:
            try:
                extracted.append(extract_job(raw))
            except Exception as exc:  # pragma: no cover - malformed source row
                source = getattr(raw, "source", "unknown")
                outcome.errors[f"{source}:extract"] = str(exc)[:200]

        outcome.raw_count = len(extracted)
        unique = dedupe_jobs(extracted)
        outcome.duplicates = unique.duplicates
        outcome.jobs = unique.unique[:MAX_COLLECTED]
        outcome.sources_used = sorted(set(outcome.sources_used))
        outcome.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return outcome

    def stats(self) -> list[dict[str, Any]]:
        return [status.model_dump(mode="json") for status in self.doctor()]


def _timed_fetch(adapter: SourceAdapter, query: JobQuery) -> tuple[SourceRun, list[RawJob]]:
    started = time.perf_counter()
    run = SourceRun(name=adapter.name)
    try:
        jobs = adapter.fetch(query)
    except Exception as exc:
        run.status = "error"
        run.error = str(exc)[:300] or exc.__class__.__name__
        run.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return run, []
    run.count = len(jobs)
    run.status = "ok" if jobs else "empty"
    run.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return run, jobs


_registry: SourceRegistry | None = None


def get_registry() -> SourceRegistry:
    global _registry
    if _registry is None:
        _registry = SourceRegistry()
    return _registry
