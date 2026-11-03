"""Domain models shared by the harness, MCP servers, services and UI."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}_{raw}" if prefix else raw


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
class User(Model):
    id: str = Field(default_factory=lambda: new_id("usr"))
    email: str
    name: str = ""
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_login_at: datetime | None = None

    @property
    def display_name(self) -> str:
        return self.name.strip() or self.email.split("@")[0]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
WorkplaceType = Literal["remote", "hybrid", "onsite", "unknown"]
RemoteScope = Literal["worldwide", "country", "region", "city", "unknown"]
LocationConfidence = Literal["verified", "likely", "inferred", "unknown"]
MatchTier = Literal["exact", "related", "unverified", "mismatch"]
Availability = Literal["active", "unknown", "stale", "expired"]


class LocationMatch(Model):
    """How a listing's geography relates to the requested geography."""

    tier: Literal["exact", "regional", "remote", "unverified", "mismatch", "any"] = "any"
    verified: bool = False
    score: float = 0.0
    reason: str = ""


class RawJob(Model):
    """A job exactly as a source returned it, before extraction."""

    source: str
    external_id: str = ""
    title: str
    company: str = "Unknown"
    company_location: str = ""
    location: str = ""
    job_location: str = ""
    eligible_locations: list[str] = Field(default_factory=list)
    remote_scope: RemoteScope = "unknown"
    location_confidence: LocationConfidence = "unknown"
    description: str = ""
    url: str = ""
    salary: str = ""
    employment_type: str = ""
    department: str = ""
    seniority_hint: str = ""
    remote: bool | None = None
    workplace_type: WorkplaceType = "unknown"
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class Job(Model):
    id: str = Field(default_factory=lambda: new_id("job"))
    source: str = "manual"
    external_id: str = ""
    title: str
    company: str = "Unknown"
    location: str = ""
    description: str = ""
    url: str = ""
    salary: str = ""
    employment_type: str = ""
    remote: bool = False
    seniority: str = "unspecified"
    skills: list[str] = Field(default_factory=list)
    posted_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=utcnow)
    fingerprint: str = ""
    saved: bool = False
    status: str = "new"
    score: float | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    # --- classification & geography (schema v3) ---------------------------
    category: str = "other"
    category_label: str = ""
    workplace_type: WorkplaceType = "unknown"
    company_location: str = ""
    job_location: str = ""
    eligible_locations: list[str] = Field(default_factory=list)
    remote_scope: RemoteScope = "unknown"
    location_confidence: LocationConfidence = "unknown"
    location_restrictions: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    availability: Availability = "unknown"
    last_seen_at: datetime | None = None
    relevance: float = 0.0
    match_tier: MatchTier = "exact"
    relevance_reasons: list[str] = Field(default_factory=list)

    def card(self, score: float | None = None) -> "JobCard":
        return JobCard(
            id=self.id,
            title=self.title,
            company=self.company,
            location=self.location,
            remote=self.remote,
            seniority=self.seniority,
            skills=self.skills[:8],
            salary=self.salary,
            source=self.source,
            posted_at=self.posted_at,
            score=self.score if score is None else score,
            saved=self.saved,
            status=self.status,
            category=self.category,
            category_label=self.category_label,
            workplace_type=self.workplace_type,
            company_location=self.company_location,
            job_location=self.job_location,
            eligible_locations=self.eligible_locations[:8],
            remote_scope=self.remote_scope,
            location_confidence=self.location_confidence,
            availability=self.availability,
            expires_at=self.expires_at,
            fetched_at=self.fetched_at,
            relevance=self.relevance,
            match_tier=self.match_tier,
        )


