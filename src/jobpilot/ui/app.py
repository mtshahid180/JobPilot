"""Flask application: server-rendered pages + JSON API."""

from __future__ import annotations

import logging
import secrets
import traceback
from datetime import UTC, datetime, timedelta

from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.exceptions import HTTPException

from jobpilot.core.config import get_settings
from jobpilot.core.context import (
    LOCAL_USER_ID,
    reset_current_user_id,
    set_current_user_id,
)
from jobpilot.core.db import get_db
from jobpilot.core.models import JobQuery
from jobpilot.harness.llm import BudgetExceeded, LLMUnavailable, get_gateway
from jobpilot.harness.loop import get_agent_loop
from jobpilot.harness.permissions import ApprovalService
from jobpilot.harness.tracing import RunStore, Tracer
from jobpilot.pipelines import taxonomy
from jobpilot.services.jobs import get_jobs_service
from jobpilot.services.match import get_match_service
from jobpilot.services.memory import MemoryService
from jobpilot.services.profile import ProfileService
from jobpilot.services.resumes import ResumeError, get_resume_service
from jobpilot.services.sessions import get_session_service
from jobpilot.services.stats import dashboard_stats
from jobpilot.services.tailor import TailorService
from jobpilot.services.tools import ToolError, execute_approval, tool_catalog
from jobpilot.services.tracker import STATUSES, get_tracker_service
from jobpilot.services.users import AccountError, UserService

log = logging.getLogger("jobpilot.ui")

PUBLIC_ENDPOINTS = {"login", "login_post", "signup", "signup_post", "api_health", "static"}


def profile_needs_setup(profile) -> bool:
    """First-run state: no real name, experience or skills yet."""
    name = profile.full_name.strip()
    return (not name or name == "Your Name") and not profile.experience and not profile.skills


def _resume_payload(resume, *, include_draft: bool) -> dict | None:
    if resume is None:
        return None
    data = resume.model_dump(mode="json")
    if not include_draft:
        data.pop("extracted", None)
    return data


def _cv_downloads(cv) -> list[dict]:
    """Only advertise formats that were actually written to disk."""
    files = getattr(cv, "files", None) or {}
    return [
        {
            "format": fmt,
            "url": f"/api/tailor/cv/{cv.id}/download/{fmt}",
        }
        for fmt in ("docx", "pdf")
        if fmt in files
    ]


