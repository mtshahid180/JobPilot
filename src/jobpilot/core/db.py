"""SQLite persistence layer.

One file, explicit SQL, no ORM. Connections are short-lived which keeps the
module safe under Flask's threaded server and the MCP stdio servers.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jobpilot.core.config import get_settings

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_login_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL DEFAULT 'local',
    source            TEXT NOT NULL,
    external_id       TEXT NOT NULL DEFAULT '',
    title             TEXT NOT NULL,
    company           TEXT NOT NULL DEFAULT '',
    location          TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    url               TEXT NOT NULL DEFAULT '',
    salary            TEXT NOT NULL DEFAULT '',
    employment_type   TEXT NOT NULL DEFAULT '',
    remote            INTEGER NOT NULL DEFAULT 0,
    seniority         TEXT NOT NULL DEFAULT 'unspecified',
    skills            TEXT NOT NULL DEFAULT '[]',
    posted_at         TEXT,
    fetched_at        TEXT NOT NULL,
    fingerprint       TEXT NOT NULL,
    saved             INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'new',
    score             REAL,
    score_breakdown   TEXT NOT NULL DEFAULT '{}',
    raw               TEXT NOT NULL DEFAULT '{}',
    category          TEXT NOT NULL DEFAULT 'other',
    category_label    TEXT NOT NULL DEFAULT '',
    workplace_type    TEXT NOT NULL DEFAULT 'unknown',
    company_location  TEXT NOT NULL DEFAULT '',
    job_location      TEXT NOT NULL DEFAULT '',
    eligible_locations TEXT NOT NULL DEFAULT '[]',
    remote_scope      TEXT NOT NULL DEFAULT 'unknown',
    location_confidence TEXT NOT NULL DEFAULT 'unknown',
    location_restrictions TEXT NOT NULL DEFAULT '[]',
    expires_at        TEXT,
    availability      TEXT NOT NULL DEFAULT 'unknown',
    last_seen_at      TEXT,
    relevance         REAL NOT NULL DEFAULT 0,
    match_tier        TEXT NOT NULL DEFAULT 'exact',
    relevance_reasons TEXT NOT NULL DEFAULT '[]',
    UNIQUE(user_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_jobs_user     ON jobs(user_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_posted   ON jobs(posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_score    ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source   ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_saved    ON jobs(saved);
CREATE INDEX IF NOT EXISTS idx_jobs_category ON jobs(user_id, category);

CREATE TABLE IF NOT EXISTS profile (
    user_id    TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'local',
    job_id        TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'saved',
    applied_at    TEXT,
    follow_up_at  TEXT,
    notes         TEXT NOT NULL DEFAULT '',
    history       TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apps_user   ON applications(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_apps_job    ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_apps_unique ON applications(job_id);

CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'local',
    kind         TEXT NOT NULL DEFAULT 'semantic',
    content      TEXT NOT NULL,
    tags         TEXT NOT NULL DEFAULT '[]',
    provenance   TEXT NOT NULL DEFAULT '',
    confidence   REAL NOT NULL DEFAULT 0.8,
    importance   REAL NOT NULL DEFAULT 0.5,
    status       TEXT NOT NULL DEFAULT 'active',
    embedding    BLOB,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    use_count    INTEGER NOT NULL DEFAULT 0,
    supersedes   TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_user   ON memories(user_id, status);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);

CREATE TABLE IF NOT EXISTS llm_cache (
    key        TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'chat',
    response   TEXT NOT NULL,
    tokens_in  INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost       REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budget (
    user_id TEXT NOT NULL DEFAULT 'local',
    day     TEXT NOT NULL,
    spent   REAL NOT NULL DEFAULT 0,
    calls   INTEGER NOT NULL DEFAULT 0,
    tokens  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'local',
    session_id  TEXT NOT NULL DEFAULT '',
    goal        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'running',
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    steps       INTEGER NOT NULL DEFAULT 0,
    tokens      INTEGER NOT NULL DEFAULT 0,
    cost        REAL NOT NULL DEFAULT 0,
    summary     TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    mode        TEXT NOT NULL DEFAULT 'local'
);
CREATE INDEX IF NOT EXISTS idx_runs_user    ON runs(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS trace_events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seq    INTEGER NOT NULL,
    ts     TEXT NOT NULL,
    type   TEXT NOT NULL,
    data   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_trace_run ON trace_events(run_id, seq);

CREATE TABLE IF NOT EXISTS approvals (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'local',
    run_id     TEXT NOT NULL DEFAULT '',
    tool       TEXT NOT NULL,
    args       TEXT NOT NULL DEFAULT '{}',
    reason     TEXT NOT NULL DEFAULT '',
    tier       TEXT NOT NULL DEFAULT 'external',
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_user   ON approvals(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

CREATE TABLE IF NOT EXISTS tailored_cvs (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL DEFAULT 'local',
    job_id           TEXT NOT NULL,
    resume_id        TEXT NOT NULL DEFAULT '',
    headline         TEXT NOT NULL DEFAULT '',
    summary          TEXT NOT NULL DEFAULT '',
    skills           TEXT NOT NULL DEFAULT '[]',
    bullets          TEXT NOT NULL DEFAULT '[]',
    changes          TEXT NOT NULL DEFAULT '[]',
    files            TEXT NOT NULL DEFAULT '{}',
    checks           TEXT NOT NULL DEFAULT '[]',
    unsupported_count INTEGER NOT NULL DEFAULT 0,
    verified         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cvs_user ON tailored_cvs(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS cover_letters (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL DEFAULT 'local',
    job_id           TEXT NOT NULL,
    body             TEXT NOT NULL DEFAULT '',
    checks           TEXT NOT NULL DEFAULT '[]',
    unsupported_count INTEGER NOT NULL DEFAULT 0,
    verified         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_letters_user ON cover_letters(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'local',
    title      TEXT NOT NULL DEFAULT 'New session',
    summary    TEXT NOT NULL DEFAULT '',
    context    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    run_id     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS resumes (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'local',
    filename     TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT '',
    size         INTEGER NOT NULL DEFAULT 0,
    storage_path TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'uploaded',
    error        TEXT NOT NULL DEFAULT '',
    extracted    TEXT NOT NULL DEFAULT '{}',
    text_chars   INTEGER NOT NULL DEFAULT 0,
    is_current   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resumes_user ON resumes(user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_resumes_current
    ON resumes(user_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _add_user_id(conn: sqlite3.Connection, table: str) -> None:
    if _table_exists(conn, table) and "user_id" not in _columns(conn, table):
        conn.execute(
            f'ALTER TABLE "{table}" ADD COLUMN user_id TEXT NOT NULL DEFAULT \'local\''
        )


JOB_V3_COLUMNS: dict[str, str] = {
    "category": "TEXT NOT NULL DEFAULT 'other'",
    "category_label": "TEXT NOT NULL DEFAULT ''",
    "workplace_type": "TEXT NOT NULL DEFAULT 'unknown'",
    "company_location": "TEXT NOT NULL DEFAULT ''",
    "job_location": "TEXT NOT NULL DEFAULT ''",
    "eligible_locations": "TEXT NOT NULL DEFAULT '[]'",
    "remote_scope": "TEXT NOT NULL DEFAULT 'unknown'",
    "location_confidence": "TEXT NOT NULL DEFAULT 'unknown'",
    "location_restrictions": "TEXT NOT NULL DEFAULT '[]'",
    "expires_at": "TEXT",
    "availability": "TEXT NOT NULL DEFAULT 'unknown'",
    "last_seen_at": "TEXT",
    "relevance": "REAL NOT NULL DEFAULT 0",
    "match_tier": "TEXT NOT NULL DEFAULT 'exact'",
    "relevance_reasons": "TEXT NOT NULL DEFAULT '[]'",
}


def _add_job_v3_columns(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "jobs"):
        return
    existing = _columns(conn, "jobs")
    for column, definition in JOB_V3_COLUMNS.items():
        if column not in existing:
            conn.execute(f'ALTER TABLE "jobs" ADD COLUMN "{column}" {definition}')


def _add_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    if not _table_exists(conn, table):
        return
    existing = _columns(conn, table)
    for column, definition in columns.items():
        if column not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


SESSION_V4_COLUMNS = {
    "context": "TEXT NOT NULL DEFAULT '{}'",
}

TAILORED_CV_V4_COLUMNS = {
    "resume_id": "TEXT NOT NULL DEFAULT ''",
    "changes": "TEXT NOT NULL DEFAULT '[]'",
    "files": "TEXT NOT NULL DEFAULT '{}'",
}


def _add_v4_columns(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "sessions", SESSION_V4_COLUMNS)
    _add_columns(conn, "tailored_cvs", TAILORED_CV_V4_COLUMNS)


def _prepare_existing_database(conn: sqlite3.Connection) -> None:
    """Upgrade an existing database in place before the schema is re-applied.

    v1 kept one global profile, one global budget row per day and globally
    unique job fingerprints. v2 scopes all of them by ``user_id``. Existing
    local data is preserved under the ``local`` workspace. v3 added the
    geography/workplace columns and v4 adds chat search context plus generated
    file metadata.
    """
    _add_v4_columns(conn)
    if not _table_exists(conn, "jobs"):
        return

    _add_job_v3_columns(conn)

    for table in (
        "jobs",
        "applications",
        "memories",
        "runs",
        "approvals",
        "tailored_cvs",
        "cover_letters",
        "sessions",
    ):
        _add_user_id(conn, table)

    if _table_exists(conn, "profile") and "user_id" not in _columns(conn, "profile"):
        conn.execute("ALTER TABLE profile RENAME TO profile_v1")
        conn.execute(
            "CREATE TABLE profile ("
            "user_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO profile (user_id, data, updated_at) "
            "SELECT 'local', data, updated_at FROM profile_v1 WHERE id = 1"
        )
        conn.execute("DROP TABLE profile_v1")

    if _table_exists(conn, "budget") and "user_id" not in _columns(conn, "budget"):
        conn.execute("ALTER TABLE budget RENAME TO budget_v1")
        conn.execute(
            "CREATE TABLE budget ("
            "user_id TEXT NOT NULL DEFAULT 'local', day TEXT NOT NULL, "
            "spent REAL NOT NULL DEFAULT 0, calls INTEGER NOT NULL DEFAULT 0, "
            "tokens INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, day))"
        )
        conn.execute(
            "INSERT INTO budget (user_id, day, spent, calls, tokens) "
            "SELECT 'local', day, spent, calls, tokens FROM budget_v1"
        )
        conn.execute("DROP TABLE budget_v1")

    # Keep the legacy UNIQUE(fingerprint) index working for multi-user data by
    # namespacing each stored fingerprint with its owner. New databases use a
    # composite unique constraint and receive the same namespaced value.
    for row in conn.execute("SELECT id, user_id, fingerprint FROM jobs").fetchall():
        fingerprint = row["fingerprint"] or ""
        prefix = f"{row['user_id']}:"
        if not fingerprint.startswith(prefix):
            conn.execute(
                "UPDATE jobs SET fingerprint = ? WHERE id = ?",
                (prefix + fingerprint, row["id"]),
            )


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def from_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class Database:
    """Thin, explicit SQLite wrapper."""

    def __init__(self, path: str | Path | None = None) -> None:
        settings = get_settings()
        self.path = Path(path) if path is not None else settings.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if not row:
            return default
        return next(iter(row.values()))

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        conn = self.connect()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid or 0
        finally:
            conn.close()

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> None:
        conn = self.connect()
        try:
            conn.executemany(sql, seq)
            conn.commit()
        finally:
            conn.close()

    # --- helpers -----------------------------------------------------------
    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT v FROM kv WHERE k = ?", (key,))
        return from_json(row["v"], default) if row else default

    def set_kv(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, to_json(value)),
        )

    def init(self) -> None:
        with self.transaction() as conn:
            _prepare_existing_database(conn)
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def reset(self) -> None:
        """Drop every table. Used by tests and the CLI `reset` command."""
        with self.transaction() as conn:
            tables = [
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for table in tables:
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        self.init()

    def health(self, user_id: str | None = None) -> dict[str, Any]:
        try:
            scope = " WHERE user_id = ?" if user_id else ""
            params: tuple[Any, ...] = (user_id,) if user_id else ()
            jobs = self.scalar(f"SELECT COUNT(*) FROM jobs{scope}", params, default=0)
            apps = self.scalar(
                f"SELECT COUNT(*) FROM applications{scope}", params, default=0
            )
            if user_id:
                mems = self.scalar(
                    "SELECT COUNT(*) FROM memories WHERE status = 'active' AND user_id = ?",
                    (user_id,),
                    default=0,
                )
            else:
                mems = self.scalar(
                    "SELECT COUNT(*) FROM memories WHERE status = 'active'", default=0
                )
            # Scoped health should not reveal how many other accounts exist.
            users = (
                1 if user_id else self.scalar("SELECT COUNT(*) FROM users", default=0)
            )
            return {
                "ok": True,
                "path": str(self.path),
                "size_kb": round(self.path.stat().st_size / 1024, 1)
                if self.path.exists()
                else 0,
                "users": users,
                "jobs": jobs,
                "applications": apps,
                "memories": mems,
            }
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            return {"ok": False, "error": str(exc), "path": str(self.path)}


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
        _db.init()
    return _db


def set_db(db: Database) -> None:
    """Inject a database (tests)."""
    global _db
    _db = db