class JobCard(Model):
    """Compact representation handed to the agent and list endpoints."""

    id: str
    title: str
    company: str
    location: str = ""
    remote: bool = False
    seniority: str = "unspecified"
    skills: list[str] = Field(default_factory=list)
    salary: str = ""
    source: str = ""
    posted_at: datetime | None = None
    score: float | None = None
    saved: bool = False
    status: str = "new"
    category: str = "other"
    category_label: str = ""
    workplace_type: WorkplaceType = "unknown"
    company_location: str = ""
    job_location: str = ""
    eligible_locations: list[str] = Field(default_factory=list)
    remote_scope: RemoteScope = "unknown"
    location_confidence: LocationConfidence = "unknown"
    availability: Availability = "unknown"
    expires_at: datetime | None = None
    fetched_at: datetime | None = None
    relevance: float = 0.0
    match_tier: MatchTier = "exact"


class JobQuery(Model):
    query: str = ""
    location: str = ""
    remote_only: bool = False
    limit: int = 30
    sources: list[str] = Field(default_factory=list)
    posted_within_days: int | None = None

    # --- interpreted filters (schema v3) ----------------------------------
    category: str = ""
    workplace_type: str = ""
    seniority: str = ""
    employment_type: str = ""
    skills: list[str] = Field(default_factory=list)
    city: str = ""
    region: str = ""
    country: str = ""
    country_code: str = ""
    keywords: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    page: int = 1
    page_size: int = 0
    refresh: bool = False
    include_related: bool = True
    include_unverified: bool = True


class ProbeResult(Model):
    name: str
    ok: bool
    latency_ms: float | None = None
    detail: str = ""
    checked_at: datetime = Field(default_factory=utcnow)


class SourceStatus(Model):
    name: str
    priority: int
    enabled: bool = True
    requires_key: bool = False
    last_probe: ProbeResult | None = None

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.last_probe is None:
            return "unknown"
        return "live" if self.last_probe.ok else "down"


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
class Experience(Model):
    title: str
    company: str
    start: str = ""
    end: str = ""
    location: str = ""
    bullets: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class Education(Model):
    degree: str
    institution: str
    year: str = ""
    detail: str = ""


class Project(Model):
    name: str
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    link: str = ""


class Certification(Model):
    name: str
    issuer: str = ""
    year: str = ""


class Profile(Model):
    full_name: str = "Your Name"
    headline: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    remote_preference: Literal["remote", "hybrid", "onsite", "any"] = "any"
    salary_floor: str = ""
    work_authorization: str = ""
    languages: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def all_bullets(self) -> list[str]:
        return [b for exp in self.experience for b in exp.bullets]

    @property
    def experience_skills(self) -> list[str]:
        out: list[str] = []
        for exp in self.experience:
            out.extend(exp.skills)
        return out

    def completeness(self) -> int:
        checks = [
            bool(self.full_name and self.full_name != "Your Name"),
            bool(self.headline),
            bool(self.email),
            bool(self.location),
            bool(self.summary),
            len(self.skills) >= 5,
            bool(self.experience),
            any(exp.bullets for exp in self.experience),
            bool(self.target_roles),
            bool(self.education),
        ]
        return round(100 * sum(checks) / len(checks))


# ---------------------------------------------------------------------------
# Resumes
# ---------------------------------------------------------------------------
ResumeStatus = Literal["uploaded", "extracting", "extracted", "needs_review", "failed"]


class Resume(Model):
    """An uploaded resume and its extraction state.

    ``extracted`` holds a *draft* profile. It is never applied to the real
    profile until the user confirms it, so a parse cannot silently overwrite
    verified information.
    """

    id: str = Field(default_factory=lambda: new_id("res"))
    filename: str = ""
    content_type: str = ""
    size: int = 0
    storage_path: str = ""
    status: ResumeStatus = "uploaded"
    error: str = ""
    extracted: dict[str, Any] = Field(default_factory=dict)
    text_chars: int = 0
    is_current: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ResumeDraft(Model):
    """Review payload: extracted facts plus the fields a merge would change."""

    resume: Resume
    profile_draft: Profile
    changed_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
class ScoreBreakdown(Model):
    skills: float = 0.0
    role: float = 0.0
    location: float = 0.0
    seniority: float = 0.0
    recency: float = 0.0
    semantic: float = 0.0

    def total(self) -> float:
        weights = {
            "skills": 0.45,
            "role": 0.25,
            "semantic": 0.15,
            "location": 0.07,
            "seniority": 0.05,
            "recency": 0.03,
        }
        score = sum(getattr(self, k) * w for k, w in weights.items())
        return round(max(0.0, min(1.0, score)) * 100, 1)


