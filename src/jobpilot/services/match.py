"""Job–profile matching: embedding similarity plus an explicit rubric."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from jobpilot.core.db import Database, get_db
from jobpilot.core.models import (
    Job,
    MatchResult,
    Profile,
    ScoreBreakdown,
    SkillGap,
)
from jobpilot.pipelines.embedding import cosine, embed_text
from jobpilot.pipelines.extract import extract_skills
from jobpilot.services.jobs import JobsService, get_jobs_service
from jobpilot.services.profile import ProfileService

SENIORITY_LEVEL = {
    "intern": 1,
    "junior": 2,
    "mid": 3,
    "senior": 4,
    "lead": 5,
    "manager": 6,
    "unspecified": 3,
}

REMOTE_PREFERENCE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "any": "any"}


@dataclass
class RankedJob:
    job: Job
    match: MatchResult

    def to_dict(self) -> dict:
        return {
            "job": self.job.model_dump(mode="json"),
            "match": self.match.model_dump(mode="json"),
        }


class MatchService:
    def __init__(
        self,
        db: Database | None = None,
        jobs: JobsService | None = None,
        profiles: ProfileService | None = None,
    ) -> None:
        self.db = db or get_db()
        self.jobs = jobs or get_jobs_service()
        self.profiles = profiles or ProfileService(self.db)

    # --- public ------------------------------------------------------------
    def score_job(self, job: Job, profile: Profile | None = None, persist: bool = True) -> MatchResult:
        profile = profile or self.profiles.get()
        breakdown, matched, missing, rationale = self._rubric(job, profile)
        score = breakdown.total()
        result = MatchResult(
            job_id=job.id,
            score=score,
            breakdown=breakdown,
            matched_skills=matched,
            missing_skills=missing,
            rationale=rationale,
            method="heuristic+embedding",
        )
        if persist:
            job.score = score
            job.score_breakdown = breakdown.model_dump()
            self.jobs.update_score(job.id, score, breakdown.model_dump())
        return result

    def score_by_id(self, job_id: str) -> MatchResult:
        job = self.jobs.get(job_id)
        if not job:
            raise ValueError(f"job not found: {job_id}")
        return self.score_job(job)

    def rank(self, limit: int = 20, persist: bool = True) -> list[RankedJob]:
        profile = self.profiles.get()
        jobs = self.jobs.list(limit=300, sort="recent")
        ranked = [RankedJob(job=job, match=self.score_job(job, profile, persist=persist)) for job in jobs]
        ranked.sort(key=lambda item: item.match.score, reverse=True)
        return ranked[: max(1, limit)]

    def skill_gap(self, job_id: str) -> SkillGap:
        job = self.jobs.get(job_id)
        if not job:
            raise ValueError(f"job not found: {job_id}")
        profile = self.profiles.get()
        have, missing = self._skill_diff(job, profile)
        coverage = len(have) / max(1, len(have) + len(missing))
        suggestions = []
        for skill in missing[:3]:
            suggestions.append(
                f"Add concrete evidence of {skill}: a small project, course or bullet with a measurable result."
            )
        if not suggestions:
            suggestions.append("No skill gaps detected for this posting — focus on impact metrics.")
        return SkillGap(
            job_id=job_id,
            have=have,
            missing=missing,
            coverage=round(coverage, 3),
            suggestions=suggestions,
        )

    # --- internals ---------------------------------------------------------
    def _rubric(
        self, job: Job, profile: Profile
    ) -> tuple[ScoreBreakdown, list[str], list[str], str]:
        have, missing = self._skill_diff(job, profile)
        if job.skills:
            skills = round(len(have) / len(job.skills), 3)
        else:
            inferred = extract_skills(job.description)
            overlap = len(set(inferred) & set(self._profile_skill_set(profile)))
            skills = round(min(1.0, overlap / max(1, len(inferred))) + 0.2, 3)

        role = self._role_fit(job, profile)
        location = self._location_fit(job, profile)
        seniority = self._seniority_fit(job, profile)
        recency = self._recency_fit(job)
        semantic = round((cosine(embed_text(self._profile_text(profile)), embed_text(self._job_text(job))) + 1) / 2, 3)

        breakdown = ScoreBreakdown(
            skills=min(1.0, skills),
            role=role,
            location=location,
            seniority=seniority,
            recency=recency,
            semantic=semantic,
        )
        rationale = self._rationale(job, profile, breakdown, have, missing)
        return breakdown, have, missing, rationale

    def _profile_skill_set(self, profile: Profile) -> set[str]:
        skills = set(profile.skills) | set(profile.experience_skills)
        for bullet in profile.all_bullets:
            skills.update(extract_skills(bullet))
        return skills

    def _skill_diff(self, job: Job, profile: Profile) -> tuple[list[str], list[str]]:
        candidate = self._profile_skill_set(profile)
        job_skills = job.skills or extract_skills(job.description)[:12]
        have = [skill for skill in job_skills if skill in candidate]
        missing = [skill for skill in job_skills if skill not in candidate]
        return have, missing

    def _role_fit(self, job: Job, profile: Profile) -> float:
        title_tokens = set(re.findall(r"[a-z]+", job.title.lower()))
        best = 0.0
        for target in profile.target_roles:
            target_tokens = set(re.findall(r"[a-z]+", target.lower()))
            if not target_tokens:
                continue
            if target.lower() in job.title.lower() or job.title.lower() in target.lower():
                best = max(best, 0.96)
                continue
            overlap = len(title_tokens & target_tokens) / len(target_tokens)
            best = max(best, overlap)
        headline_sim = (cosine(embed_text(profile.headline), embed_text(job.title)) + 1) / 2
        return round(min(1.0, max(best, headline_sim * 0.9)), 3)

    def _location_fit(self, job: Job, profile: Profile) -> float:
        pref = REMOTE_PREFERENCE.get(profile.remote_preference, "any")
        job_location = (job.location or "").lower()
        if pref == "remote":
            return 1.0 if job.remote else (0.25 if job_location else 0.4)
        if pref == "onsite":
            if job.remote:
                return 0.25
            return 1.0 if self._city_match(job.location, profile) else 0.45
        if pref == "hybrid":
            return 0.9 if job.remote or "hybrid" in job_location else 0.5
        if job.remote:
            return 0.85
        return 1.0 if self._city_match(job.location, profile) else 0.55

    def _city_match(self, job_location: str, profile: Profile) -> bool:
        locations = [profile.location, *profile.preferred_locations]
        for location in locations:
            city = location.split(",")[0].strip().lower()
            if city and city in (job_location or "").lower():
                return True
        return False

    def _seniority_fit(self, job: Job, profile: Profile) -> float:
        years = self._years_experience(profile)
        if years < 1.5:
            candidate_level = 2
        elif years < 3.5:
            candidate_level = 3
        elif years < 6:
            candidate_level = 4
        else:
            candidate_level = 5
        job_level = SENIORITY_LEVEL.get(job.seniority, 3)
        gap = abs(job_level - candidate_level)
        return round(max(0.0, 1 - gap / 3.5), 3)

    @staticmethod
    def _years_experience(profile: Profile) -> float:
        match = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*years?", profile.summary, re.I)
        stated = float(match.group(1)) if match else 0.0
        years: list[int] = []
        now = datetime.now(UTC).year
        for exp in profile.experience:
            start = re.search(r"(19|20)\d{2}", exp.start)
            if not start:
                continue
            start_year = int(start.group(0))
            end_match = re.search(r"(19|20)\d{2}", exp.end)
            end_year = int(end_match.group(0)) if end_match else now
            years.append(max(0, end_year - start_year))
        return max(stated, sum(years) if years else 0.0)

    @staticmethod
    def _recency_fit(job: Job) -> float:
        if not job.posted_at:
            return 0.55
        posted = job.posted_at if job.posted_at.tzinfo else job.posted_at.replace(tzinfo=UTC)
        days = max(0.0, (datetime.now(UTC) - posted).total_seconds() / 86400)
        if days <= 2:
            return 1.0
        if days <= 7:
            return 0.9
        if days <= 30:
            return 0.7
        if days <= 90:
            return 0.45
        return 0.25

    @staticmethod
    def _profile_text(profile: Profile) -> str:
        parts = [profile.headline, profile.summary, " ".join(profile.skills)]
        parts.extend(profile.all_bullets[:10])
        parts.extend(profile.target_roles)
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _job_text(job: Job) -> str:
        return f"{job.title}\n{job.company}\n{' '.join(job.skills)}\n{job.description[:2500]}"

    def _rationale(
        self,
        job: Job,
        profile: Profile,
        breakdown: ScoreBreakdown,
        have: list[str],
        missing: list[str],
    ) -> str:
        parts: list[str] = []
        job_skills = job.skills or extract_skills(job.description)[:12]
        if job_skills:
            parts.append(f"Skills: {len(have)}/{len(job_skills)} matched")
            if missing:
                parts.append(f"gap: {', '.join(missing[:3])}")
        if breakdown.role >= 0.7:
            parts.append("role aligns with your targets")
        elif breakdown.role < 0.35:
            parts.append("role is outside your target titles")
        if breakdown.location >= 0.85:
            parts.append("location/remote fit is strong")
        elif breakdown.location <= 0.35:
            parts.append("location fit is weak")
        if breakdown.seniority < 0.5:
            parts.append(f"seniority looks like a stretch ({job.seniority})")
        if breakdown.recency >= 0.9:
            parts.append("posted this week")
        return "; ".join(parts).capitalize() + "." if parts else "Baseline match."


_match_service: MatchService | None = None


def get_match_service() -> MatchService:
    global _match_service
    if _match_service is None:
        _match_service = MatchService()
    return _match_service
