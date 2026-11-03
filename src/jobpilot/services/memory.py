"""High-level memory operations used by the UI, tools and MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from jobpilot.core.db import Database, get_db
from jobpilot.core.models import Memory, MemoryProposal
from jobpilot.memory.policy import importance_for, propose
from jobpilot.memory.store import MemoryStore


@dataclass
class RememberResult:
    memory: Memory | None
    action: str
    proposal: MemoryProposal
    message: str


class MemoryService:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()
        self.store = MemoryStore(self.db)

    def remember(
        self,
        content: str,
        kind: str | None = None,
        provenance: str = "ui",
        confirmed: bool = False,
    ) -> RememberResult:
        proposal = propose(content, provenance=provenance)
        if kind:
            proposal.kind = kind  # type: ignore[assignment]
        if proposal.requires_confirmation and not confirmed:
            memory, action = self.store.remember(
                content=proposal.content,
                kind=proposal.kind,
                tags=proposal.tags,
                provenance=provenance,
                importance=importance_for(proposal.kind),
                status="pending",
            )
            return RememberResult(
                memory=memory,
                action="pending_confirmation",
                proposal=proposal,
                message="This looks like a profile fact. Confirm it before I store it.",
            )
        memory, action = self.store.remember(
            content=proposal.content,
            kind=proposal.kind,
            tags=proposal.tags,
            provenance=provenance,
            importance=importance_for(proposal.kind),
        )
        message = {
            "created": "Saved to long-term memory.",
            "merged": "Merged with a similar memory.",
            "conflict": "This conflicts with an existing memory — review it before I use it.",
        }.get(action, "Memory updated.")
        return RememberResult(memory=memory, action=action, proposal=proposal, message=message)

    def list(self, status: str = "active", kind: str | None = None, limit: int = 200) -> list[Memory]:
        return self.store.list(kind=kind, status=status, limit=limit)

    def recall(self, query: str, limit: int = 6) -> list[Memory]:
        return self.store.recall(query, k=limit)

    def forget(self, memory_id: str) -> bool:
        return self.store.forget(memory_id)

    def approve(self, memory_id: str) -> Memory | None:
        return self.store.approve(memory_id)

    def reject(self, memory_id: str) -> bool:
        return self.store.forget(memory_id)

    def count(self, status: str = "active") -> int:
        return self.store.count(status)

    def rules(self) -> list[Memory]:
        rules = self.store.list(kind="rule", status="active", limit=20)
        prefs = self.store.list(kind="preference", status="active", limit=20)
        return rules + prefs

    def stats(self) -> dict:
        by_kind: dict[str, int] = {}
        for memory in self.store.list(status="all", limit=500):
            by_kind[memory.kind] = by_kind.get(memory.kind, 0) + 1
        return {
            "active": self.store.count("active"),
            "pending": self.store.count("pending"),
            "by_kind": by_kind,
        }