def create_app(test_config: dict | None = None) -> Flask:
    settings = get_settings()
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    app.config.update(
        SECRET_KEY=settings.secret_key,
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,
        SEND_FILE_MAX_AGE_DEFAULT=0,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=not settings.debug,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    if test_config:
        app.config.update(test_config)

    get_db().init()

    # --- authentication ----------------------------------------------------
    def _csrf_token() -> str:
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(24)
            session["csrf_token"] = token
        return token

    def _csrf_ok() -> bool:
        supplied = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        return bool(supplied and expected and secrets.compare_digest(supplied, expected))

    def _safe_next(value: str | None) -> str | None:
        if not value or not value.startswith("/") or value.startswith("//"):
            return None
        if "\\" in value or value.startswith("/login") or value.startswith("/signup"):
            return None
        return value

    def _login_user(user) -> None:
        session.clear()
        session["user_id"] = user.id
        session["csrf_token"] = secrets.token_urlsafe(24)
        session.permanent = True

    @app.before_request
    def load_current_user():
        g.user = None
        g.user_token = set_current_user_id(session.get("user_id") or LOCAL_USER_ID)
        user_id = session.get("user_id")
        if user_id:
            user = UserService().get(user_id)
            if user is None or not user.is_active:
                session.clear()
                reset_current_user_id(g.user_token)
                g.user_token = set_current_user_id(LOCAL_USER_ID)
            else:
                g.user = user
                set_current_user_id(user.id)

        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Authentication required"}), 401
            next_url = request.path
            if request.query_string:
                next_url += "?" + request.query_string.decode("utf-8", "ignore")
            return redirect(url_for("login", next=next_url if next_url != "/" else None))
        return None

    @app.teardown_request
    def release_current_user(_error=None):
        token = g.pop("user_token", None)
        if token is not None:
            reset_current_user_id(token)

    @app.get("/login")
    def login():
        if g.user is not None:
            return redirect(url_for("dashboard"))
        return render_template(
            "login.html",
            title="Sign in",
            next_url=_safe_next(request.args.get("next")) or "",
        )

    @app.post("/login")
    def login_post():
        if g.user is not None:
            return redirect(url_for("dashboard"))
        email = str(request.form.get("email", "")).strip()
        password = str(request.form.get("password", ""))
        next_url = _safe_next(request.form.get("next"))
        if not _csrf_ok():
            return (
                render_template(
                    "login.html",
                    title="Sign in",
                    error="Your session expired. Please try again.",
                    email=email,
                    next_url=next_url or "",
                ),
                400,
            )
        user = UserService().authenticate(email, password)
        if not user:
            return (
                render_template(
                    "login.html",
                    title="Sign in",
                    error="That email and password combination is not recognized.",
                    email=email,
                    next_url=next_url or "",
                ),
                401,
            )
        _login_user(user)
        return redirect(next_url or url_for("dashboard"))

    @app.get("/signup")
    def signup():
        if g.user is not None:
            return redirect(url_for("dashboard"))
        return render_template(
            "signup.html",
            title="Create account",
            next_url=_safe_next(request.args.get("next")) or "",
        )

    @app.post("/signup")
    def signup_post():
        if g.user is not None:
            return redirect(url_for("dashboard"))
        name = str(request.form.get("name", "")).strip()
        email = str(request.form.get("email", "")).strip()
        password = str(request.form.get("password", ""))
        confirm = str(request.form.get("confirm", ""))
        next_url = _safe_next(request.form.get("next"))
        if not _csrf_ok():
            return (
                render_template(
                    "signup.html",
                    title="Create account",
                    error="Your session expired. Please try again.",
                    name=name,
                    email=email,
                    next_url=next_url or "",
                ),
                400,
            )
        if password != confirm:
            return (
                render_template(
                    "signup.html",
                    title="Create account",
                    error="The two passwords do not match.",
                    name=name,
                    email=email,
                    next_url=next_url or "",
                ),
                400,
            )
        try:
            user = UserService().create(email, password, name)
        except AccountError as exc:
            return (
                render_template(
                    "signup.html",
                    title="Create account",
                    error=str(exc),
                    name=name,
                    email=email,
                    next_url=next_url or "",
                ),
                400,
            )
        _login_user(user)
        return redirect(next_url or url_for("setup_page"))

    @app.post("/logout")
    def logout():
        if _csrf_ok():
            session.clear()
        return redirect(url_for("login"))

    @app.post("/account/password")
    def account_password():
        if not _csrf_ok():
            return fail("Your session expired. Refresh and try again.", 400)
        try:
            UserService().update_password(
                g.user.id,
                str(request.form.get("current_password", "")),
                str(request.form.get("new_password", "")),
            )
        except AccountError as exc:
            return redirect(url_for("settings_page", password_error=str(exc)))
        return redirect(url_for("settings_page", password="updated"))

    # --- template helpers --------------------------------------------------
    @app.template_filter("nice_date")
    def nice_date(value):
        if not value:
            return "—"
        text = str(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            return parsed.strftime("%b %d, %Y")
        except ValueError:
            return text[:10]

    @app.template_filter("time_ago")
    def time_ago(value):
        if not value:
            return "—"
        text = str(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
        except ValueError:
            return text[:10]
        seconds = max(0, (datetime.now(UTC) - parsed).total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h ago"
        if seconds < 604800:
            return f"{int(seconds // 86400)}d ago"
        return parsed.strftime("%b %d")

    @app.context_processor
    def inject_globals():
        user = g.get("user")
        base = {
            "app_settings": settings.public_dict(),
            "current_user": user,
            "csrf_token": _csrf_token(),
            "auth_enabled": True,
        }
        if user is None:
            return {
                **base,
                "pending_approvals": 0,
                "budget": {
                    "spent_total": 0.0,
                    "spent_today": 0.0,
                    "max_total": settings.max_budget_usd,
                    "max_daily": settings.daily_budget_usd,
                    "remaining": settings.max_budget_usd,
                },
                "llm_enabled": settings.llm_enabled,
                "profile_name": "",
                "profile_completeness": 0,
                "profile_initials": "JP",
                "needs_onboarding": True,
                "now": datetime.now(UTC),
            }

        gateway = get_gateway()
        try:
            pending = ApprovalService().pending_count()
        except Exception:
            pending = 0
        try:
            profile = ProfileService().get()
            profile_name = profile.full_name
            profile_completeness = profile.completeness()
            profile_initials = "".join(
                part[0].upper() for part in profile.full_name.split()[:2] if part
            ) or "JP"
            needs_onboarding = profile_needs_setup(profile)
        except Exception:
            profile_name = user.display_name
            profile_completeness = 0
            profile_initials = "".join(
                part[0].upper() for part in user.display_name.split()[:2] if part
            ) or "JP"
            needs_onboarding = True
        return {
            **base,
            "pending_approvals": pending,
            "budget": gateway.budget_snapshot(),
            "llm_enabled": gateway.available,
            "profile_name": profile_name or user.display_name,
            "profile_completeness": profile_completeness,
            "profile_initials": profile_initials,
            "needs_onboarding": needs_onboarding,
            "now": datetime.now(UTC),
        }

    # --- pages -------------------------------------------------------------
    @app.get("/")
    def dashboard():
        if request.args.get("skip") != "1" and profile_needs_setup(ProfileService().get()):
            return redirect(url_for("setup_page"))
        return render_template("index.html", active="dashboard", title="Dashboard")

    @app.get("/setup")
    def setup_page():
        if not profile_needs_setup(ProfileService().get()):
            return redirect(url_for("dashboard"))
        return render_template("setup.html", active="setup", title="Set up JobPilot")

    @app.get("/jobs")
    def jobs_page():
        return render_template(
            "jobs.html",
            active="jobs",
            title="Job explorer",
            categories=taxonomy.category_options(),
        )

    @app.get("/agent")
    def agent_page():
        return render_template("agent.html", active="agent", title="Agent console")

    @app.get("/profile")
    def profile_page():
        return render_template("profile.html", active="profile", title="Profile")

    @app.get("/tracker")
    def tracker_page():
        return render_template("tracker.html", active="tracker", title="Tracker")

    @app.get("/memory")
    def memory_page():
        return render_template("memory.html", active="memory", title="Memory")

    @app.get("/drafts")
    def drafts_page():
        return render_template("drafts.html", active="drafts", title="Drafts")

    @app.get("/runs")
    def runs_page():
        return render_template("runs.html", active="runs", title="Runs & traces")

    @app.get("/settings")
    def settings_page():
        return render_template(
            "settings.html",
            active="settings",
            title="Settings",
            tools=tool_catalog(),
            servers=[
                ("jobpilot-jobs", "jobs_server", "search_jobs, get_job, refresh_jobs, add_job_from_url"),
                ("jobpilot-profile", "profile_server", "get_profile, search_experience, list_skills"),
                ("jobpilot-memory", "memory_server", "remember, recall, forget, list_memories"),
                ("jobpilot-match", "match_server", "score_job, rank_jobs, skill_gap"),
                ("jobpilot-tailor", "tailor_server", "tailor_cv, draft_cover_letter"),
                ("jobpilot-tracker", "tracker_server", "add_application, update_status, list_followups"),
            ],
        )

    # --- API: health & dashboard ------------------------------------------
    @app.get("/api/health")
    def api_health():
        user = g.get("user")
        payload = {
            "ok": True,
            "app": settings.app_name,
            "version": "0.1.0",
            "llm_enabled": settings.llm_enabled,
            # Anonymous callers only get a liveness signal; counts and the
            # database path stay private to the signed-in workspace.
            "database": get_db().health(user.id) if user else {"ok": True},
            "time": datetime.now(UTC).isoformat(),
        }
        if user:
            payload["budget"] = get_gateway().budget_snapshot()
            payload["user"] = {"id": user.id, "email": user.email}
        return jsonify(payload)

    @app.get("/api/dashboard")
    def api_dashboard():
        data = dashboard_stats()
        jobs = get_jobs_service().list(limit=5, sort="score")
        data["top_jobs"] = [
            {**job.card().model_dump(mode="json"), "rationale": job.score_breakdown}
            for job in jobs
        ]
        data["has_activity"] = bool(data["jobs"]["total"] or data["applications"]["total"])
        return jsonify(data)

    # --- API: jobs ---------------------------------------------------------
    @app.post("/api/jobs/search")
    def api_jobs_search():
        data = request.get_json(silent=True) or {}
        try:
            page = max(1, int(data.get("page", 1) or 1))
            page_size = max(0, min(int(data.get("page_size", 0) or 0), 60))
        except (TypeError, ValueError):
            return fail("page and page_size must be integers", 400)
        query = JobQuery(
            query=str(data.get("query", "")).strip(),
            location=str(data.get("location", "")).strip(),
            remote_only=bool(data.get("remote_only", False)),
            limit=max(1, min(int(data.get("limit", 24) or 24), 60)),
            page=page,
            page_size=page_size,
            category=str(data.get("category", "") or "").strip(),
            workplace_type=str(data.get("workplace_type", "") or "").strip(),
            seniority=str(data.get("seniority", "") or "").strip(),
            employment_type=str(data.get("employment_type", "") or "").strip(),
            include_related=bool(data.get("include_related", True)),
            include_unverified=bool(data.get("include_unverified", True)),
            refresh=bool(data.get("refresh", False)),
            posted_within_days=(
                int(data["posted_within_days"])
                if data.get("posted_within_days")
                else None
            ),
            sources=data.get("sources") or [],
        )
        jobs, meta = get_jobs_service().search(query)
        match_service = get_match_service()
        profile = ProfileService().get()
        cards = []
        for job in jobs:
            result = match_service.score_job(job, profile)
            cards.append(
                {
                    **job.card(score=result.score).model_dump(mode="json"),
                    "rationale": result.rationale,
                    "matched_skills": result.matched_skills,
                    "missing_skills": result.missing_skills,
                    "breakdown": result.breakdown.model_dump(),
                    "relevance_reasons": job.relevance_reasons,
                }
            )
        # Keep the unified tier ordering (exact → related → unverified) and
        # enrich each card with the profile score rather than re-sorting by it.
        return jsonify(
            {
                "ok": True,
                "jobs": cards,
                "meta": meta,
                "count": len(cards),
                "total": meta.get("total", len(cards)),
                "page": meta.get("page", page),
                "total_pages": meta.get("total_pages", 1),
            }
        )

    @app.get("/api/jobs")
    def api_jobs():
        args = request.args
        try:
            limit = max(1, min(int(args.get("limit", 60)), 200))
            offset = max(0, int(args.get("offset", 0)))
        except ValueError:
            return fail("limit and offset must be integers", 400)
        filters = {
            "search": args.get("q", ""),
            "location": args.get("location", ""),
            "remote_only": args.get("remote") == "1",
            "saved_only": args.get("saved") == "1",
            "status": args.get("status", ""),
            "min_score": float(args["min_score"]) if args.get("min_score") else None,
            "source": args.get("source", ""),
            "category": args.get("category", ""),
            "workplace_type": args.get("workplace", ""),
            "match_tier": args.get("tier", ""),
        }
        service = get_jobs_service()
        jobs = service.list(**filters, sort=args.get("sort", "score"), limit=limit, offset=offset)
        return jsonify(
            {
                "ok": True,
                "jobs": [job.model_dump(mode="json") for job in jobs],
                "count": len(jobs),
                "total": service.count(**filters),
                "offset": offset,
                "limit": limit,
            }
        )

    @app.get("/api/jobs/<job_id>")
    def api_job(job_id: str):
        job = get_jobs_service().get(job_id)
        if not job:
            return fail("Job not found", 404)
        match = get_match_service().score_job(job)
        return jsonify(
            {
                "ok": True,
                "job": job.model_dump(mode="json"),
                "match": match.model_dump(mode="json"),
                "application": (
                    get_tracker_service().by_job(job.id).model_dump(mode="json")
                    if get_tracker_service().by_job(job.id)
                    else None
                ),
            }
        )

    @app.post("/api/jobs/<job_id>/save")
    def api_job_save(job_id: str):
        data = request.get_json(silent=True) or {}
        job = get_jobs_service().get(job_id)
        if not job:
            return fail("Job not found", 404)
        saved = bool(data.get("saved", not job.saved))
        updated = get_jobs_service().set_saved(job_id, saved)
        return jsonify({"ok": True, "saved": saved, "job": updated.card().model_dump(mode="json") if updated else None})

    @app.post("/api/jobs/<job_id>/score")
    def api_job_score(job_id: str):
        try:
            result = get_match_service().score_by_id(job_id)
        except ValueError as exc:
            return fail(str(exc), 404)
        return jsonify({"ok": True, "match": result.model_dump(mode="json")})

    @app.post("/api/jobs/import")
    def api_job_import():
        data = request.get_json(silent=True) or {}
        value = str(data.get("value", "")).strip()
        if not value:
            return fail("Paste a job URL or description", 400)
        job = get_jobs_service().import_job(value)
        match = get_match_service().score_job(job)
        return jsonify(
            {
                "ok": True,
                "job": job.card(score=match.score).model_dump(mode="json"),
                "match": match.model_dump(mode="json"),
            }
        )

    # --- API: profile ------------------------------------------------------
    @app.get("/api/profile")
    def api_profile():
        profile = ProfileService().get()
        return jsonify(
            {
                "ok": True,
                "profile": profile.model_dump(mode="json"),
                "completeness": profile.completeness(),
                "skills": ProfileService().list_skills(),
            }
        )

    @app.put("/api/profile")
    def api_profile_update():
        data = request.get_json(silent=True) or {}
        try:
            profile = ProfileService().update(data)
        except Exception as exc:
            return fail(f"Invalid profile: {exc}", 400)
        return jsonify(
            {
                "ok": True,
                "profile": profile.model_dump(mode="json"),
                "completeness": profile.completeness(),
            }
        )

    @app.post("/api/profile/parse-cv")
    def api_profile_parse():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", "")).strip()
        if len(text) < 40:
            return fail("Paste more CV text (at least 40 characters)", 400)
        profile = ProfileService().parse_cv(text)
        return jsonify(
            {
                "ok": True,
                "profile": profile.model_dump(mode="json"),
                "completeness": profile.completeness(),
                "message": "CV parsed. Review every field before saving.",
            }
        )

    # --- API: resumes ------------------------------------------------------
    @app.get("/api/resumes")
    def api_resumes():
        service = get_resume_service()
        resumes = service.list()
        return jsonify(
            {
                "ok": True,
                "resumes": [_resume_payload(item, include_draft=False) for item in resumes],
                "current": (
                    _resume_payload(service.current(), include_draft=False)
                    if service.current()
                    else None
                ),
            }
        )

    @app.post("/api/resumes")
    def api_resume_upload():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return fail("Choose a PDF, DOCX, TXT or Markdown resume to upload.", 400)
        try:
            draft = get_resume_service().upload(
                upload.filename,
                upload.read(),
                upload.mimetype or "",
            )
        except ResumeError as exc:
            return fail(str(exc), 400)
        except Exception as exc:
            log.exception("resume upload failed")
            return fail(f"Upload failed: {exc}", 500)
        payload = _resume_payload(draft.resume, include_draft=True)
        payload["profile_draft"] = draft.profile_draft.model_dump(mode="json")
        payload["changed_fields"] = draft.changed_fields
        payload["warnings"] = draft.warnings
        return jsonify(
            {
                "ok": draft.resume.status != "failed",
                "resume": payload,
                "changed_fields": draft.changed_fields,
                "warnings": draft.warnings,
                "message": (
                    "Resume parsed. Review the extracted profile before applying it."
                    if draft.resume.status != "failed"
                    else draft.resume.error
                ),
            }
        )

    @app.get("/api/resumes/<resume_id>")
    def api_resume(resume_id: str):
        resume = get_resume_service().get(resume_id)
        if resume is None:
            return fail("Resume not found", 404)
        return jsonify(
            {"ok": True, "resume": _resume_payload(resume, include_draft=True)}
        )

    @app.post("/api/resumes/<resume_id>/apply")
    def api_resume_apply(resume_id: str):
        data = request.get_json(silent=True) or {}
        fields = data.get("fields")
        if fields is not None and not isinstance(fields, list):
            return fail("fields must be a list", 400)
        patch = data.get("patch") if isinstance(data.get("patch"), dict) else None
        try:
            profile = get_resume_service().apply(resume_id, fields=fields, patch=patch)
        except ResumeError as exc:
            return fail(str(exc), 400)
        return jsonify(
            {
                "ok": True,
                "profile": profile.model_dump(mode="json"),
                "completeness": profile.completeness(),
                "resume": _resume_payload(
                    get_resume_service().get(resume_id), include_draft=False
                ),
            }
        )

    @app.post("/api/resumes/<resume_id>/current")
    def api_resume_current(resume_id: str):
        try:
            resume = get_resume_service().set_current(resume_id)
        except ResumeError as exc:
            return fail(str(exc), 404)
        return jsonify(
            {"ok": True, "resume": _resume_payload(resume, include_draft=False)}
        )

    @app.delete("/api/resumes/<resume_id>")
    def api_resume_delete(resume_id: str):
        removed = get_resume_service().delete(resume_id)
        if not removed:
            return fail("Resume not found", 404)
        return jsonify({"ok": True, "deleted": resume_id})

    # --- API: tailoring ----------------------------------------------------
    @app.post("/api/tailor/cv")
    def api_tailor_cv():
        data = request.get_json(silent=True) or {}
        job_id = str(data.get("job_id", ""))
        try:
            cv = TailorService().tailor_cv(job_id)
        except ValueError as exc:
            return fail(str(exc), 400)
        except Exception as exc:
            log.exception("tailor cv failed")
            return fail(f"Tailoring failed: {exc}", 500)
        return jsonify(
            {
                "ok": True,
                "cv": cv.model_dump(mode="json"),
                "downloads": _cv_downloads(cv),
            }
        )

    @app.get("/api/tailor/cv/<cv_id>/download/<fmt>")
    def api_tailor_cv_download(cv_id: str, fmt: str):
        try:
            path, name, mimetype = TailorService().cv_file(cv_id, fmt)
        except ValueError as exc:
            return fail(str(exc), 404 if "not found" in str(exc).lower() else 400)
        except Exception as exc:
            log.exception("tailored CV download failed")
            return fail(f"Download failed: {exc}", 500)
        return send_file(
            path,
            mimetype=mimetype,
            as_attachment=True,
            download_name=name,
        )

    @app.post("/api/tailor/cover-letter")
    def api_cover_letter():
        data = request.get_json(silent=True) or {}
        job_id = str(data.get("job_id", ""))
        try:
            letter = TailorService().draft_cover_letter(job_id)
        except ValueError as exc:
            return fail(str(exc), 400)
        except Exception as exc:
            log.exception("cover letter failed")
            return fail(f"Cover letter failed: {exc}", 500)
        return jsonify({"ok": True, "cover_letter": letter.model_dump(mode="json")})

    @app.get("/api/drafts")
    def api_drafts():
        tailor = TailorService()
        return jsonify(
            {
                "ok": True,
                "cvs": [cv.model_dump(mode="json") for cv in tailor.list_cvs(30)],
                "letters": [letter.model_dump(mode="json") for letter in tailor.list_letters(30)],
            }
        )

    @app.get("/api/drafts/cv/<cv_id>")
    def api_draft_cv(cv_id: str):
        cv = TailorService().get_cv(cv_id)
        if not cv:
            return fail("CV draft not found", 404)
        return jsonify({"ok": True, "cv": cv.model_dump(mode="json")})

    @app.get("/api/drafts/letter/<letter_id>")
    def api_draft_letter(letter_id: str):
        letter = TailorService().get_letter(letter_id)
        if not letter:
            return fail("Cover letter not found", 404)
        return jsonify({"ok": True, "cover_letter": letter.model_dump(mode="json")})

    # --- API: tracker ------------------------------------------------------
    @app.get("/api/applications")
    def api_applications():
        status = request.args.get("status", "")
        tracker = get_tracker_service()
        return jsonify(
            {
                "ok": True,
                "applications": [app.model_dump(mode="json") for app in tracker.list(status=status, limit=200)],
                "stats": tracker.stats(),
                "statuses": STATUSES,
            }
        )

    @app.post("/api/applications")
    def api_application_create():
        data = request.get_json(silent=True) or {}
        try:
            application = get_tracker_service().add_application(
                str(data.get("job_id", "")),
                status=str(data.get("status", "applied")),
                notes=str(data.get("notes", "")),
            )
        except ValueError as exc:
            return fail(str(exc), 400)
        return jsonify({"ok": True, "application": application.model_dump(mode="json")})

    @app.patch("/api/applications/<application_id>")
    def api_application_update(application_id: str):
        data = request.get_json(silent=True) or {}
        try:
            application = get_tracker_service().update_status(
                application_id,
                str(data.get("status", "applied")),
                str(data.get("note", "")),
            )
        except ValueError as exc:
            return fail(str(exc), 400)
        return jsonify({"ok": True, "application": application.model_dump(mode="json")})

    @app.delete("/api/applications/<application_id>")
    def api_application_delete(application_id: str):
        removed = get_tracker_service().delete(application_id)
        return jsonify({"ok": removed})

    # --- API: memory -------------------------------------------------------
    @app.get("/api/memories")
    def api_memories():
        service = MemoryService()
        return jsonify(
            {
                "ok": True,
                "memories": [memory.model_dump(mode="json") for memory in service.list(limit=200)],
                "pending": [memory.model_dump(mode="json") for memory in service.list(status="pending")],
                "stats": service.stats(),
            }
        )

    @app.post("/api/memories")
    def api_memory_create():
        data = request.get_json(silent=True) or {}
        content = str(data.get("content", "")).strip()
        if not content:
            return fail("Memory content is required", 400)
        try:
            result = MemoryService().remember(
                content, kind=data.get("kind") or None, provenance="ui:memory"
            )
        except ValueError as exc:
            return fail(str(exc), 400)
        return jsonify(
            {
                "ok": True,
                "action": result.action,
                "message": result.message,
                "memory": result.memory.model_dump(mode="json") if result.memory else None,
            }
        )

    @app.post("/api/memories/<memory_id>/approve")
    def api_memory_approve(memory_id: str):
        memory = MemoryService().approve(memory_id)
        if not memory:
            return fail("Memory not found", 404)
        return jsonify({"ok": True, "memory": memory.model_dump(mode="json")})

    @app.delete("/api/memories/<memory_id>")
    def api_memory_delete(memory_id: str):
        removed = MemoryService().forget(memory_id)
        return jsonify({"ok": removed})

    # --- API: approvals ----------------------------------------------------
    @app.get("/api/approvals")
    def api_approvals():
        service = ApprovalService()
        return jsonify(
            {
                "ok": True,
                "approvals": [
                    approval.model_dump(mode="json")
                    for approval in service.list(status="all", limit=50)
                ],
                "pending": service.pending_count(),
            }
        )

    @app.post("/api/approvals/<approval_id>/decide")
    def api_approval_decide(approval_id: str):
        data = request.get_json(silent=True) or {}
        approve = bool(data.get("approve", False))
        service = ApprovalService()
        approval = service.get(approval_id)
        if not approval:
            return fail("Approval not found", 404)
        if approval.status != "pending":
            return fail("This approval was already decided", 409)
        decided = service.decide(approval_id, approve)
        execution = None
        if approve and decided:
            execution = execute_approval(decided)
            Tracer(decided.run_id).approval(decided.id, decided.tool, "executed")
        return jsonify(
            {
                "ok": True,
                "approval": decided.model_dump(mode="json") if decided else None,
                "execution": execution,
            }
        )

    # --- API: agent --------------------------------------------------------
    @app.post("/api/agent/run")
    def api_agent_run():
        data = request.get_json(silent=True) or {}
        message = str(data.get("message", "")).strip()
        if not message:
            return fail("Message is required", 400)
        session_id = str(data.get("session_id", "")).strip()
        mode = "local" if data.get("mode") == "local" else "auto"
        try:
            reply = get_agent_loop().run(message, session_id=session_id, mode=mode)
        except (LLMUnavailable, BudgetExceeded) as exc:
            return fail(str(exc), 429)
        return jsonify({"ok": True, "reply": reply.model_dump(mode="json")})

    @app.get("/api/sessions")
    def api_sessions():
        return jsonify({"ok": True, "sessions": get_session_service().list(30)})

    @app.post("/api/sessions")
    def api_session_create():
        data = request.get_json(silent=True) or {}
        session = get_session_service().create(str(data.get("title", "New session")))
        return jsonify({"ok": True, "session": session})

    @app.get("/api/sessions/<session_id>")
    def api_session(session_id: str):
        service = get_session_service()
        session = service.get(session_id)
        if not session:
            return fail("Session not found", 404)
        return jsonify(
            {
                "ok": True,
                "session": session,
                "messages": service.messages(session_id),
            }
        )

    @app.delete("/api/sessions/<session_id>")
    def api_session_delete(session_id: str):
        return jsonify({"ok": get_session_service().delete(session_id)})

    # --- API: runs, sources, tools ----------------------------------------
    @app.get("/api/runs")
    def api_runs():
        return jsonify({"ok": True, "runs": RunStore().list(50), "stats": RunStore().stats()})

    @app.get("/api/runs/<run_id>")
    def api_run(run_id: str):
        run = RunStore().get(run_id)
        if not run:
            return fail("Run not found", 404)
        return jsonify({"ok": True, "run": run, "trace": Tracer.load(run_id)})

    @app.get("/api/sources")
    def api_sources():
        statuses = []
        registry = get_jobs_service().registry
        for status in registry.doctor(force=False):
            data = status.model_dump(mode="json")
            data["status"] = status.status
            statuses.append(data)
        return jsonify({"ok": True, "sources": statuses})

    @app.post("/api/sources/check")
    def api_sources_check():
        registry = get_jobs_service().registry
        statuses = []
        for status in registry.doctor(force=True):
            data = status.model_dump(mode="json")
            data["status"] = status.status
            statuses.append(data)
        return jsonify({"ok": True, "sources": statuses})

    @app.get("/api/tools")
    def api_tools():
        return jsonify({"ok": True, "tools": tool_catalog()})

    # --- errors ------------------------------------------------------------
    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/"):
            return fail("Not found", 404)
        return render_template("404.html", active="", title="Not found"), 404

    @app.errorhandler(ToolError)
    def tool_error(error):
        return fail(str(error), 400)

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith("/api/"):
            return fail(error.description or "Request failed", error.code or 500)
        return render_template("error.html", active="", title="Error", error=error), error.code or 500

    @app.errorhandler(Exception)
    def unexpected(error):
        log.error("Unhandled error: %s\n%s", error, traceback.format_exc())
        if request.path.startswith("/api/"):
            return fail("Internal error. Check the server log.", 500)
        return (
            render_template("error.html", active="", title="Error", error=error),
            500,
        )

    return app


def fail(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    app.run(host=settings.host, port=settings.port, debug=settings.debug)
