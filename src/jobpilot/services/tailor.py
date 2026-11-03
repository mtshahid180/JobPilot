"""CV and cover-letter tailoring, constrained to verified profile facts."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from jobpilot.core.config import get_settings
from jobpilot.core.context import current_user_id
from jobpilot.core.db import Database, from_json, get_db, to_json
from jobpilot.core.models import (
    ClaimCheck,
    CoverLetter,
    Job,
    Profile,
    TailoredBullet,
    TailoredCV,
)
from jobpilot.harness.guardrails import (
    profile_evidence,
    verify_claim,
    wrap_untrusted,
)
from jobpilot.harness.llm import Gateway, get_gateway
from jobpilot.pipelines.documents import (
    build_docx,
    build_pdf,
    validate_docx,
    validate_pdf,
)
from jobpilot.pipelines.embedding import cosine, embed_text
from jobpilot.pipelines.extract import extract_skills
from jobpilot.services.jobs import JobsService, get_jobs_service
from jobpilot.services.profile import ProfileService

log = logging.getLogger("jobpilot.tailor")

MAX_BULLETS = 6
FILE_FORMATS = {
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "pdf": ("application/pdf", ".pdf"),
}


class TailorService:
    def __init__(
        self,
        db: Database | None = None,
        jobs: JobsService | None = None,
        profiles: ProfileService | None = None,
        gateway: Gateway | None = None,
    ) -> None:
        self.db = db or get_db()
        self.jobs = jobs or get_jobs_service()
        self.profiles = profiles or ProfileService(self.db)
        self.gateway = gateway or get_gateway()

    # --- CV ----------------------------------------------------------------
    def tailor_cv(self, job_id: str, use_llm: bool | None = None) -> TailoredCV:
        job = self._job(job_id)
        profile = self.profiles.get()
        self._ensure_profile(profile)

        method = "template"
        llm = self.gateway.available if use_llm is None else (use_llm and self.gateway.available)
        if llm:
            try:
                cv = self._llm_cv(job, profile)
                if cv:
                    method = f"llm:{self.gateway.settings.jobs_strong_model}"
                    cv.method = method
                    return self._finalise_cv(cv, job, profile)
            except Exception:  # noqa: BLE001 - fall back to the deterministic path
                log.warning("LLM tailoring failed; using the template CV", exc_info=True)

        cv = self._local_cv(job, profile)
        cv.method = method
        return self._finalise_cv(cv, job, profile)

    def _local_cv(self, job: Job, profile: Profile) -> TailoredCV:
        selected = self._select_bullets(job, profile)
        bullets = [
            TailoredBullet(
                text=bullet["text"],
                evidence=(
                    f"{bullet['title']} @ {bullet['company']}"
                    if bullet["company"]
                    else bullet["title"]
                ),
                source_bullet=bullet["text"],
                supported=True,
            )
            for bullet in selected
        ]
        checks: list[ClaimCheck] = [
            verify_claim(bullet.text, profile, extras=bullet.source_bullet) for bullet in bullets
        ]
        for bullet, check in zip(bullets, checks):
            bullet.supported = check.supported

        summary = (profile.summary or self._fallback_summary(profile)).strip()
        summary_check = verify_claim(summary, profile) if summary else None
        if summary_check and not summary_check.supported:
            summary = self._fallback_summary(profile)
            summary_check = verify_claim(summary, profile) if summary else None
        headline = profile.headline or (profile.target_roles[0] if profile.target_roles else "Candidate")
        skills = self._matched_skills(job, profile)

        checks_out = [check for check in [summary_check, *checks] if check]
        unsupported = sum(1 for check in checks_out if not check.supported)
        return TailoredCV(
            job_id=job.id,
            headline=headline,
            summary=summary,
            skills=skills,
            bullets=[bullet for bullet in bullets if bullet.supported],
            checks=checks_out,
            unsupported_count=unsupported,
            verified=unsupported == 0,
            job=job.card(),
        )

    def _llm_cv(self, job: Job, profile: Profile) -> TailoredCV | None:
        evidence = profile_evidence(profile)
        allowed_bullets = "\n".join(f"- {bullet}" for bullet in profile.all_bullets)
        messages = [
            {
                "role": "system",
                "content": (
                    "You tailor CVs without inventing anything. Rewrite only the facts given. "
                    "Every bullet must preserve its source bullet's facts and metrics. "
                    "Return strict JSON with keys: headline (string), summary (string), "
                    "bullets (array of {text, source}). 'source' must be copied verbatim "
                    "from the allowed profile bullets. Never add skills, employers, dates, "
                    "metrics or claims that are not in the profile."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"PROFILE FACTS:\n{evidence}\n\n"
                    f"ALLOWED SOURCE BULLETS:\n{allowed_bullets}\n\n"
                    f"TARGET JOB:\n{wrap_untrusted(self._job_brief(job), 'TARGET JOB')}\n\n"
                    "Select at most 6 of the strongest source bullets for this job and "
                    "rewrite each one concisely. Keep every number and technology name."
                ),
            },
        ]
        data, _ = self.gateway.json_complete(
            messages,
            model=self.gateway.settings.jobs_strong_model,
            max_tokens=900,
            temperature=0.2,
            cache=True,
            kind="tailor_cv",
        )
        if not data:
            return None

        allowed = set(profile.all_bullets)
        bullets: list[TailoredBullet] = []
        checks: list[ClaimCheck] = []
        for item in (data.get("bullets") or [])[:MAX_BULLETS]:
            if isinstance(item, str):
                item = {"text": item, "source": ""}
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            source = str(item.get("source", "")).strip()
            if not text:
                continue
            if source not in allowed:
                checks.append(
                    ClaimCheck(
                        claim=text,
                        supported=False,
                        reason="rewrite does not map to a verified profile bullet",
                        score=0.0,
                    )
                )
                continue
            check = verify_claim(text, profile, extras=source)
            checks.append(check)
            if check.supported:
                bullets.append(
                    TailoredBullet(text=text, source_bullet=source, evidence=source, supported=True)
                )

        if len(bullets) < 2:
            return None

        summary = str(data.get("summary") or profile.summary).strip()
        summary_check = verify_claim(summary, profile)
        if not summary_check.supported:
            summary = profile.summary
            summary_check = verify_claim(summary, profile) if summary else None

        headline = str(data.get("headline") or profile.headline).strip()
        if not self._headline_safe(headline, profile):
            headline = profile.headline or "Candidate"

        checks_out = [check for check in [summary_check, *checks] if check]
        unsupported = sum(1 for check in checks_out if not check.supported)
        return TailoredCV(
            job_id=job.id,
            headline=headline[:160],
            summary=summary,
            skills=self._matched_skills(job, profile),
            bullets=bullets,
            checks=checks_out,
            unsupported_count=unsupported,
            verified=unsupported == 0,
            job=job.card(),
            method="llm",
        )

    # --- cover letter ------------------------------------------------------
    def draft_cover_letter(self, job_id: str) -> CoverLetter:
        job = self._job(job_id)
        profile = self.profiles.get()
        self._ensure_profile(profile)

        name = profile.full_name or "Candidate"
        target = job.title
        company = job.company or "your team"
        matched = self._matched_skills(job, profile)
        top_skills = matched[:4] or profile.skills[:4]

        experiences = [exp for exp in profile.experience if exp.bullets]
        primary = experiences[0] if experiences else None
        achievements: list[str] = []
        if primary:
            for bullet in self._rank_bullets(job, primary.bullets)[:2]:
                achievements.append(_as_sentence(bullet))
        achievement_text = " ".join(achievements).strip()

        intro = f"I am applying for the {target} role at {company}."
        summary_sentence = _first_sentence(profile.summary)
        if summary_sentence:
            intro += f" {summary_sentence}"
        elif profile.headline:
            intro += f" My background is in {profile.headline.split('|')[0].strip()}."

        if primary and achievement_text:
            para2 = (
                f"At {primary.company}, as {primary.title}, I "
                f"{_lower_first(achievements[0])}"
            )
            if len(achievements) > 1:
                para2 += f" {achievements[1]}"
            if top_skills:
                para2 += (
                    " I have also worked with "
                    + _join_list(top_skills)
                    + "."
                )
        else:
            para2 = (
                "I have worked with "
                + _join_list(top_skills)
                + "."
            )

        closing = (
            f"I would welcome the chance to bring this experience to {company}. "
            "Thank you for your time and consideration.\n\n"
            f"Sincerely,\n{name}"
        )
        contact = " | ".join(
            part for part in [profile.email, profile.phone, profile.location] if part
        )
        if contact:
            closing += f"\n{contact}"

        body = f"Dear Hiring Team,\n\n{intro}\n\n{para2}\n\n{closing}"
        checks = self._verify_letter(f"{intro}\n\n{para2}", profile, job)
        unsupported = sum(1 for check in checks if not check.supported)
        letter = CoverLetter(
            job_id=job.id,
            body=body,
            checks=checks,
            unsupported_count=unsupported,
            verified=unsupported == 0,
            job=job.card(),
            method="template",
        )
        self._save_letter(letter)
        return letter

    def _verify_letter(self, body: str, profile: Profile, job: Job) -> list[ClaimCheck]:
        extras = f"{job.title} {job.company} {job.location}"
        checks: list[ClaimCheck] = []
        for sentence in re.split(r"(?<=[.!?])\s+", body.replace("\n", " ")):
            sentence = sentence.strip()
            if len(sentence) < 35 or not _substantive(sentence):
                continue
            checks.append(verify_claim(sentence, profile, extras=extras))
        return checks

    # --- drafts ------------------------------------------------------------
    def list_cvs(self, limit: int = 20) -> list[TailoredCV]:
        rows = self.db.query(
            "SELECT * FROM tailored_cvs WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (current_user_id(), limit),
        )
        return [self._row_to_cv(row) for row in rows]

    def get_cv(self, cv_id: str) -> TailoredCV | None:
        row = self.db.query_one(
            "SELECT * FROM tailored_cvs WHERE id = ? AND user_id = ?",
            (cv_id, current_user_id()),
        )
        return self._row_to_cv(row) if row else None

    def list_letters(self, limit: int = 20) -> list[CoverLetter]:
        rows = self.db.query(
            "SELECT * FROM cover_letters WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (current_user_id(), limit),
        )
        return [self._row_to_letter(row) for row in rows]

    def get_letter(self, letter_id: str) -> CoverLetter | None:
        row = self.db.query_one(
            "SELECT * FROM cover_letters WHERE id = ? AND user_id = ?",
            (letter_id, current_user_id()),
        )
        return self._row_to_letter(row) if row else None

    # --- helpers -----------------------------------------------------------
    def _job(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if not job:
            raise ValueError(f"job not found: {job_id}")
        return job

    @staticmethod
    def _ensure_profile(profile: Profile) -> None:
        if not profile.all_bullets and not profile.summary:
            raise ValueError(
                "Add at least a summary or one experience bullet before tailoring."
            )

    def _select_bullets(self, job: Job, profile: Profile, limit: int = MAX_BULLETS) -> list[dict]:
        job_text = f"{job.title} {job.company} {' '.join(job.skills)} {job.description[:3000]}"
        job_vec = embed_text(job_text)
        ranked: list[tuple[float, dict]] = []
        for exp in profile.experience:
            for bullet in exp.bullets:
                score = cosine(job_vec, embed_text(bullet))
                score += 0.03 * len(set(extract_skills(bullet)) & set(job.skills))
                ranked.append(
                    (
                        score,
                        {
                            "text": bullet,
                            "title": exp.title,
                            "company": exp.company,
                        },
                    )
                )
        if not ranked and profile.summary:
            return [{"text": profile.summary, "title": profile.headline, "company": ""}]
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in ranked[:limit]]

    @staticmethod
    def _rank_bullets(job: Job, bullets: list[str]) -> list[str]:
        job_vec = embed_text(
            f"{job.title} {' '.join(job.skills)} {job.description[:2500]}"
        )
        ranked = sorted(
            bullets,
            key=lambda bullet: cosine(job_vec, embed_text(bullet)),
            reverse=True,
        )
        return ranked

    @staticmethod
    def _matched_skills(job: Job, profile: Profile) -> list[str]:
        candidate = set(profile.skills) | set(profile.experience_skills)
        matched = [skill for skill in job.skills if skill in candidate]
        return list(dict.fromkeys(matched or profile.skills))[:14]

    @staticmethod
    def _fallback_summary(profile: Profile) -> str:
        skills = ", ".join(profile.skills[:5])
        target = profile.target_roles[0] if profile.target_roles else "software"
        if skills:
            return f"{target} candidate with hands-on experience in {skills}."
        return ""

    @staticmethod
    def _headline_safe(headline: str, profile: Profile) -> bool:
        if not headline:
            return False
        skills = extract_skills(headline)
        known = set(profile.skills) | set(profile.experience_skills)
        if any(skill not in known for skill in skills):
            return False
        if re.search(r"\d+\+?\s*years", headline, re.I) and not re.search(
            r"\d+\+?\s*years", profile.summary, re.I
        ):
            return False
        return True

    @staticmethod
    def _job_brief(job: Job) -> str:
        return (
            f"Title: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
            f"Skills: {', '.join(job.skills)}\n\n{job.description[:4000]}"
        )

    # --- generated documents ----------------------------------------------
    def _finalise_cv(self, cv: TailoredCV, job: Job, profile: Profile) -> TailoredCV:
        """Attach provenance, a change summary and real DOCX/PDF files."""
        cv.resume_id = self._current_resume_id()
        cv.changes = self._change_summary(cv, profile, job)
        blocks = self._document_blocks(cv, profile, job)
        cv.files = self._write_files(cv, blocks, profile, job)
        self._save_cv(cv)
        return cv

    def _current_resume_id(self) -> str:
        row = self.db.query_one(
            "SELECT id FROM resumes WHERE user_id = ? AND is_current = 1",
            (current_user_id(),),
        )
        return str(row["id"]) if row else ""

    @staticmethod
    def _document_blocks(cv: TailoredCV, profile: Profile, job: Job) -> list[dict]:
        """Build one neutral document model shared by the DOCX and PDF writers.

        The whole history is preserved.  Tailored bullets replace their exact
        source bullet; every other original bullet stays verbatim.  Nothing is
        added from the job description because that would invent experience.
        """
        blocks: list[dict] = []
        name = (profile.full_name or cv.headline or "Candidate").strip()
        blocks.append({"type": "name", "text": name})

        contact_parts = [
            profile.email,
            profile.phone,
            profile.location,
            *[link for link in profile.links[:2] if link],
        ]
        contact = " | ".join(part.strip() for part in contact_parts if part and part.strip())
        if contact:
            blocks.append({"type": "line", "text": contact})

        headline = (cv.headline or profile.headline).strip()
        if headline:
            blocks.append({"type": "line", "text": headline})

        summary = (cv.summary or profile.summary).strip()
        if summary:
            blocks.append({"type": "heading", "text": "Summary"})
            blocks.append({"type": "paragraph", "text": summary})

        if cv.skills:
            blocks.append({"type": "heading", "text": "Skills"})
            blocks.append({"type": "paragraph", "text": ", ".join(cv.skills)})

        tailored = {
            bullet.source_bullet: bullet.text
            for bullet in cv.bullets
            if bullet.source_bullet and bullet.supported
        }
        experiences = [
            exp for exp in profile.experience if exp.title or exp.company or exp.bullets
        ]
        if experiences:
            blocks.append({"type": "heading", "text": "Experience"})
            for exp in experiences:
                role = (exp.title or "Role").strip()
                company = (exp.company or "").strip()
                text = f"{role} — {company}" if company else role
                meta = " · ".join(
                    part
                    for part in (_date_range(exp.start, exp.end), (exp.location or "").strip())
                    if part
                )
                blocks.append({"type": "entry", "text": text, "meta": meta})
                for original in exp.bullets:
                    line = (tailored.get(original) or original or "").strip()
                    if line:
                        blocks.append({"type": "bullet", "text": line})

        if profile.projects:
            blocks.append({"type": "heading", "text": "Projects"})
            for project in profile.projects:
                blocks.append(
                    {
                        "type": "entry",
                        "text": project.name,
                        "meta": project.link or "",
                    }
                )
                if project.description:
                    blocks.append({"type": "paragraph", "text": project.description})
                if project.technologies:
                    blocks.append(
                        {
                            "type": "paragraph",
                            "text": "Technologies: " + ", ".join(project.technologies),
                        }
                    )

        if profile.education:
            blocks.append({"type": "heading", "text": "Education"})
            for item in profile.education:
                degree = (item.degree or "").strip()
                if not degree:
                    continue
                blocks.append(
                    {"type": "entry", "text": degree, "meta": (item.year or "").strip()}
                )
                if item.institution:
                    blocks.append({"type": "paragraph", "text": item.institution})
                if item.detail:
                    blocks.append({"type": "paragraph", "text": item.detail})

        if profile.certifications:
            blocks.append({"type": "heading", "text": "Certifications"})
            for item in profile.certifications:
                meta = " · ".join(
                    part for part in ((item.issuer or "").strip(), (item.year or "").strip()) if part
                )
                blocks.append({"type": "entry", "text": item.name, "meta": meta})

        if profile.achievements:
            blocks.append({"type": "heading", "text": "Achievements"})
            for achievement in profile.achievements:
                if achievement.strip():
                    blocks.append({"type": "bullet", "text": achievement.strip()})

        return blocks

    def _write_files(
        self,
        cv: TailoredCV,
        blocks: list[dict],
        profile: Profile,
        job: Job,
    ) -> dict[str, str]:
        settings = get_settings()
        root = self._storage_dir()
        data_root = Path(settings.data_dir)
        title = f"{profile.full_name or 'Candidate'} — {job.title}".strip()
        files: dict[str, str] = {}

        docx_bytes = build_docx(blocks, title=title)
        problems = validate_docx(docx_bytes)
        if problems:
            raise TailorError(
                "The generated DOCX failed validation: " + "; ".join(problems)
            )
        docx_path = root / f"{cv.id}.docx"
        docx_path.write_bytes(docx_bytes)
        files["docx"] = _relative(docx_path, data_root)

        try:
            pdf_bytes = build_pdf(blocks, title=title)
            pdf_problems = validate_pdf(pdf_bytes)
            if pdf_problems:
                log.warning("Generated PDF failed validation: %s", "; ".join(pdf_problems))
            else:
                pdf_path = root / f"{cv.id}.pdf"
                pdf_path.write_bytes(pdf_bytes)
                files["pdf"] = _relative(pdf_path, data_root)
        except Exception:  # noqa: BLE001 - DOCX remains downloadable
            log.warning("PDF generation failed; only DOCX is available", exc_info=True)
        return files

    def _storage_dir(self) -> Path:
        root = Path(get_settings().data_dir) / "tailored" / _safe_component(current_user_id())
        root.mkdir(parents=True, exist_ok=True)
        return root

    def cv_file(self, cv_id: str, fmt: str = "docx") -> tuple[Path, str, str]:
        """Return ``(path, download_name, mimetype)``, regenerating if needed."""
        fmt = (fmt or "docx").lower().lstrip(".")
        if fmt not in FILE_FORMATS:
            raise TailorError("Unsupported format. Choose DOCX or PDF.")
        cv = self.get_cv(cv_id)
        if cv is None:
            raise TailorError("Tailored CV not found.")
        relative = (cv.files or {}).get(fmt)
        if relative:
            existing = Path(get_settings().data_dir) / relative
            if existing.is_file():
                return existing, self._download_name(cv, fmt), FILE_FORMATS[fmt][0]

        job = self.jobs.get(cv.job_id)
        if job is None:
            raise TailorError("The target job for this CV no longer exists.")
        profile = self.profiles.get()
        blocks = self._document_blocks(cv, profile, job)
        cv.files = {**(cv.files or {}), **self._write_files(cv, blocks, profile, job)}
        self._save_cv(cv)
        relative = cv.files.get(fmt)
        if not relative:
            raise TailorError("That format could not be generated.")
        path = Path(get_settings().data_dir) / relative
        return path, self._download_name(cv, fmt), FILE_FORMATS[fmt][0]

    @staticmethod
    def _download_name(cv: TailoredCV, fmt: str) -> str:
        name = (cv.job.company if cv.job else "") or "Job"
        base = re.sub(r"[^A-Za-z0-9]+", "_", f"{name}_CV").strip("_") or "Tailored_CV"
        return f"{base}.{fmt}"

    def _change_summary(self, cv: TailoredCV, profile: Profile, job: Job) -> list[dict]:
        """Explain what changed without claiming any new fact."""
        changes: list[dict] = []
        target = f"{job.title} at {job.company}" if job.company else job.title

        new_summary = (cv.summary or "").strip()
        old_summary = (profile.summary or "").strip()
        if new_summary and _norm(new_summary) != _norm(old_summary):
            changes.append(
                {
                    "field": "summary",
                    "label": "Professional summary",
                    "detail": f"Rewritten for the {target} role using only your existing facts.",
                }
            )

        new_headline = (cv.headline or "").strip()
        if new_headline and _norm(new_headline) != _norm(profile.headline):
            changes.append(
                {
                    "field": "headline",
                    "label": "Headline",
                    "detail": f"Aligned to “{new_headline}”.",
                }
            )

        if cv.skills:
            original = [skill.lower() for skill in profile.skills]
            ordered = [skill.lower() for skill in cv.skills]
            matched = [skill for skill in cv.skills if skill.lower() in {s.lower() for s in job.skills}]
            if ordered != original[: len(ordered)] or matched:
                detail = "Reordered to lead with your relevant skills"
                if matched:
                    detail += f": {', '.join(matched[:6])}"
                changes.append(
                    {"field": "skills", "label": "Skills", "detail": detail + "."}
                )

        rewrites = [
            bullet
            for bullet in cv.bullets
            if bullet.source_bullet and _norm(bullet.text) != _norm(bullet.source_bullet)
        ]
        if rewrites:
            changes.append(
                {
                    "field": "experience",
                    "label": "Experience bullets",
                    "detail": (
                        f"Rewrote {len(rewrites)} bullet"
                        f"{'' if len(rewrites) == 1 else 's'} for this job; "
                        "employers, dates and metrics are unchanged."
                    ),
                }
            )

        if not changes:
            changes.append(
                {
                    "field": "none",
                    "label": "No factual changes",
                    "detail": (
                        "Your profile already matched this role; the CV keeps the same "
                        "facts and only the relevant bullets were selected."
                    ),
                }
            )
        return changes

    # --- persistence -------------------------------------------------------
    def _save_cv(self, cv: TailoredCV) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO tailored_cvs (id, user_id, job_id, resume_id, headline, "
            "summary, skills, bullets, changes, files, checks, unsupported_count, verified, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cv.id,
                current_user_id(),
                cv.job_id,
                cv.resume_id,
                cv.headline,
                cv.summary,
                to_json(cv.skills),
                to_json([bullet.model_dump(mode="json") for bullet in cv.bullets]),
                to_json(cv.changes),
                to_json(cv.files),
                to_json([check.model_dump(mode="json") for check in cv.checks]),
                cv.unsupported_count,
                int(cv.verified),
                cv.created_at.isoformat(),
            ),
        )

    def _save_letter(self, letter: CoverLetter) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cover_letters (id, user_id, job_id, body, checks, "
            "unsupported_count, verified, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                letter.id,
                current_user_id(),
                letter.job_id,
                letter.body,
                to_json([check.model_dump(mode="json") for check in letter.checks]),
                letter.unsupported_count,
                int(letter.verified),
                letter.created_at.isoformat(),
            ),
        )

    def _row_to_cv(self, row: dict) -> TailoredCV:
        job = self.jobs.get(row["job_id"])
        return TailoredCV(
            id=row["id"],
            job_id=row["job_id"],
            resume_id=row.get("resume_id") or "",
            headline=row["headline"],
            summary=row["summary"],
            skills=from_json(row["skills"], []),
            bullets=[TailoredBullet.model_validate(item) for item in from_json(row["bullets"], [])],
            changes=from_json(row.get("changes"), []),
            files=from_json(row.get("files"), {}),
            checks=[ClaimCheck.model_validate(item) for item in from_json(row["checks"], [])],
            unsupported_count=row["unsupported_count"],
            verified=bool(row["verified"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            job=job.card() if job else None,
        )

    def _row_to_letter(self, row: dict) -> CoverLetter:
        job = self.jobs.get(row["job_id"])
        return CoverLetter(
            id=row["id"],
            job_id=row["job_id"],
            body=row["body"],
            checks=[ClaimCheck.model_validate(item) for item in from_json(row["checks"], [])],
            unsupported_count=row["unsupported_count"],
            verified=bool(row["verified"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            job=job.card() if job else None,
        )


class TailorError(ValueError):
    """User-facing tailoring problem (no profile, missing job, bad file)."""


def _norm(value: str) -> str:
    return " ".join((value or "").split()).strip().lower()


def _date_range(start: str, end: str) -> str:
    start = (start or "").strip()
    end = (end or "").strip()
    if start and end:
        return f"{start} – {end}"
    return start or end


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value or "user")[:80] or "user"


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _as_sentence(text: str) -> str:
    text = text.strip().rstrip(".")
    if not text:
        return ""
    return text[0].upper() + text[1:] + "."


def _lower_first(text: str) -> str:
    if not text:
        return ""
    return text[0].lower() + text[1:]


def _join_list(items: list[str]) -> str:
    items = [item for item in items if item]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _first_sentence(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    match = re.match(r"(.+?[.!?])(?:\s|$)", cleaned)
    sentence = match.group(1) if match else cleaned
    return sentence if len(sentence) > 20 else ""


def _substantive(sentence: str) -> bool:
    lowered = sentence.lower()
    if lowered.startswith(
        (
            "dear ", "i am applying", "i would welcome", "thank you", "sincerely",
            "best regards", "kind regards",
        )
    ):
        return False
    if re.search(r"\d", sentence):
        return True
    if extract_skills(sentence):
        return True
    return any(
        word in lowered
        for word in (
            "built", "led", "designed", "reduced", "improved", "shipped", "worked",
            "experience", "engineer", "developed", "automated", "trained", "years",
        )
    )


def get_tailor_service() -> TailorService:
    return TailorService()
