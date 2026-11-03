"""SQLite persistence layer."""

from __future__ import annotations

import sqlite3

import pytest

from jobpilot.core.db import SCHEMA_VERSION, Database, from_json, to_json


EXPECTED_TABLES = {
    "users",
    "jobs",
    "profile",
    "applications",
    "memories",
    "llm_cache",
    "budget",
    "runs",
    "trace_events",
    "approvals",
    "tailored_cvs",
    "cover_letters",
    "sessions",
    "messages",
    "kv",
}


def test_init_creates_all_tables_and_schema_version(db):
    tables = {
        row["name"]
        for row in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert EXPECTED_TABLES <= tables
    assert db.scalar("PRAGMA user_version") == SCHEMA_VERSION


def test_kv_roundtrip_supports_structured_values(db):
    db.set_kv("preferences", {"remote": True, "cities": ["Islamabad"]})
    assert db.get_kv("preferences") == {"remote": True, "cities": ["Islamabad"]}
    assert db.get_kv("missing", {"default": 1}) == {"default": 1}
    db.set_kv("preferences", ["overwritten"])
    assert db.get_kv("preferences") == ["overwritten"]


def test_transaction_rolls_back_on_error(db):
    db.set_kv("counter", 1)
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("UPDATE kv SET v = ? WHERE k = ?", (to_json(2), "counter"))
            raise RuntimeError("boom")
    assert db.get_kv("counter") == 1


def test_reset_recreates_an_empty_schema(db):
    db.execute(
        "INSERT INTO jobs (id, source, title, fetched_at, fingerprint) "
        "VALUES ('job_1', 'unit', 'Engineer', '2026-01-01T00:00:00+00:00', 'fp')"
    )
    assert db.scalar("SELECT COUNT(*) FROM jobs") == 1
    db.reset()
    assert db.scalar("SELECT COUNT(*) FROM jobs") == 0
    assert db.health()["ok"] is True


def test_health_reports_counts(db):
    health = db.health()
    assert health["ok"] is True
    assert health["jobs"] == 0
    assert health["applications"] == 0
    assert health["memories"] == 0
    assert health["path"].endswith("jobpilot.sqlite3")


def test_json_helpers_are_tolerant():
    assert from_json("[1, 2]") == [1, 2]
    assert from_json("not json", default={}) == {}
    assert to_json({"unicode": "café"}) == '{"unicode": "café"}'
    assert from_json(to_json({"a": 1})) == {"a": 1}


def test_foreign_keys_are_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO applications (id, job_id, status, created_at, updated_at) "
            "VALUES ('app_1', 'missing-job', 'saved', '2026-01-01', '2026-01-01')"
        )


def test_database_uses_explicit_path(tmp_path):
    other = Database(tmp_path / "nested" / "other.sqlite3")
    other.init()
    assert other.path.exists()
    assert other.health()["path"].endswith("other.sqlite3")


V1_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL, company TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
    salary TEXT NOT NULL DEFAULT '', employment_type TEXT NOT NULL DEFAULT '',
    remote INTEGER NOT NULL DEFAULT 0, seniority TEXT NOT NULL DEFAULT 'unspecified',
    skills TEXT NOT NULL DEFAULT '[]', posted_at TEXT, fetched_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE, saved INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new', score REAL,
    score_breakdown TEXT NOT NULL DEFAULT '{}', raw TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE profile (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE budget (day TEXT PRIMARY KEY, spent REAL NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0);
PRAGMA user_version = 1;
"""


def test_v1_database_migrates_to_private_workspaces(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript(V1_SCHEMA)
    legacy.execute(
        "INSERT INTO jobs (id, source, title, fetched_at, fingerprint) "
        "VALUES ('job_old', 'remotive', 'AI Engineer', '2026-01-01T00:00:00+00:00', 'legacy-fp')"
    )
    legacy.execute(
        "INSERT INTO profile (id, data, updated_at) VALUES (1, '{\"full_name\": \"Legacy\"}', "
        "'2026-01-01T00:00:00+00:00')"
    )
    legacy.execute(
        "INSERT INTO budget (day, spent, calls, tokens) VALUES ('2026-01-01', 0.5, 2, 100)"
    )
    legacy.commit()
    legacy.close()

    database = Database(path)
    database.init()

    job = database.query_one("SELECT * FROM jobs WHERE id = 'job_old'")
    assert job["user_id"] == "local"
    assert job["fingerprint"] == "local:legacy-fp"

    profile = database.query_one("SELECT * FROM profile")
    assert profile["user_id"] == "local"
    assert "Legacy" in profile["data"]

    budget = database.query_one("SELECT * FROM budget")
    assert budget["user_id"] == "local"
    assert budget["spent"] == 0.5

    assert database.scalar("PRAGMA user_version") == SCHEMA_VERSION
    assert database.query_one("SELECT 1 FROM users LIMIT 1") is None

