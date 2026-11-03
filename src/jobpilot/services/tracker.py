"""Application tracking with a status history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobpilot.core.context import current_user_id
from jobpilot.core.db import Database, from_json, get_db, to_json
from jobpilot.core.models import Application, ApplicationEvent, utcnow
from jobpilot.services.jobs import JobsService, get_jobs_service

STATUSES = ["saved", "applied", "screening", "interview", "offer", "rejected", "withdrawn"]
ACTIVE_STATUSES = {"applied", "screening", "interview", "offer"}


class TrackerService:
    def __init__(self, db: Database | None = None, jobs: JobsService | None = None) -> None:
        self.db = db or get_db()
        self.jobs = jobs or get_jobs_service()

    def add_application(
        self,
        job_id: str,
        status: str = "applied",
        notes: str = "",
        follow_up_days: int = 7,
    ) -> Application:
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        job = self.jobs.get(job_id)
        if not job:
            raise ValueError(f"job not found: {job_id}")
        existing = self.by_job(job_id)
        if existing:
            return self.update_status(existing.id, status, note=notes or "status updated")

        now = utcnow()
        history = [ApplicationEvent(at=now, status=status, note=notes or "application created")]
        app = Application(
            job_id=job_id,
            status=status,  # type: ignore[arg-type]
            applied_at=now if status == "applied" else None,
            follow_up_at=now + timedelta(days=follow_up_days) if status in ACTIVE_STATUSES else None,
            notes=notes,
            history=history,
            created_at=now,
            updated_at=now,
        )
        self.db.execute(
            "INSERT INTO applications (id, user_id, job_id, status, applied_at, follow_up_at, "
            "notes, history, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                app.id,
                current_user_id(),
                app.job_id,
                app.status,
                app.applied_at.isoformat() if app.applied_at else None,
                app.follow_up_at.isoformat() if app.follow_up_at else None,
                app.notes,
                to_json([event.model_dump(mode="json") for event in history]),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        self.jobs.set_status(job_id, status)
        return self.get(app.id) or app

    def update_status(self, application_id: str, status: str, note: str = "") -> Application:
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        app = self.get(application_id)
        if not app:
            raise ValueError(f"application not found: {application_id}")
        now = utcnow()
        history = app.history + [ApplicationEvent(at=now, status=status, note=note)]
        applied_at = app.applied_at or (now if status == "applied" else None)
        follow_up = app.follow_up_at
        if status in ACTIVE_STATUSES and not follow_up:
            follow_up = now + timedelta(days=7)
        if status in ("rejected", "withdrawn", "offer"):
            follow_up = None
        self.db.execute(
            "UPDATE applications SET status = ?, applied_at = ?, follow_up_at = ?, "
            "notes = ?, history = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (
                status,
                applied_at.isoformat() if applied_at else None,
                follow_up.isoformat() if follow_up else None,
                note or app.notes,
                to_json([event.model_dump(mode="json") for event in history]),
                now.isoformat(),
                application_id,
                current_user_id(),
            ),
        )
        self.jobs.set_status(app.job_id, status)
        return self.get(application_id) or app

    def set_follow_up(self, application_id: str, days: int = 7) -> Application | None:
        when = utcnow() + timedelta(days=days)
        self.db.execute(
            "UPDATE applications SET follow_up_at = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (
                when.isoformat(),
                utcnow().isoformat(),
                application_id,
                current_user_id(),
            ),
        )
        return self.get(application_id)

    def get(self, application_id: str) -> Application | None:
        row = self.db.query_one(
            "SELECT * FROM applications WHERE id = ? AND user_id = ?",
            (application_id, current_user_id()),
        )
        return self._to_model(row) if row else None

    def by_job(self, job_id: str) -> Application | None:
        row = self.db.query_one(
            "SELECT * FROM applications WHERE job_id = ? AND user_id = ?",
            (job_id, current_user_id()),
        )
        return self._to_model(row) if row else None

    def list(self, status: str = "", limit: int = 100) -> list[Application]:
        if status:
            rows = self.db.query(
                "SELECT * FROM applications WHERE status = ? AND user_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (status, current_user_id(), limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM applications WHERE user_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (current_user_id(), limit),
            )
        return [self._to_model(row) for row in rows]

    def delete(self, application_id: str) -> bool:
        existing = self.get(application_id)
        if not existing:
            return False
        self.db.execute(
            "DELETE FROM applications WHERE id = ? AND user_id = ?",
            (application_id, current_user_id()),
        )
        self.jobs.set_status(existing.job_id, "new")
        return True

    def followups(self, within_days: int = 7) -> list[Application]:
        cutoff = (datetime.now(UTC) + timedelta(days=within_days)).isoformat()
        rows = self.db.query(
            "SELECT * FROM applications WHERE follow_up_at IS NOT NULL AND follow_up_at <= ? "
            "AND user_id = ? ORDER BY follow_up_at ASC LIMIT 50",
            (cutoff, current_user_id()),
        )
        return [self._to_model(row) for row in rows]

    def stats(self) -> dict:
        counts = {
            status: 0
            for status in STATUSES
        }
        for row in self.db.query(
            "SELECT status, COUNT(*) AS count FROM applications WHERE user_id = ? "
            "GROUP BY status",
            (current_user_id(),),
        ):
            counts[row["status"]] = row["count"]
        total = sum(counts.values())
        responses = counts["screening"] + counts["interview"] + counts["offer"] + counts["rejected"]
        applied = max(1, counts["applied"] + responses)
        return {
            "total": total,
            "by_status": counts,
            "active": sum(counts[s] for s in ACTIVE_STATUSES),
            "interviews": counts["interview"],
            "offers": counts["offer"],
            "response_rate": round(100 * responses / applied, 1) if total else 0.0,
        }

    def _to_model(self, row: dict) -> Application:
        job = self.jobs.get(row["job_id"])
        return Application(
            id=row["id"],
            job_id=row["job_id"],
            status=row["status"],
            applied_at=_dt(row["applied_at"]),
            follow_up_at=_dt(row["follow_up_at"]),
            notes=row["notes"],
            history=[ApplicationEvent.model_validate(item) for item in from_json(row["history"], [])],
            created_at=_dt(row["created_at"]) or utcnow(),
            updated_at=_dt(row["updated_at"]) or utcnow(),
            job=job.card() if job else None,
        )


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


_tracker: TrackerService | None = None


def get_tracker_service() -> TrackerService:
    global _tracker
    if _tracker is None:
        _tracker = TrackerService()
    return _tracker
