"""Account creation, authentication and workspace resolution."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from jobpilot.core.context import LOCAL_USER_ID, user_scope
from jobpilot.core.db import Database, get_db
from jobpilot.core.models import Profile, User, new_id, utcnow

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


class AccountError(ValueError):
    """A user-facing account validation error."""


class UserService:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    # --- writes ------------------------------------------------------------
    def create(
        self,
        email: str,
        password: str,
        name: str = "",
        *,
        user_id: str | None = None,
    ) -> User:
        email = _normalize_email(email)
        if not EMAIL_RE.match(email):
            raise AccountError("Enter a valid email address.")
        if len(password or "") < MIN_PASSWORD_LENGTH:
            raise AccountError(
                f"Use at least {MIN_PASSWORD_LENGTH} characters for your password."
            )
        if len(name.strip()) > 120:
            raise AccountError("Name is too long.")

        now = utcnow()
        user = User(
            id=user_id or new_id("usr"),
            email=email,
            name=name.strip(),
            created_at=now,
            updated_at=now,
        )
        try:
            self.db.execute(
                "INSERT INTO users (id, email, name, password_hash, is_active, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (
                    user.id,
                    user.email,
                    user.name,
                    generate_password_hash(password),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise AccountError("An account with that email already exists.") from exc

        # Every account starts with a clean, private profile, unless one already
        # exists for this workspace (tests, or a migrated legacy account).
        with user_scope(user.id):
            from jobpilot.services.profile import ProfileService

            profiles = ProfileService(self.db)
            if self.db.query_one(
                "SELECT 1 AS present FROM profile WHERE user_id = ?", (user.id,)
            ) is None:
                profiles.save(Profile())
        return user

    def touch_login(self, user_id: str) -> None:
        now = utcnow().isoformat()
        self.db.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now, now, user_id),
        )

    # --- reads -------------------------------------------------------------
    def get(self, user_id: str) -> User | None:
        row = self.db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        return _to_user(row) if row else None

    def get_by_email(self, email: str) -> User | None:
        row = self.db.query_one(
            "SELECT * FROM users WHERE email = ?", (_normalize_email(email),)
        )
        return _to_user(row) if row else None

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM users", default=0))

    def authenticate(self, email: str, password: str) -> User | None:
        row = self.db.query_one(
            "SELECT * FROM users WHERE email = ?", (_normalize_email(email),)
        )
        if not row or not row["is_active"]:
            return None
        if not check_password_hash(row["password_hash"] or "", password or ""):
            return None
        user = _to_user(row)
        self.touch_login(user.id)
        return self.get(user.id)

    def update_password(self, user_id: str, current: str, new: str) -> User:
        row = self.db.query_one("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        if not row:
            raise AccountError("Account not found.")
        if not check_password_hash(row["password_hash"] or "", current or ""):
            raise AccountError("Current password is incorrect.")
        if len(new or "") < MIN_PASSWORD_LENGTH:
            raise AccountError(
                f"Use at least {MIN_PASSWORD_LENGTH} characters for your password."
            )
        self.db.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (generate_password_hash(new), utcnow().isoformat(), user_id),
        )
        return self.get(user_id)  # type: ignore[return-value]


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _to_user(row: dict) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_login_at=datetime.fromisoformat(row["last_login_at"])
        if row.get("last_login_at")
        else None,
    )


def resolve_workspace_user() -> str:
    """Resolve the CLI/MCP workspace user from the environment.

    The web UI always uses the signed-in account. Headless transports have no
    request context, so they can bind a specific account with
    ``JOBPILOT_USER_EMAIL`` (preferred) or ``JOBPILOT_USER_ID``. Without either,
    they use the local admin workspace.
    """
    explicit = (os.environ.get("JOBPILOT_USER_ID") or "").strip()
    if explicit:
        return explicit
    email = (os.environ.get("JOBPILOT_USER_EMAIL") or "").strip()
    if not email:
        return LOCAL_USER_ID
    user = UserService().get_by_email(email)
    if not user:
        raise RuntimeError(
            f"JOBPILOT_USER_EMAIL={email!r} does not match a JobPilot account."
        )
    return user.id
