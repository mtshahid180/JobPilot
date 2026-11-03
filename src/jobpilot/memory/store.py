"""Long-term memory storage: semantic facts, preferences and episodes."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from jobpilot.core.context import current_user_id
from jobpilot.core.db import Database, from_json, get_db, to_json
from jobpilot.core.models import Memory, utcnow
from jobpilot.pipelines.embedding import cosine, embed_text, from_blob, to_blob

MERGE_THRESHOLD = 0.92


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


class MemoryStore:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    # --- reads -------------------------------------------------------------
    def get(self, memory_id: str) -> Memory | None:
        row = self.db.query_one(
            "SELECT * FROM memories WHERE id = ? AND user_id = ?",
            (memory_id, current_user_id()),
        )
        return self._to_model(row) if row else None

    def list(
        self,
        kind: str | None = None,
        status: str = "active",
        limit: int = 200,
    ) -> list[Memory]:
        clauses = ["user_id = ?"]
        params: list = [current_user_id()]
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if status != "all":
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}"
        rows = self.db.query(
            f"SELECT * FROM memories {where} ORDER BY importance DESC, created_at DESC LIMIT ?",
            (*params, limit),
        )
        return [self._to_model(row) for row in rows]

    def search(self, query: str, limit: int = 10) -> list[Memory]:
        return self.recall(query, k=limit)

    def recall(self, query: str, k: int = 6) -> list[Memory]:
        memories = self.list(status="active", limit=400)
        if not memories:
            return []
        qvec = embed_text(query)
        now = datetime.now(UTC)
        scored: list[tuple[float, Memory]] = []
        for memory in memories:
            vec = from_blob(self._blob(memory.id))
            sim = cosine(qvec, vec)
            age_days = max(0.0, (now - memory.created_at).total_seconds() / 86400)
            recency = math.exp(-age_days / 120)
            score = 0.65 * sim + 0.20 * memory.importance + 0.15 * recency
            scored.append((score, memory))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = [memory for score, memory in scored[:k] if score > 0.08]
        for memory in top:
            self.db.execute(
                "UPDATE memories SET last_used_at = ?, use_count = use_count + 1 "
                "WHERE id = ? AND user_id = ?",
                (utcnow().isoformat(), memory.id, current_user_id()),
            )
        return top

    def count(self, status: str = "active") -> int:
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM memories WHERE status = ? AND user_id = ?",
                (status, current_user_id()),
                default=0,
            )
        )

    # --- writes ------------------------------------------------------------
    def remember(
        self,
        content: str,
        kind: str = "semantic",
        tags: list[str] | None = None,
        provenance: str = "",
        confidence: float = 0.8,
        importance: float = 0.5,
        status: str = "active",
    ) -> tuple[Memory, str]:
        """Store a memory, merging near-duplicates.

        Returns ``(memory, action)`` where action is ``created``, ``merged`` or
        ``conflict``. A conflict stores the new memory as pending for review.
        """
        from jobpilot.memory.policy import detect_conflict

        content = _norm(content)
        tags = list(dict.fromkeys(tags or []))
        if len(content) < 4:
            raise ValueError("memory content is too short")

        active = self.list(status="active", limit=400)
        vec = embed_text(content)
        best: tuple[float, Memory] | None = None
        for memory in active:
            sim = cosine(vec, from_blob(self._blob(memory.id)))
            if best is None or sim > best[0]:
                best = (sim, memory)
        if best and best[0] >= MERGE_THRESHOLD:
            existing = best[1]
            merged_tags = sorted(set(existing.tags) | set(tags))
            new_content = content if len(content) > len(existing.content) else existing.content
            new_confidence = max(existing.confidence, confidence)
            self.db.execute(
                "UPDATE memories SET content = ?, tags = ?, confidence = ?, "
                "importance = ?, last_used_at = ? WHERE id = ? AND user_id = ?",
                (
                    new_content,
                    to_json(merged_tags),
                    new_confidence,
                    max(existing.importance, importance),
                    utcnow().isoformat(),
                    existing.id,
                    current_user_id(),
                ),
            )
            existing.content = new_content
            existing.tags = merged_tags
            existing.confidence = new_confidence
            return existing, "merged"

        conflict = detect_conflict(content, active)
        if conflict is not None:
            memory = Memory(
                kind=kind,  # type: ignore[arg-type]
                content=content,
                tags=tags,
                provenance=provenance,
                confidence=confidence,
                importance=importance,
                status="pending",
                supersedes=conflict.id,
            )
            self._insert(memory, to_blob(vec))
            return memory, "conflict"

        memory = Memory(
            kind=kind,  # type: ignore[arg-type]
            content=content,
            tags=tags,
            provenance=provenance,
            confidence=confidence,
            importance=importance,
            status=status,
        )
        self._insert(memory, to_blob(vec))
        return memory, "created"

    def replace(self, memory_id: str, content: str) -> Memory | None:
        existing = self.get(memory_id)
        if not existing:
            return None
        self.db.execute(
            "UPDATE memories SET content = ?, status = 'active', embedding = ? "
            "WHERE id = ? AND user_id = ?",
            (
                _norm(content),
                to_blob(embed_text(content)),
                memory_id,
                current_user_id(),
            ),
        )
        return self.get(memory_id)

    def forget(self, memory_id: str) -> bool:
        existing = self.get(memory_id)
        if not existing or existing.status == "forgotten":
            return False
        self.db.execute(
            "UPDATE memories SET status = 'forgotten' WHERE id = ? AND user_id = ?",
            (memory_id, current_user_id()),
        )
        return True

    def approve(self, memory_id: str) -> Memory | None:
        memory = self.get(memory_id)
        if not memory:
            return None
        if memory.supersedes:
            self.db.execute(
                "UPDATE memories SET status = 'superseded' WHERE id = ? AND user_id = ?",
                (memory.supersedes, current_user_id()),
            )
        self.db.execute(
            "UPDATE memories SET status = 'active' WHERE id = ? AND user_id = ?",
            (memory_id, current_user_id()),
        )
        return self.get(memory_id)

    # --- internals ---------------------------------------------------------
    def _insert(self, memory: Memory, embedding: bytes) -> None:
        self.db.execute(
            "INSERT INTO memories (id, user_id, kind, content, tags, provenance, confidence, "
            "importance, status, embedding, created_at, last_used_at, use_count, supersedes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory.id,
                current_user_id(),
                memory.kind,
                memory.content,
                to_json(memory.tags),
                memory.provenance,
                memory.confidence,
                memory.importance,
                memory.status,
                embedding,
                memory.created_at.isoformat(),
                None,
                0,
                memory.supersedes,
            ),
        )

    def _blob(self, memory_id: str) -> bytes | None:
        row = self.db.query_one(
            "SELECT embedding FROM memories WHERE id = ? AND user_id = ?",
            (memory_id, current_user_id()),
        )
        return row["embedding"] if row else None

    def _to_model(self, row: dict) -> Memory:
        return Memory(
            id=row["id"],
            kind=row["kind"],
            content=row["content"],
            tags=from_json(row["tags"], []),
            provenance=row["provenance"],
            confidence=row["confidence"],
            importance=row["importance"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_used_at=datetime.fromisoformat(row["last_used_at"])
            if row["last_used_at"]
            else None,
            use_count=row["use_count"],
            supersedes=row["supersedes"],
        )
