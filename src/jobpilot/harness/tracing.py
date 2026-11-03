"""JSONL tracing — one file per run, mirrored into SQLite for the UI."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from jobpilot.core.config import get_settings
from jobpilot.core.context import current_user_id
from jobpilot.core.db import Database, from_json, get_db, to_json
from jobpilot.core.models import utcnow


class Tracer:
    def __init__(self, run_id: str, db: Database | None = None, trace_dir: Path | None = None) -> None:
        self.run_id = run_id
        self.db = db or get_db()
        self.trace_dir = trace_dir or get_settings().trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.trace_dir / f"{run_id}.jsonl"
        self._seq = 0
        self._lock = threading.Lock()

    def event(self, kind: str, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        payload = {**(data or {}), **kwargs}
        with self._lock:
            self._seq += 1
            seq = self._seq
            record = {
                "run_id": self.run_id,
                "seq": seq,
                "at": utcnow().isoformat(),
                "type": kind,
                "data": payload,
            }
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass
            try:
                self.db.execute(
                    "INSERT INTO trace_events (run_id, seq, ts, type, data) VALUES (?, ?, ?, ?, ?)",
                    (self.run_id, seq, record["at"], kind, to_json(payload)),
                )
            except Exception:
                pass

    def llm(self, model: str, tokens: int, cost: float, latency_ms: float, **extra: Any) -> None:
        self.event(
            "llm",
            model=model,
            tokens=tokens,
            cost=cost,
            latency_ms=latency_ms,
            **extra,
        )

    def tool_call(self, name: str, args: dict, tier: str) -> None:
        self.event("tool_call", name=name, args=args, tier=tier)

    def tool_result(self, name: str, ok: bool, latency_ms: float, result: Any = None, error: str = "") -> None:
        summary = ""
        if result is not None:
            try:
                summary = json.dumps(result, ensure_ascii=False, default=str)[:1200]
            except (TypeError, ValueError):
                summary = str(result)[:1200]
        self.event(
            "tool_result",
            name=name,
            ok=ok,
            latency_ms=latency_ms,
            result=summary,
            error=error,
        )

    def approval(self, approval_id: str, tool: str, status: str) -> None:
        self.event("approval", approval_id=approval_id, tool=tool, status=status)

    def finish(self, status: str, summary: str, **extra: Any) -> None:
        self.event("run_finished", status=status, summary=summary, **extra)

    @staticmethod
    def load(run_id: str, db: Database | None = None) -> list[dict]:
        database = db or get_db()
        rows = database.query(
            "SELECT seq, ts, type, data FROM trace_events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        )
        return [
            {
                "seq": row["seq"],
                "at": row["ts"],
                "type": row["type"],
                "data": from_json(row["data"], {}),
            }
            for row in rows
        ]

    @staticmethod
    def file_events(run_id: str, trace_dir: Path | None = None) -> list[dict]:
        directory = trace_dir or get_settings().trace_dir
        path = directory / f"{run_id}.jsonl"
        if not path.exists():
            return []
        events: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events


class RunStore:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def create(self, run_id: str, goal: str, session_id: str = "", mode: str = "local") -> None:
        now = datetime.now().astimezone().isoformat()
        self.db.execute(
            "INSERT OR REPLACE INTO runs (id, user_id, session_id, goal, status, "
            "started_at, mode) VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (run_id, current_user_id(), session_id, goal[:2000], now, mode),
        )

    def finish(
        self,
        run_id: str,
        status: str,
        summary: str = "",
        steps: int = 0,
        tokens: int = 0,
        cost: float = 0.0,
        error: str = "",
    ) -> None:
        self.db.execute(
            "UPDATE runs SET status = ?, finished_at = ?, summary = ?, steps = ?, "
            "tokens = ?, cost = ?, error = ? WHERE id = ? AND user_id = ?",
            (
                status,
                utcnow().isoformat(),
                summary[:4000],
                steps,
                tokens,
                cost,
                error[:2000],
                run_id,
                current_user_id(),
            ),
        )

    def get(self, run_id: str) -> dict | None:
        return self.db.query_one(
            "SELECT * FROM runs WHERE id = ? AND user_id = ?",
            (run_id, current_user_id()),
        )

    def list(self, limit: int = 30) -> list[dict]:
        return self.db.query(
            "SELECT * FROM runs WHERE user_id = ? ORDER BY started_at DESC LIMIT ?",
            (current_user_id(), limit),
        )

    def stats(self) -> dict:
        row = self.db.query_one(
            "SELECT COUNT(*) AS total, SUM(cost) AS cost, SUM(tokens) AS tokens, "
            "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed "
            "FROM runs WHERE user_id = ?",
            (current_user_id(),),
        ) or {}
        return {
            "total_runs": row.get("total") or 0,
            "completed_runs": row.get("completed") or 0,
            "total_cost": round(row.get("cost") or 0.0, 4),
            "total_tokens": row.get("tokens") or 0,
        }
