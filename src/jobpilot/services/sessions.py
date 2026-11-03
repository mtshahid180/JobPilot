"""Chat session persistence for the agent console."""

from __future__ import annotations

from datetime import datetime

from jobpilot.core.context import current_user_id
from jobpilot.core.db import Database, from_json, get_db, to_json
from jobpilot.core.models import new_id, utcnow


class SessionService:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def create(self, title: str = "New session") -> dict:
        session_id = new_id("ses")
        now = utcnow().isoformat()
        self.db.execute(
            "INSERT INTO sessions (id, user_id, title, summary, created_at, updated_at) "
            "VALUES (?, ?, ?, '', ?, ?)",
            (session_id, current_user_id(), title[:160], now, now),
        )
        return self.get(session_id) or {}

    def get(self, session_id: str) -> dict | None:
        return self.db.query_one(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, current_user_id()),
        )

    def get_or_create(self, session_id: str | None = None) -> dict:
        if session_id:
            row = self.get(session_id)
            if row:
                return row
        return self.create()

    def list(self, limit: int = 30) -> list[dict]:
        return self.db.query(
            "SELECT * FROM sessions WHERE user_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (current_user_id(), limit),
        )

    def messages(self, session_id: str, limit: int = 100) -> list[dict]:
        return self.db.query(
            "SELECT m.* FROM messages m JOIN sessions s ON s.id = m.session_id "
            "WHERE m.session_id = ? AND s.user_id = ? "
            "ORDER BY m.created_at ASC LIMIT ?",
            (session_id, current_user_id(), limit),
        )

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        run_id: str = "",
    ) -> dict:
        if not self.get(session_id):
            raise ValueError(f"session not found: {session_id}")
        message_id = new_id("msg")
        now = utcnow().isoformat()
        self.db.execute(
            "INSERT INTO messages (id, session_id, role, content, run_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, session_id, role, content, run_id, now),
        )
        self.db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ? AND user_id = ?",
            (now, session_id, current_user_id()),
        )
        if role == "user":
            title = content.strip().replace("\n", " ")[:72]
            self.db.execute(
                "UPDATE sessions SET title = ? WHERE id = ? AND user_id = ? "
                "AND title = 'New session'",
                (title, session_id, current_user_id()),
            )
        return {
            "id": message_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "run_id": run_id,
            "created_at": now,
        }

    def update_summary(self, session_id: str, summary: str) -> None:
        self.db.execute(
            "UPDATE sessions SET summary = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (summary[-4000:], utcnow().isoformat(), session_id, current_user_id()),
        )

    # --- last search context ------------------------------------------------
    def get_context(self, session_id: str) -> dict:
        row = self.get(session_id)
        if not row:
            return {}
        value = from_json(row.get("context"), {})
        return value if isinstance(value, dict) else {}

    def set_context(self, session_id: str, context: dict) -> dict:
        """Persist the last resolved search so follow-ups can change one axis."""
        clean = {
            key: value
            for key, value in (context or {}).items()
            if key
            in (
                "query",
                "location",
                "remote_only",
                "workplace_type",
                "seniority",
                "category",
                "employment_type",
            )
            and value not in (None, "", "any", "other")
        }
        self.db.execute(
            "UPDATE sessions SET context = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (to_json(clean), utcnow().isoformat(), session_id, current_user_id()),
        )
        return clean

    def rename(self, session_id: str, title: str) -> dict | None:
        self.db.execute(
            "UPDATE sessions SET title = ? WHERE id = ? AND user_id = ?",
            (title[:160], session_id, current_user_id()),
        )
        return self.get(session_id)

    def delete(self, session_id: str) -> bool:
        existing = self.get(session_id)
        if not existing:
            return False
        self.db.execute(
            "DELETE FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, current_user_id()),
        )
        return True

    def history(self, session_id: str, limit: int = 20) -> list[dict]:
        messages = self.messages(session_id, limit=limit)
        return [
            {"role": row["role"], "content": row["content"]}
            for row in messages
            if row["role"] in ("user", "assistant")
        ]


_sessions: SessionService | None = None


def get_session_service() -> SessionService:
    global _sessions
    if _sessions is None:
        _sessions = SessionService()
    return _sessions
