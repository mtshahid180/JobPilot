"""Profile storage and CV ingestion."""

from __future__ import annotations

import re

from jobpilot.core import locations as geo
from jobpilot.core.context import current_user_id
from jobpilot.core.db import Database, get_db, to_json
from jobpilot.core.models import (
    Certification,
    Education,
    Experience,
    Profile,
    Project,
    utcnow,
)
from jobpilot.pipelines.extract import extract_skills, strip_html

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-().]{7,}\d)")
URL_RE = re.compile(r"https?://[^\s,;]+")
HEADER_SPLIT_RE = re.compile(r"[|·•]|\s+[–—]\s+")

SECTION_ALIASES = {
    "summary": ("summary", "profile", "objective", "about"),
    "experience": ("experience", "employment", "work history", "professional experience"),
    "education": ("education", "academics", "qualifications"),
    "skills": ("skills", "technical skills", "core competencies", "technologies"),
    "projects": ("projects", "selected projects"),
    "certifications": ("certifications", "certificates", "licenses"),
    "achievements": ("achievements", "awards", "honours", "honors", "accomplishments"),
}


class ProfileService:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def get(self) -> Profile:
        row = self.db.query_one(
            "SELECT data FROM profile WHERE user_id = ?", (current_user_id(),)
        )
        if not row:
            profile = Profile()
            self.save(profile)
            return profile
        import json

        try:
            return Profile.model_validate(json.loads(row["data"]))
        except Exception:
            return Profile()

    def save(self, profile: Profile) -> Profile:
        profile.updated_at = utcnow()
        self.db.execute(
            "INSERT INTO profile (user_id, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, "
            "updated_at = excluded.updated_at",
            (
                current_user_id(),
                to_json(profile.model_dump(mode="json")),
                profile.updated_at.isoformat(),
            ),
        )
        return profile

    def update(self, patch: dict) -> Profile:
        profile = self.get()
        data = profile.model_dump()
        for key, value in patch.items():
            if key in data and key != "updated_at":
                data[key] = value
        updated = Profile.model_validate(data)
        return self.save(updated)

    def list_skills(self) -> list[dict]:
        profile = self.get()
        counts: dict[str, int] = {}
        for skill in profile.skills:
            counts[skill] = counts.get(skill, 0) + 2
        for exp in profile.experience:
            for skill in exp.skills:
                counts[skill] = counts.get(skill, 0) + 1
            for bullet in exp.bullets:
                for skill in extract_skills(bullet):
                    counts[skill] = counts.get(skill, 0) + 1
        return [
            {"skill": skill, "evidence_count": count}
            for skill, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
        ]

    def search_experience(self, query: str, limit: int = 5) -> list[dict]:
        from jobpilot.pipelines.embedding import cosine, embed_text

        profile = self.get()
        qvec = embed_text(query)
        results: list[dict] = []
        for exp in profile.experience:
            for bullet in exp.bullets:
                score = cosine(qvec, embed_text(bullet))
                results.append(
                    {
                        "title": exp.title,
                        "company": exp.company,
                        "bullet": bullet,
                        "score": round(score, 3),
                    }
                )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:limit]

    def parse_cv(self, text: str) -> Profile:
        """Heuristically parse pasted CV text into a profile.

        The parser is intentionally conservative: everything it extracts is
        text that was literally present in the CV.
        """
        profile = self.parse_cv_into(text, self.get())
        return self.save(profile)

    def parse_cv_into(self, text: str, profile: Profile) -> Profile:
        """Populate ``profile`` from CV text without persisting it.

        Resume uploads use this to build a review draft; nothing reaches the
        saved profile until the user confirms the merge.
        """
        cleaned = strip_html(text)
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]

        email = EMAIL_RE.search(cleaned)
        if email:
            profile.email = email.group(0)
        phone = PHONE_RE.search(cleaned[:1500])
        if phone:
            profile.phone = phone.group(0).strip()
        links = [url.rstrip(".,;)") for url in URL_RE.findall(cleaned)][:5]
        if links:
            profile.links = list(dict.fromkeys(links))

        if lines:
            candidate = lines[0]
            if 2 <= len(candidate.split()) <= 5 and not EMAIL_RE.search(candidate):
                profile.full_name = candidate[:80]

        sections: dict[str, list[str]] = {key: [] for key in SECTION_ALIASES}
        current = ""
        for line in lines:
            lowered = line.lower().strip(" :")
            matched = next(
                (
                    key
                    for key, aliases in SECTION_ALIASES.items()
                    if any(lowered == alias or lowered.startswith(alias + " ") for alias in aliases)
                ),
                None,
            )
            if matched:
                current = matched
                continue
            if current:
                sections[current].append(line)

        if sections["summary"]:
            profile.summary = " ".join(sections["summary"])[:1200]
        skills_text = " ".join(sections["skills"])
        parsed_skills = extract_skills(skills_text) if skills_text else []
        if parsed_skills:
            # Keep the order the candidate used, not our alias-table order.
            source = (skills_text or cleaned).lower()
            parsed_skills.sort(
                key=lambda skill: source.find(skill.lower())
                if skill.lower() in source
                else len(source)
            )
            profile.skills = list(dict.fromkeys([*profile.skills, *parsed_skills]))[:40]

        experience = _parse_experience(sections["experience"])
        if experience:
            profile.experience = experience
        education = _parse_education(sections["education"])
        if education:
            profile.education = education
        projects = _parse_projects(sections["projects"])
        if projects:
            profile.projects = projects
        certifications = _parse_certifications(sections["certifications"])
        if certifications:
            profile.certifications = certifications
        achievements = _parse_achievements(sections["achievements"])
        if achievements:
            profile.achievements = achievements

        _apply_header(profile, lines)

        return profile

    def completeness(self) -> int:
        return self.get().completeness()


