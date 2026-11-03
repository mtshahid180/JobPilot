"""Job storage and the unified search pipeline.

Every caller — the job board, the conversational agent and the MCP tools — goes
through :meth:`JobsService.search`.  The pipeline is deliberately explicit:

1. interpret the request (category, role, place, workplace, seniority),
2. generate source terms and ask every enabled source in parallel,
3. normalise and deduplicate,
4. apply mandatory category and geography filters,
5. grade each surviving listing as exact, related or unverified,
6. rank, paginate, persist and report source/freshness metadata.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import UTC, datetime, timedelta

from jobpilot.core import locations as geo
from jobpilot.core.config import get_settings
from jobpilot.core.context import current_user_id
from jobpilot.core.db import Database, from_json, get_db, to_json
from jobpilot.core.models import Job, JobCard, JobQuery
from jobpilot.pipelines import taxonomy
from jobpilot.pipelines.extract import strip_html
from jobpilot.sources.manual import ManualSource
from jobpilot.sources.registry import SearchOutcome, SourceRegistry, SourceRun, get_registry

CACHE_LIMIT = 256
_CACHE: dict[str, tuple[float, list[str], dict]] = {}
_CACHE_LOCK = threading.Lock()

TIER_RANK = {"exact": 0, "related": 1, "unverified": 2}


class JobsService:
    def __init__(self, db: Database | None = None, registry: SourceRegistry | None = None) -> None:
        self.db = db or get_db()
        self.registry = registry or get_registry()
        self.settings = get_settings()

    # --- search ------------------------------------------------------------
    def search(self, query: JobQuery) -> tuple[list[Job], dict]:
        started = time.perf_counter()
        intent = taxonomy.parse_intent(
            query.query,
            query.location
            or ", ".join(part for part in (query.city, query.region, query.country) if part),
            category=query.category,
            workplace_type=query.workplace_type,
            seniority=query.seniority,
            employment_type=query.employment_type,
            remote_only=query.remote_only,
        )
        source_query = self._source_query(query, intent)
        page = max(1, int(query.page or 1))
        page_size = max(1, min(int(query.page_size or query.limit or 24), 100))
        cache_key = self._cache_key(source_query, intent)

        if self._cache_enabled() and not query.refresh:
            cached = self._cache_get(cache_key)
            if cached is not None:
                age, ids, meta = cached
                offset = (page - 1) * page_size
                page_ids = ids[offset : offset + page_size]
                jobs = self.get_many(page_ids)
                if len(jobs) == len(page_ids):
                    meta = {
                        **meta,
                        "total": len(ids),
                        "page": page,
                        "page_size": page_size,
                        "total_pages": max(1, math.ceil(len(ids) / page_size)),
                        "returned": len(jobs),
                        "cache": {"hit": True, "age_seconds": round(age, 1)},
                    }
                    meta["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
                    return jobs, meta

        outcome = self.registry.search(source_query)
        kept, counts = self._filter_jobs(outcome.jobs, intent, query)
        kept.sort(key=_rank_key)
        kept = _diversify_employers(kept)
        total = len(kept)
        total_pages = max(1, math.ceil(total / page_size))
        offset = (page - 1) * page_size
        page_jobs = kept[offset : offset + page_size]

        stored = self.upsert_many(kept)
        stored_ids = {job.id for job in stored}
        page_ids = [job.id for job in page_jobs if job.id in stored_ids]
        page_jobs = self.get_many(page_ids)

        meta = self._meta(
            query=source_query,
            intent=intent,
            outcome=outcome,
            counts=counts,
            matched=kept,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            returned=len(page_jobs),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        )

        source_ok = any(
            run.status in ("ok", "empty") for run in outcome.runs.values()
        )
        if self._cache_enabled() and source_ok:
            # Cache the full ranked ID list once per query. Pages are sliced on
            # read, so changing pages never triggers a second source fan-out
            # and the API still returns only the requested page.
            self._cache_set(cache_key, [job.id for job in kept], meta)
        return page_jobs, meta

    def _source_query(self, query: JobQuery, intent: taxonomy.SearchIntent) -> JobQuery:
        category = intent.category if intent.has_category else query.category
        terms = taxonomy.generated_source_terms(intent)
        return query.model_copy(
            update={
                "query": intent.query or query.query,
                "search_terms": terms,
                "category": category,
                "city": intent.city,
                "region": intent.region,
                "country": intent.country or query.country,
                "country_code": intent.country_code or query.country_code,
                "location": intent.location_text or query.location,
                "workplace_type": intent.workplace_type,
                "seniority": intent.seniority or query.seniority,
                "employment_type": intent.employment_type or query.employment_type,
                "remote_only": intent.remote_only or query.remote_only,
                "limit": max(query.limit, 24),
            }
        )

    def _filter_jobs(
        self, jobs: list[Job], intent: taxonomy.SearchIntent, query: JobQuery
    ) -> tuple[list[Job], dict]:
        kept: list[Job] = []
        counts = {
            "fetched": len(jobs),
            "category_rejected": 0,
            "location_rejected": 0,
            "workplace_rejected": 0,
            "unverified_dropped": 0,
            "date_filtered": 0,
            "expired": 0,
            "exact": 0,
            "related": 0,
            "unverified": 0,
            "filtered_out": 0,
        }
        cutoff = None
        if query.posted_within_days:
            cutoff = datetime.now(UTC) - timedelta(days=query.posted_within_days)

        for job in jobs:
            relevance = taxonomy.relevance_for_job(
                job, intent, include_related=query.include_related
            )
            if not relevance.accepted:
                counts["category_rejected"] += 1
                continue
            job.category = relevance.category
            job.category_label = relevance.category_label
            job.relevance = relevance.score

            info = _location_info(job)
            workplace_filter = _workplace_filter(intent, query)
            location = geo.location_match(
                intent.location_text,
                info,
                workplace_filter=workplace_filter,
            )
            if not location.accepted:
                if workplace_filter and info.workplace_type != workplace_filter:
                    counts["workplace_rejected"] += 1
                else:
                    counts["location_rejected"] += 1
                continue
            if location.tier == "unverified" and not query.include_unverified:
                counts["unverified_dropped"] += 1
                continue

            if cutoff and job.posted_at:
                posted = job.posted_at if job.posted_at.tzinfo else job.posted_at.replace(tzinfo=UTC)
                if posted < cutoff:
                    counts["date_filtered"] += 1
                    continue
            if job.availability == "expired":
                counts["expired"] += 1
                continue

            tier = _combined_tier(relevance.tier, location)
            if tier not in counts:
                counts[tier] = 0
            counts[tier] += 1

            job.match_tier = tier  # type: ignore[assignment]
            job.relevance_reasons = [*relevance.reasons, location.reason][:8]
            job.workplace_type = info.workplace_type
            job.location_confidence = info.location_confidence
            job.remote = info.workplace_type in ("remote", "hybrid")
            job.last_seen_at = datetime.now(UTC)
            if job.expires_at and job.expires_at < datetime.now(UTC):
                job.availability = "expired"
            kept.append(job)

        counts["filtered_out"] = counts["fetched"] - len(kept)
        return kept, counts

    def _meta(
        self,
        *,
        query: JobQuery,
        intent: taxonomy.SearchIntent,
        outcome: SearchOutcome,
        counts: dict,
        matched: list[Job],
        total: int,
        page: int,
        page_size: int,
        total_pages: int,
        returned: int,
        elapsed_ms: float,
    ) -> dict:
        runs = self._source_runs(outcome)
        posted = [job.posted_at for job in matched if job.posted_at]
        freshness = {
            "newest_posted": max(posted).isoformat() if posted else "",
            "oldest_posted": min(posted).isoformat() if posted else "",
            "active": sum(1 for job in matched if job.availability == "active"),
            "stale": sum(1 for job in matched if job.availability == "stale"),
            "expired": counts.get("expired", 0),
            "unknown": sum(1 for job in matched if job.availability == "unknown"),
        }
        notes = list(intent.notes)
        ok_sources = [run for run in runs if run.status == "ok"]
        failed = [run for run in runs if run.status in ("error", "timeout")]
        if failed:
            notes.append(
                f"{len(failed)} source(s) failed this run: "
                + ", ".join(f"{run.name} ({run.status})" for run in failed)
                + "."
            )
        if not matched:
            if runs and all(
                run.status in ("error", "timeout", "skipped", "disabled") for run in runs
            ):
                notes.append("Every source failed or was disabled; no listing is being presented as live.")
            else:
                notes.append("Sources responded, but no listing passed the category and location filters.")
        if intent.location_text:
            place = geo.find_location(intent.location_text)
            if place is not None and place.kind == "city":
                notes.append(
                    f"Exact-city filter: only listings in {place.city} were kept; "
                    "nearby cities, regions and countries were not substituted."
                )
            else:
                notes.append(f"Geography filter: {intent.location_text}.")
        else:
            notes.append("Geography: no location restriction was applied.")
        workplace_filter = _workplace_filter(intent, query)
        if workplace_filter:
            notes.append(
                f"{workplace_filter.capitalize()} filter applied strictly; listings whose "
                "workplace type is unknown were excluded."
            )
        if intent.remote_only:
            notes.append("Remote-only filter applied.")
        if (
            not matched
            and intent.location_text
            and not counts.get("location_rejected")
            and not counts.get("workplace_rejected")
        ):
            notes.append("No listing reached the exact-location step; results were not padded.")

        return {
            "sources_used": outcome.sources_used,
            "errors": outcome.errors,
            "source_runs": [run.as_dict() for run in runs],
            "duplicates": outcome.duplicates,
            "elapsed_ms": elapsed_ms,
            "raw_count": outcome.raw_count,
            "total": total,
            "returned": returned,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "counts": counts,
            "interpreted": intent.describe(),
            "filters": {
                "query": query.query,
                "location": intent.location_text,
                "remote_only": intent.remote_only,
                "workplace_type": intent.workplace_type,
                "category": intent.category,
                "seniority": intent.seniority,
                "employment_type": intent.employment_type,
                "posted_within_days": query.posted_within_days,
                "include_related": query.include_related,
                "include_unverified": query.include_unverified,
            },
            "freshness": freshness,
            "cache": {"hit": False, "age_seconds": 0},
            "notes": notes,
            "ok_sources": len(ok_sources),
            "failed_sources": len(failed),
        }

    def _source_runs(self, outcome: SearchOutcome) -> list[SourceRun]:
        runs = dict(outcome.runs)
        adapters = getattr(self.registry, "adapters", [])
        for adapter in adapters:
            if adapter.name in runs:
                continue
            if adapter.name == "manual":
                continue
            runs[adapter.name] = SourceRun(
                name=adapter.name,
                status="disabled" if not adapter.enabled else "skipped",
                error="" if adapter.enabled else "disabled",
            )
        order = {adapter.name: index for index, adapter in enumerate(adapters)}
        return sorted(runs.values(), key=lambda run: order.get(run.name, 999))

    # --- cache -------------------------------------------------------------
    def _cache_enabled(self) -> bool:
        return self.settings.environment.lower() not in {"test", "testing"}

    def _cache_key(
        self,
        query: JobQuery,
        intent: taxonomy.SearchIntent,
    ) -> str:
        parts = (
            current_user_id(),
            id(self.registry),
            intent.query.lower(),
            intent.category,
            intent.location_text.lower(),
            intent.workplace_type,
            intent.seniority,
            intent.employment_type,
            tuple(sorted(query.sources)),
            query.posted_within_days or 0,
            query.include_related,
            query.include_unverified,
        )
        return "|".join(str(part) for part in parts)

    def _cache_get(self, key: str) -> tuple[float, list[str], dict] | None:
        with _CACHE_LOCK:
            entry = _CACHE.get(key)
            if entry is None:
                return None
            created, ids, meta = entry
            age = time.monotonic() - created
            if age > self.settings.search_cache_ttl_seconds:
                _CACHE.pop(key, None)
                return None
            return age, list(ids), dict(meta)

    def _cache_set(self, key: str, ids: list[str], meta: dict) -> None:
        with _CACHE_LOCK:
            if len(_CACHE) >= CACHE_LIMIT:
                oldest = min(_CACHE, key=lambda item: _CACHE[item][0])
                _CACHE.pop(oldest, None)
            _CACHE[key] = (time.monotonic(), list(ids), dict(meta))

    @staticmethod
    def clear_cache() -> None:
        with _CACHE_LOCK:
            _CACHE.clear()

    # --- manual import -----------------------------------------------------
    def import_job(self, value: str) -> Job:
        value = (value or "").strip()
        if not value:
            raise ValueError("Provide a job URL or pasted description")
        if value.startswith("http://") or value.startswith("https://"):
            job = ManualSource.from_url(value)
        else:
            job = ManualSource.from_text(value)
        if not job.description and not job.title:
            raise ValueError("Could not extract a job from that input")
        return self.upsert(job)

    # --- persistence -------------------------------------------------------
    def upsert(self, job: Job) -> Job:
        with self.db.transaction() as conn:
            return self._upsert_conn(conn, job, current_user_id())

    def _upsert_conn(self, conn, job: Job, user_id: str) -> Job:
        fingerprint = _scoped_fingerprint(user_id, job.fingerprint)
        existing = conn.execute(
            "SELECT id FROM jobs WHERE user_id = ? AND fingerprint = ?",
            (user_id, fingerprint),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE jobs SET description = ?, salary = ?, skills = ?, remote = ?, "
                "seniority = ?, employment_type = ?, posted_at = ?, expires_at = ?, url = ?, "
                "company = ?, location = ?, title = ?, fetched_at = ?, category = ?, "
                "category_label = ?, workplace_type = ?, company_location = ?, job_location = ?, "
                "eligible_locations = ?, remote_scope = ?, location_confidence = ?, "
                "location_restrictions = ?, availability = ?, last_seen_at = ?, relevance = ?, "
                "match_tier = ?, relevance_reasons = ? "
                "WHERE id = ? AND user_id = ?",
                (
                    job.description,
                    job.salary,
                    to_json(job.skills),
                    int(job.remote),
                    job.seniority,
                    job.employment_type,
                    _iso(job.posted_at),
                    _iso(job.expires_at),
                    job.url,
                    job.company,
                    job.location,
                    job.title,
                    job.fetched_at.isoformat(),
                    job.category,
                    job.category_label,
                    job.workplace_type,
                    job.company_location,
                    job.job_location,
                    to_json(job.eligible_locations),
                    job.remote_scope,
                    job.location_confidence,
                    to_json(job.location_restrictions),
                    job.availability,
                    _iso(job.last_seen_at) or job.fetched_at.isoformat(),
                    job.relevance,
                    job.match_tier,
                    to_json(job.relevance_reasons),
                    existing["id"],
                    user_id,
                ),
            )
            job.id = existing["id"]
            return job

        conn.execute(
            "INSERT OR IGNORE INTO jobs (id, user_id, source, external_id, title, company, "
            "location, description, url, salary, employment_type, remote, seniority, skills, "
            "posted_at, fetched_at, fingerprint, saved, status, score, score_breakdown, raw, "
            "category, category_label, workplace_type, company_location, job_location, "
            "eligible_locations, remote_scope, location_confidence, location_restrictions, "
            "expires_at, availability, last_seen_at, relevance, match_tier, relevance_reasons) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job.id,
                user_id,
                job.source,
                job.external_id,
                job.title,
                job.company,
                job.location,
                job.description,
                job.url,
                job.salary,
                job.employment_type,
                int(job.remote),
                job.seniority,
                to_json(job.skills),
                _iso(job.posted_at),
                job.fetched_at.isoformat(),
                fingerprint,
                int(job.saved),
                job.status,
                job.score,
                to_json(job.score_breakdown),
                to_json(job.raw),
                job.category,
                job.category_label,
                job.workplace_type,
                job.company_location,
                job.job_location,
                to_json(job.eligible_locations),
                job.remote_scope,
                job.location_confidence,
                to_json(job.location_restrictions),
                _iso(job.expires_at),
                job.availability,
                _iso(job.last_seen_at) or job.fetched_at.isoformat(),
                job.relevance,
                job.match_tier,
                to_json(job.relevance_reasons),
            ),
        )
        row = conn.execute(
            "SELECT id FROM jobs WHERE user_id = ? AND fingerprint = ?",
            (user_id, fingerprint),
        ).fetchone()
        if row:
            job.id = row["id"]
        return job

    def upsert_many(self, jobs: list[Job]) -> list[Job]:
        stored: list[Job] = []
        seen: set[str] = set()
        user_id = current_user_id()
        with self.db.transaction() as conn:
            for job in jobs:
                if job.fingerprint in seen:
                    continue
                seen.add(job.fingerprint)
                stored.append(self._upsert_conn(conn, job, user_id))
        return stored

    # --- reads -------------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        row = self.db.query_one(
            "SELECT * FROM jobs WHERE id = ? AND user_id = ?",
            (job_id, current_user_id()),
        )
        return self._to_job(row) if row else None

    def get_many(self, job_ids: list[str]) -> list[Job]:
        if not job_ids:
            return []
        placeholders = ",".join("?" for _ in job_ids)
        rows = self.db.query(
            f"SELECT * FROM jobs WHERE user_id = ? AND id IN ({placeholders})",
            (current_user_id(), *job_ids),
        )
        by_id = {row["id"]: self._to_job(row) for row in rows}
        return [by_id[job_id] for job_id in job_ids if job_id in by_id]

    def _where_clause(
        self,
        *,
        search: str = "",
        location: str = "",
        remote_only: bool = False,
        saved_only: bool = False,
        status: str = "",
        min_score: float | None = None,
        source: str = "",
        category: str = "",
        workplace_type: str = "",
        match_tier: str = "",
    ) -> tuple[str, list]:
        clauses: list[str] = ["user_id = ?"]
        params: list = [current_user_id()]
        if search:
            clauses.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
            needle = f"%{search}%"
            params.extend([needle, needle, needle])
        if location:
            clauses.append(
                "(location LIKE ? OR job_location LIKE ? OR eligible_locations LIKE ?)"
            )
            needle = f"%{location}%"
            params.extend([needle, needle, needle])
        if remote_only:
            clauses.append("COALESCE(workplace_type, 'unknown') = 'remote'")
        if saved_only:
            clauses.append("saved = 1")
        if status:
            clauses.append("status = ?")
            params.append(status)
        if min_score is not None:
            clauses.append("COALESCE(score, 0) >= ?")
            params.append(min_score)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if workplace_type:
            normalized = "onsite" if workplace_type in ("on-site", "on site", "office") else workplace_type
            clauses.append("COALESCE(workplace_type, 'unknown') = ?")
            params.append(normalized)
        if match_tier:
            clauses.append("match_tier = ?")
            params.append(match_tier)
        return f"WHERE {' AND '.join(clauses)}", params

    def list(
        self,
        *,
        search: str = "",
        location: str = "",
        remote_only: bool = False,
        saved_only: bool = False,
        status: str = "",
        min_score: float | None = None,
        source: str = "",
        category: str = "",
        workplace_type: str = "",
        match_tier: str = "",
        sort: str = "score",
        limit: int = 100,
        offset: int = 0,
    ) -> list[Job]:
        where, params = self._where_clause(
            search=search,
            location=location,
            remote_only=remote_only,
            saved_only=saved_only,
            status=status,
            min_score=min_score,
            source=source,
            category=category,
            workplace_type=workplace_type,
            match_tier=match_tier,
        )
        order = {
            "recent": "COALESCE(posted_at, fetched_at) DESC",
            "company": "company COLLATE NOCASE ASC",
            "title": "title COLLATE NOCASE ASC",
            "score": "COALESCE(score, 0) DESC, relevance DESC, COALESCE(posted_at, fetched_at) DESC",
            "relevance": "relevance DESC, COALESCE(score, 0) DESC",
        }.get(sort, "COALESCE(score, 0) DESC, relevance DESC")
        rows = self.db.query(
            f"SELECT * FROM jobs {where} ORDER BY {order} LIMIT ? OFFSET ?",
            (*params, max(1, min(limit, 500)), max(0, offset)),
        )
        return [self._to_job(row) for row in rows]

    def cards(self, limit: int = 50, **filters) -> list[JobCard]:
        return [job.card() for job in self.list(limit=limit, **filters)]

    def set_saved(self, job_id: str, saved: bool) -> Job | None:
        self.db.execute(
            "UPDATE jobs SET saved = ? WHERE id = ? AND user_id = ?",
            (int(saved), job_id, current_user_id()),
        )
        return self.get(job_id)

    def set_status(self, job_id: str, status: str) -> Job | None:
        self.db.execute(
            "UPDATE jobs SET status = ? WHERE id = ? AND user_id = ?",
            (status, job_id, current_user_id()),
        )
        return self.get(job_id)

    def update_score(self, job_id: str, score: float, breakdown: dict) -> None:
        self.db.execute(
            "UPDATE jobs SET score = ?, score_breakdown = ? WHERE id = ? AND user_id = ?",
            (score, to_json(breakdown), job_id, current_user_id()),
        )

    def count(self, **filters) -> int:
        where, params = self._where_clause(**filters)
        return int(
            self.db.scalar(f"SELECT COUNT(*) FROM jobs {where}", tuple(params), default=0)
        )

    def stats(self) -> dict:
        user_id = current_user_id()
        return {
            "total": int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM jobs WHERE user_id = ?", (user_id,), default=0
                )
            ),
            "saved": int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM jobs WHERE saved = 1 AND user_id = ?",
                    (user_id,),
                    default=0,
                )
            ),
            "remote": int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM jobs WHERE remote = 1 AND saved = 1 AND user_id = ?",
                    (user_id,),
                    default=0,
                )
            ),
            "scored": int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM jobs WHERE score IS NOT NULL AND user_id = ?",
                    (user_id,),
                    default=0,
                )
            ),
            "sources": [
                row
                for row in self.db.query(
                    "SELECT source, COUNT(*) AS count FROM jobs WHERE user_id = ? "
                    "GROUP BY source ORDER BY count DESC",
                    (user_id,),
                )
            ],
        }

    def top_skills(self, limit: int = 14) -> list[dict]:
        rows = self.db.query(
            "SELECT skills FROM jobs WHERE user_id = ? ORDER BY fetched_at DESC LIMIT 500",
            (current_user_id(),),
        )
        counts: dict[str, int] = {}
        for row in rows:
            for skill in from_json(row["skills"], []):
                counts[skill] = counts.get(skill, 0) + 1
        ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
        return [{"skill": skill, "count": count} for skill, count in ordered]

    def all_fingerprints(self) -> set[str]:
        user_id = current_user_id()
        return {
            _raw_fingerprint(row["fingerprint"])
            for row in self.db.query(
                "SELECT fingerprint FROM jobs WHERE user_id = ?", (user_id,)
            )
        }

    def _to_job(self, row: dict) -> Job:
        description = row["description"] or ""
        # Older rows may still hold HTML-escaped markup from before extraction
        # sanitized it; clean on read so the drawer never renders tags.
        if "<" in description or "&lt;" in description:
            description = strip_html(description)
        return Job(
            id=row["id"],
            source=row["source"],
            external_id=row["external_id"],
            title=row["title"],
            company=row["company"],
            location=row["location"],
            description=description,
            url=row["url"],
            salary=row["salary"],
            employment_type=row["employment_type"],
            remote=bool(row["remote"]),
            seniority=row["seniority"],
            skills=from_json(row["skills"], []),
            posted_at=_parse_dt(row["posted_at"]),
            fetched_at=_parse_dt(row["fetched_at"]) or datetime.now().astimezone(),
            fingerprint=_raw_fingerprint(row["fingerprint"]),
            saved=bool(row["saved"]),
            status=row["status"],
            score=row["score"],
            score_breakdown=from_json(row["score_breakdown"], {}),
            raw=from_json(row["raw"], {}),
            category=_row(row, "category", "other") or "other",
            category_label=_row(row, "category_label", "") or "",
            workplace_type=_row(row, "workplace_type", "unknown") or "unknown",
            company_location=_row(row, "company_location", "") or "",
            job_location=_row(row, "job_location", "") or "",
            eligible_locations=from_json(_row(row, "eligible_locations", "[]"), []),
            remote_scope=_row(row, "remote_scope", "unknown") or "unknown",
            location_confidence=_row(row, "location_confidence", "unknown") or "unknown",
            location_restrictions=from_json(_row(row, "location_restrictions", "[]"), []),
            expires_at=_parse_dt(_row(row, "expires_at")),
            availability=_row(row, "availability", "unknown") or "unknown",
            last_seen_at=_parse_dt(_row(row, "last_seen_at")),
            relevance=float(_row(row, "relevance", 0) or 0),
            match_tier=_row(row, "match_tier", "exact") or "exact",
            relevance_reasons=from_json(_row(row, "relevance_reasons", "[]"), []),
        )


def _combined_tier(relevance_tier: str, location) -> str:
    if location.tier == "unverified":
        return "unverified"
    if relevance_tier == "related" or location.tier == "regional":
        return "related"
    if relevance_tier == "exact" and location.tier in ("exact", "remote", "any"):
        return "exact"
    return "related"


def _rank_key(job: Job):
    posted = job.posted_at.timestamp() if job.posted_at else 0
    return (
        TIER_RANK.get(job.match_tier, 3),
        -job.relevance,
        -posted,
        job.company.lower(),
    )


def _diversify_employers(jobs: list[Job]) -> list[Job]:
    """Stop one employer filling a page when other matches exist.

    Ranking already orders by tier/relevance/freshness.  This pass only breaks
    runs of the same employer: at most two consecutive roles from one company
    before the next-best different employer is surfaced.  It never adds jobs.
    """
    if len(jobs) < 3:
        return jobs
    if len({job.company.lower() for job in jobs}) < 2:
        return jobs
    remaining = list(jobs)
    ordered: list[Job] = []
    run_company = ""
    run_length = 0
    while remaining:
        pick = remaining[0]
        for candidate in remaining:
            company = candidate.company.lower()
            if company == run_company and run_length >= 2:
                continue
            pick = candidate
            break
        remaining.remove(pick)
        company = pick.company.lower()
        if company == run_company:
            run_length += 1
        else:
            run_company = company
            run_length = 1
        ordered.append(pick)
    return ordered


def _workplace_filter(intent: taxonomy.SearchIntent, query: JobQuery) -> str:
    """Resolve the one strict workplace filter shared by every caller."""
    if query.remote_only or intent.remote_only:
        return "remote"
    value = (intent.workplace_type or query.workplace_type or "").strip().lower()
    if value in ("on-site", "on site", "office"):
        return "onsite"
    return value if value in ("remote", "hybrid", "onsite") else ""


def _location_info(job: Job) -> geo.LocationInfo:
    workplace = job.workplace_type
    if workplace == "unknown" and job.remote:
        workplace = "remote"
    if workplace not in ("remote", "hybrid", "onsite"):
        # Only the title and dedicated location field are evidence; description
        # prose is intentionally not consulted here.
        workplace = geo.detect_workplace_type(job.title, job.job_location or job.location)
    return geo.LocationInfo(
        workplace_type=workplace,  # type: ignore[arg-type]
        company_location=job.company_location,
        job_location=job.job_location or job.location,
        eligible_locations=list(job.eligible_locations),
        remote_scope=job.remote_scope,  # type: ignore[arg-type]
        location_confidence=job.location_confidence,  # type: ignore[arg-type]
        restrictions=list(job.location_restrictions),
        remote=workplace in ("remote", "hybrid"),
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _row(row: dict, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _scoped_fingerprint(user_id: str, fingerprint: str) -> str:
    """Namespace fingerprints so old databases keep their global unique index."""
    return f"{user_id}:{fingerprint or ''}"


def _raw_fingerprint(fingerprint: str | None) -> str:
    value = fingerprint or ""
    return value.split(":", 1)[1] if ":" in value else value


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


_jobs_service: JobsService | None = None


def get_jobs_service() -> JobsService:
    global _jobs_service
    if _jobs_service is None:
        _jobs_service = JobsService()
    return _jobs_service
