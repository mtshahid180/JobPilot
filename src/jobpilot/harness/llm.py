"""LLM gateway: one choke point for caching, retries, cost and budget caps."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from jobpilot.core.config import Settings, get_settings
from jobpilot.core.context import LOCAL_USER_ID, current_user_id
from jobpilot.core.db import Database, get_db, to_json

# USD per 1M tokens: (input, output).
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o4-mini": (1.10, 4.40),
}
DEFAULT_PRICE = (0.20, 0.80)


class LLMUnavailable(RuntimeError):
    """No key configured, or the provider rejected the request."""


class BudgetExceeded(RuntimeError):
    """A hard spend cap would be crossed."""


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 3.8) + 1)


def price_for(model: str) -> tuple[float, float]:
    lowered = (model or "").lower()
    for name, price in PRICING.items():
        if lowered.startswith(name):
            return price
    return DEFAULT_PRICE


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    inp, out = price_for(model)
    return round((tokens_in / 1_000_000) * inp + (tokens_out / 1_000_000) * out, 6)


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    cached: bool = False
    latency_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


class Gateway:
    def __init__(self, settings: Settings | None = None, db: Database | None = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or get_db()
        self.last_error = ""

    @property
    def available(self) -> bool:
        return self.settings.llm_enabled

    # --- budget ------------------------------------------------------------
    @property
    def spent_today(self) -> float:
        day = datetime.now(UTC).date().isoformat()
        return float(
            self.db.scalar(
                "SELECT spent FROM budget WHERE user_id = ? AND day = ?",
                (current_user_id(), day),
                default=0.0,
            )
        )

    @property
    def spent_total(self) -> float:
        user_id = current_user_id()
        value = self.db.get_kv(f"spend_total:{user_id}")
        if value is None and user_id == LOCAL_USER_ID:
            value = self.db.get_kv("spend_total", 0.0)
        return float(value or 0.0)

    def remaining(self) -> float:
        return round(min(self.settings.max_budget_usd - self.spent_total, self.settings.daily_budget_usd - self.spent_today), 6)

    def budget_snapshot(self) -> dict[str, float]:
        return {
            "spent_total": round(self.spent_total, 4),
            "spent_today": round(self.spent_today, 4),
            "max_total": self.settings.max_budget_usd,
            "max_daily": self.settings.daily_budget_usd,
            "remaining": self.remaining(),
        }

    def _guard_budget(self) -> None:
        if self.spent_total >= self.settings.max_budget_usd:
            raise BudgetExceeded(
                f"session budget of ${self.settings.max_budget_usd:.2f} exhausted"
            )
        if self.spent_today >= self.settings.daily_budget_usd:
            raise BudgetExceeded(
                f"daily budget of ${self.settings.daily_budget_usd:.2f} exhausted"
            )

    def _record_spend(self, cost: float, tokens: int) -> None:
        day = datetime.now(UTC).date().isoformat()
        user_id = current_user_id()
        self.db.execute(
            "INSERT INTO budget (user_id, day, spent, calls, tokens) VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET spent = spent + excluded.spent, "
            "calls = calls + 1, tokens = tokens + excluded.tokens",
            (user_id, day, cost, tokens),
        )
        self.db.set_kv(f"spend_total:{user_id}", round(self.spent_total + cost, 6))

    # --- calls -------------------------------------------------------------
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        kind: str = "chat",
        max_tokens: int = 800,
        temperature: float = 0.2,
        json_mode: bool = False,
        cache: bool = False,
        cache_ttl_days: int = 30,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        if not self.available:
            raise LLMUnavailable(
                "No OPENAI_API_KEY configured; JobPilot is running in local mode."
            )
        self._guard_budget()
        model = model or self.settings.jobs_fast_model
        cache_key = self._cache_key(model, messages, temperature, json_mode)

        if cache:
            cached = self._cache_get(cache_key, cache_ttl_days)
            if cached:
                return cached

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
            payload.pop("temperature", None)

        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries):
            try:
                with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
                    response = client.post(
                        f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.settings.openai_api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                if response.status_code in (400, 401, 403, 404):
                    self.last_error = response.text[:300]
                    raise LLMUnavailable(f"provider rejected request: {response.status_code}")
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = RuntimeError(f"HTTP {response.status_code}")
                    time.sleep(0.6 * (2**attempt))
                    continue
                response.raise_for_status()
                data = response.json()
                message = (data.get("choices") or [{}])[0].get("message", {}) or {}
                text = message.get("content", "") or ""
                tool_calls = [
                    {
                        "id": call.get("id", ""),
                        "name": (call.get("function") or {}).get("name", ""),
                        "arguments": (call.get("function") or {}).get("arguments", "{}"),
                    }
                    for call in (message.get("tool_calls") or [])
                ]
                usage = data.get("usage") or {}
                tokens_in = int(usage.get("prompt_tokens") or estimate_tokens(json.dumps(messages)))
                tokens_out = int(usage.get("completion_tokens") or estimate_tokens(text))
                cost = estimate_cost(model, tokens_in, tokens_out)
                latency = round((time.perf_counter() - started) * 1000, 1)
                result = LLMResponse(
                    text=text,
                    model=model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost=cost,
                    cached=False,
                    latency_ms=latency,
                    raw={
                        "finish_reason": (data.get("choices") or [{}])[0].get("finish_reason"),
                        "tool_calls": tool_calls,
                        "assistant_message": message,
                    },
                )
                self._record_spend(cost, result.tokens)
                if cache:
                    self._cache_put(cache_key, model, kind, result)
                return result
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                time.sleep(0.6 * (2**attempt))
            except httpx.HTTPStatusError as exc:
                self.last_error = str(exc)
                raise LLMUnavailable(str(exc)) from exc

        self.last_error = str(last_error or "unknown provider error")
        raise LLMUnavailable(f"LLM call failed after retries: {self.last_error}")

    def json_complete(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[dict[str, Any] | None, LLMResponse]:
        kwargs.setdefault("json_mode", True)
        response = self.complete(messages, **kwargs)
        return parse_json(response.text), response

    # --- cache -------------------------------------------------------------
    def _cache_key(
        self, model: str, messages: list[dict], temperature: float, json_mode: bool
    ) -> str:
        basis = json.dumps(
            {
                "user": current_user_id(),
                "model": model,
                "messages": messages,
                "t": temperature,
                "j": json_mode,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str, ttl_days: int) -> LLMResponse | None:
        row = self.db.query_one("SELECT * FROM llm_cache WHERE key = ?", (key,))
        if not row:
            return None
        try:
            created = datetime.fromisoformat(row["created_at"])
            if (datetime.now(UTC) - created).days > ttl_days:
                return None
        except ValueError:
            return None
        return LLMResponse(
            text=row["response"],
            model=row["model"],
            tokens_in=row["tokens_in"],
            tokens_out=row["tokens_out"],
            cost=0.0,
            cached=True,
        )

    def _cache_put(self, key: str, model: str, kind: str, result: LLMResponse) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO llm_cache (key, model, kind, response, tokens_in, "
            "tokens_out, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                model,
                kind,
                result.text,
                result.tokens_in,
                result.tokens_out,
                result.cost,
                datetime.now(UTC).isoformat(),
            ),
        )


def parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    try:
        value = json.loads(cleaned.strip())
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(cleaned[start : index + 1])
                    return value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        _gateway = Gateway()
    return _gateway


def record_cache_key(kind: str, payload: Any) -> str:
    return hashlib.sha256(
        (kind + json.dumps(payload, sort_keys=True, default=to_json)).encode()
    ).hexdigest()