def _apply_header(profile: Profile, lines: list[str]) -> None:
    """Read the candidate's role and location from the top of a CV.

    A header such as ``Senior Financial Analyst | Karachi, Pakistan`` carries
    both the professional headline and the candidate's location. Treating the
    whole line as a headline lost the location and produced a headline that no
    job search could use, so the two facts are separated here.
    """
    if not lines:
        return
    aliases = {alias for group in SECTION_ALIASES.values() for alias in group}
    header = [
        line
        for line in lines[1:5]
        if not EMAIL_RE.search(line)
        and not URL_RE.search(line)
        and line.lower().strip(" :") not in aliases
    ]

    headline = ""
    location = ""
    for line in header:
        parts = [part.strip() for part in HEADER_SPLIT_RE.split(line) if part.strip()]
        for index, part in enumerate(parts):
            place = geo.find_location(part)
            if place is not None and len(part) <= 80:
                location = location or part
            elif index == 0 and not headline and 3 <= len(part) <= 90:
                headline = part
        if headline and location:
            break

    if location:
        profile.location = location
    if headline:
        profile.headline = headline[:120]
    elif not profile.headline and profile.experience:
        # No role line at the top: fall back to the most recent job title.
        profile.headline = profile.experience[0].title[:120]


def _parse_experience(lines: list[str]) -> list[Experience]:
    entries: list[Experience] = []
    current: Experience | None = None
    for line in lines:
        bullet = re.match(r"^[•\-*·]\s*(.+)$", line)
        if bullet:
            if current:
                current.bullets.append(bullet.group(1).strip())
            continue
        looks_like_header = (
            len(line) < 130
            and (
                " at " in line.lower()
                or re.search(r"\b(19|20)\d{2}\b", line)
                or "|" in line
            )
        )
        if looks_like_header:
            if current:
                entries.append(current)
            title, company, start, end = _split_role(line)
            current = Experience(title=title, company=company, start=start, end=end)
        elif current and len(line) < 260:
            current.bullets.append(line)
    if current:
        entries.append(current)
    return entries[:8]


