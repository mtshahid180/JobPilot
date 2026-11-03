"""Source adapter protocol and shared HTTP helpers.

Every source implements ``probe()`` (is this backend alive?) and ``fetch()``.
The registry uses the probe results to route around failing backends.
"""

from __future__ import annotations

import time
import threading
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import httpx

from jobpilot.core.config import Settings, get_settings
from jobpilot.core.models import JobQuery, ProbeResult, RawJob


@runtime_checkable
class SourceAdapter(Protocol):
    name: str
    priority: int
    requires_key: bool
    enabled: bool

    def probe(self) -> ProbeResult: ...

    def fetch(self, query: JobQuery) -> list[RawJob]: ...


class BaseSource:
    name = "base"
    priority = 5
    requires_key = False
    enabled = True

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._http: httpx.Client | None = None
        self._http_lock = threading.Lock()

    # --- HTTP --------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.settings.user_agent,
            "Accept": "application/json, text/xml, application/xml, text/html;q=0.9,*/*;q=0.8",
        }

    def _client(self) -> httpx.Client:
        """Return this adapter's shared client.

        Building a client per request re-loaded the certificate store and
        forced a fresh TLS handshake for every call. One pooled client per
        adapter keeps connections alive between the listing and detail calls
        of a single search. ``httpx.Client`` is safe to use from the adapter's
        own worker pool.
        """
        if self._http is None:
            with self._http_lock:
                if self._http is None:
                    self._http = httpx.Client(
                        headers=self._headers(),
                        timeout=self.settings.http_timeout_seconds,
                        follow_redirects=True,
                        limits=httpx.Limits(
                            max_connections=12, max_keepalive_connections=8
                        ),
                    )
        return self._http

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = self._client().get(url, params=params)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str) -> str:
        response = self._client().get(url)
        response.raise_for_status()
        return response.text

    def probe(self) -> ProbeResult:
        started = time.perf_counter()
        try:
            self._probe()
            return ProbeResult(
                name=self.name,
                ok=True,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                detail="reachable",
            )
        except Exception as exc:
            return ProbeResult(
                name=self.name,
                ok=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                detail=str(exc)[:240] or exc.__class__.__name__,
            )

    def _probe(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def fetch(self, query: JobQuery) -> list[RawJob]:  # pragma: no cover - overridden
        raise NotImplementedError

    # --- utilities ---------------------------------------------------------
    def _limit(self, query: JobQuery, default: int = 30) -> int:
        return max(1, min(query.limit or default, 100))

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)


def excerpt(text: str, limit: int = 400) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit]