class MatchResult(Model):
    job_id: str
    score: float
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    rationale: str = ""
    method: str = "heuristic"


class SkillGap(Model):
    job_id: str
    have: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    coverage: float = 0.0
    suggestions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
MemoryKind = Literal["semantic", "episodic", "preference", "rule"]


class Memory(Model):
    id: str = Field(default_factory=lambda: new_id("mem"))
    kind: MemoryKind = "semantic"
    content: str
    tags: list[str] = Field(default_factory=list)
    provenance: str = ""
    confidence: float = 0.8
    importance: float = 0.5
    status: Literal["active", "pending", "superseded", "forgotten"] = "active"
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = None
    use_count: int = 0
    supersedes: str | None = None


class MemoryProposal(Model):
    content: str
    kind: MemoryKind = "semantic"
    reason: str = ""
    requires_confirmation: bool = False
    confidence: float = 0.7
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Applications / approvals
# ---------------------------------------------------------------------------
ApplicationStatus = Literal[
    "saved", "applied", "screening", "interview", "offer", "rejected", "withdrawn"
]


class ApplicationEvent(Model):
    at: datetime = Field(default_factory=utcnow)
    status: str
    note: str = ""


class Application(Model):
    id: str = Field(default_factory=lambda: new_id("app"))
    job_id: str
    status: ApplicationStatus = "saved"
    applied_at: datetime | None = None
    follow_up_at: datetime | None = None
    notes: str = ""
    history: list[ApplicationEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    job: JobCard | None = None


class Approval(Model):
    id: str = Field(default_factory=lambda: new_id("apr"))
    run_id: str = ""
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    tier: str = "external"
    status: Literal["pending", "approved", "rejected"] = "pending"
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None


# ---------------------------------------------------------------------------
# Harness / runs
# ---------------------------------------------------------------------------
class ToolCall(Model):
    id: str = Field(default_factory=lambda: new_id("call"))
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    tier: str = "read"
    ok: bool | None = None
    result: Any = None
    error: str = ""
    latency_ms: float = 0.0
    approval_id: str | None = None


class RunStep(Model):
    index: int
    thought: str = ""
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""
    ok: bool = True
    latency_ms: float = 0.0


class Run(Model):
    id: str = Field(default_factory=lambda: new_id("run"))
    session_id: str = ""
    goal: str = ""
    status: Literal["running", "completed", "failed", "awaiting_approval"] = "running"
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    steps: int = 0
    tokens: int = 0
    cost: float = 0.0
    summary: str = ""
    error: str = ""
    mode: str = "local"


class TraceEvent(Model):
    run_id: str
    seq: int
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Tailoring
# ---------------------------------------------------------------------------
class ClaimCheck(Model):
    claim: str
    supported: bool
    evidence: str = ""
    reason: str = ""
    score: float = 0.0


class TailoredBullet(Model):
    text: str
    evidence: str = ""
    supported: bool = True
    source_bullet: str = ""


class TailoredCV(Model):
    id: str = Field(default_factory=lambda: new_id("cv"))
    job_id: str
    resume_id: str = ""
    headline: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    bullets: list[TailoredBullet] = Field(default_factory=list)
    changes: list[dict[str, Any]] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    checks: list[ClaimCheck] = Field(default_factory=list)
    unsupported_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    job: JobCard | None = None
    verified: bool = False
    method: str = "template"


class CoverLetter(Model):
    id: str = Field(default_factory=lambda: new_id("cl"))
    job_id: str
    body: str = ""
    checks: list[ClaimCheck] = Field(default_factory=list)
    unsupported_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    job: JobCard | None = None
    verified: bool = False
    method: str = "template"


class AgentReply(Model):
    run_id: str
    session_id: str = ""
    status: str
    message: str
    steps: list[RunStep] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    mode: str = "local"
    tokens: int = 0
    cost: float = 0.0