def _split_role(line: str) -> tuple[str, str, str, str]:
    parts = [part.strip() for part in re.split(r"\s+[|–—]\s+|\s+at\s+", line) if part.strip()]
    start, end = "", ""
    date_match = re.search(
        r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}|\d{4})"
        r"\s*(?:[-–—to]+)\s*"
        r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}|\d{4}|present|current)",
        line,
        re.I,
    )
    if date_match:
        start, end = date_match.group(1), date_match.group(2)
        parts = [re.sub(date_match.group(0), "", part).strip(" ,-–—") for part in parts]
    title = parts[0] if parts else ""
    company = parts[1] if len(parts) > 1 else ""
    return title[:120], company[:120], start, end


def _parse_education(lines: list[str]) -> list[Education]:
    entries: list[Education] = []
    for line in lines[:12]:
        if len(line) > 4:
            year = re.search(r"\b(19|20)\d{2}\b", line)
            parts = [part.strip() for part in re.split(r"\s+[|–—]\s+|,", line) if part.strip()]
            entries.append(
                Education(
                    degree=parts[0][:120] if parts else line[:120],
                    institution=parts[1][:120] if len(parts) > 1 else "",
                    year=year.group(0) if year else "",
                    detail="",
                )
            )
    return entries[:6]


def _parse_projects(lines: list[str]) -> list[Project]:
    """Parse a projects section without inventing names or technologies."""
    entries: list[Project] = []
    current: Project | None = None
    for line in lines:
        bullet = re.match(r"^[•\-*·]\s*(.+)$", line)
        if bullet:
            if current:
                current.description = " ".join(
                    part for part in (current.description, bullet.group(1).strip()) if part
                )[:800]
                current.technologies = extract_skills(current.description, limit=8)
            continue
        parts = [part.strip() for part in re.split(r"\s+[|–—]\s+|:\s+", line) if part.strip()]
        if not parts:
            continue
        name = parts[0][:120]
        description = " ".join(parts[1:])[:800]
        if not description and len(name.split()) > 12:
            # A long unbroken sentence continues the previous project.
            if current:
                current.description = " ".join(
                    part for part in (current.description, name) if part
                )[:800]
                current.technologies = extract_skills(current.description, limit=8)
            continue
        if current:
            entries.append(current)
        current = Project(
            name=name,
            description=description,
            technologies=extract_skills(description, limit=8),
        )
    if current:
        entries.append(current)
    return [entry for entry in entries if entry.name][:8]


def _parse_certifications(lines: list[str]) -> list[Certification]:
    entries: list[Certification] = []
    for line in lines[:12]:
        cleaned = re.sub(r"^[•\-*·]\s*", "", line).strip()
        if len(cleaned) < 2:
            continue
        year = re.search(r"\b(19|20)\d{2}\b", cleaned)
        if "|" in cleaned:
            parts = [part.strip() for part in cleaned.split("|") if part.strip()]
        elif re.search(r"\s+[–—]\s+", cleaned):
            parts = [
                part.strip()
                for part in re.split(r"\s+[–—]\s+", cleaned)
                if part.strip()
            ]
        else:
            parts = [cleaned]
        if len(parts) > 1 and re.fullmatch(r"(19|20)\d{2}", parts[-1] or ""):
            parts = parts[:-1]
        entries.append(
            Certification(
                name=parts[0][:120] if parts else cleaned[:120],
                issuer=parts[1][:120] if len(parts) > 1 else "",
                year=year.group(0) if year else "",
            )
        )
    return entries[:8]


def _parse_achievements(lines: list[str]) -> list[str]:
    achievements: list[str] = []
    for line in lines[:12]:
        cleaned = re.sub(r"^[•\-*·]\s*", "", line).strip()
        if len(cleaned) > 3:
            achievements.append(cleaned[:300])
    return achievements[:8]



