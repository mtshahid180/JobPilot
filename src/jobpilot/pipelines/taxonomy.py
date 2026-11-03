"""Occupation taxonomy, query interpretation and relevance grading.

Search is not a technology keyword matcher.  This module turns a free-text
request into a category, role keywords, seniority and workplace constraints,
then grades every listing against those constraints.  Related occupations are
kept, but labelled, so a finance search never silently mixes in DevOps roles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

from jobpilot.core import locations as geo


@dataclass(frozen=True)
class CategoryDef:
    key: str
    label: str
    title_terms: tuple[str, ...]
    body_terms: tuple[str, ...]
    related: tuple[str, ...]
    synonyms: tuple[str, ...]
    search_terms: tuple[str, ...]


CATEGORIES: dict[str, CategoryDef] = {
    "technology": CategoryDef(
        "technology",
        "Software & IT",
        (
            "software engineer", "software developer", "full stack", "full-stack", "backend",
            "back end", "frontend", "front end", "web developer", "mobile developer",
            "ios developer", "android developer", "qa engineer", "quality assurance engineer",
            "test engineer", "programmer", "java developer", "python developer",
            "php developer", "ruby developer", ".net developer", "game developer",
            "solutions architect", "software architect", "systems engineer", "it support",
            "network engineer", "technical lead", "engineering lead", "developer",
        ),
        (
            "software development", "api", "microservice", "javascript", "python", "java",
            "react", "node", "django", "flask", "golang", "c++", "c#", "php", "ruby",
            "html", "css", "git", "agile", "scrum",
        ),
        ("data_ai", "devops_cloud", "product", "design", "engineering"),
        ("it", "software", "developer", "engineer", "programming", "coding"),
        ("software engineer", "software developer", "full stack developer", "backend developer"),
    ),
    "data_ai": CategoryDef(
        "data_ai",
        "Data, AI & Machine Learning",
        (
            "data scientist", "data analyst", "data engineer", "machine learning",
            "ml engineer", "ai engineer", "artificial intelligence", "analytics engineer",
            "business intelligence", "bi analyst", "nlp engineer", "computer vision",
            "research scientist", "llm engineer", "prompt engineer", "data architect",
            "quantitative analyst", "statistician", "data science",
        ),
        (
            "machine learning", "deep learning", "nlp", "computer vision", "pytorch",
            "tensorflow", "scikit", "pandas", "numpy", "sql", "tableau", "power bi",
            "data pipeline", "etl", "spark", "airflow", "llm", "rag", "statistics",
        ),
        ("technology", "devops_cloud", "product", "finance", "marketing"),
        ("data", "ai", "machine learning", "analytics", "ml", "artificial intelligence"),
        ("data analyst", "data scientist", "machine learning engineer", "data engineer"),
    ),
    "devops_cloud": CategoryDef(
        "devops_cloud",
        "DevOps, Cloud & Security",
        (
            "devops", "dev ops", "site reliability", "sre", "cloud engineer",
            "platform engineer", "infrastructure engineer", "security engineer",
            "cybersecurity", "cyber security", "information security", "network security",
            "security analyst", "soc analyst", "penetration tester", "cloud architect",
            "aws engineer", "azure engineer", "gcp engineer", "kubernetes engineer",
        ),
        (
            "kubernetes", "docker", "terraform", "aws", "azure", "gcp", "ci/cd",
            "linux", "ansible", "jenkins", "cloud", "security", "firewall", "soc",
        ),
        ("technology", "data_ai"),
        ("devops", "cloud", "security", "sre", "infrastructure"),
        ("devops engineer", "cloud engineer", "site reliability engineer", "security engineer"),
    ),
    "finance": CategoryDef(
        "finance",
        "Finance & Financial Analysis",
        (
            "finance", "financial analyst", "financial manager", "financial controller",
            "fp&a", "fpa", "financial planning", "treasury", "investment", "investor",
            "equity research", "risk analyst", "credit analyst", "portfolio manager",
            "private equity", "venture capital", "underwriter", "chief financial officer",
            "cfo", "finance business partner", "corporate finance", "financial modelling",
            "financial modeling", "asset management", "wealth management",
        ),
        (
            "financial analysis", "financial reporting", "forecasting", "budgeting",
            "valuation", "treasury", "investment", "risk", "credit", "p&l",
            "financial statements", "ifrs", "gaap", "excel", "financial model",
        ),
        ("accounting", "banking", "management", "data_ai"),
        ("finance", "financial", "treasury", "investment", "fp&a", "risk"),
        ("finance", "financial analyst", "financial planning", "treasury analyst"),
    ),
    "accounting": CategoryDef(
        "accounting",
        "Accounting, Audit & Tax",
        (
            "accountant", "accounting", "bookkeeper", "bookkeeping", "auditor", "audit",
            "tax", "taxation", "payroll", "accounts payable", "accounts receivable",
            "chartered accountant", "acca", "cma", "cpa", "financial accountant",
            "management accountant", "cost accountant", "internal audit", "tax consultant",
            "audit associate", "audit manager",
        ),
        (
            "accounting", "bookkeeping", "ledger", "reconciliation", "journal entry",
            "accounts payable", "accounts receivable", "payroll", "tax return",
            "audit", "ifrs", "gaap", "quickbooks", "sap", "excel",
        ),
        ("finance", "banking", "admin"),
        ("accounting", "accountant", "audit", "tax", "bookkeeping", "payroll"),
        ("accountant", "accounting", "audit associate", "tax consultant"),
    ),
    "banking": CategoryDef(
        "banking",
        "Banking & Financial Services",
        (
            "banking", "bank", "relationship manager", "branch manager", "loan officer",
            "credit officer", "teller", "trade finance", "islamic banking", "retail banking",
            "corporate banking", "investment banking", "treasury operations", "aml",
            "kyc", "anti money laundering", "compliance officer", "bank operations",
            "cash officer", "recovery officer", "branch operations",
        ),
        (
            "banking", "loan", "credit", "deposit", "branch", "kyc", "aml",
            "compliance", "trade finance", "treasury", "relationship management",
            "islamic finance", "retail bank", "corporate bank",
        ),
        ("finance", "accounting", "sales", "customer_support"),
        ("banking", "bank", "relationship manager", "credit", "branch"),
        ("banking", "relationship manager", "credit analyst", "branch operations"),
    ),
    "sales": CategoryDef(
        "sales",
        "Sales & Business Development",
        (
            "sales", "account executive", "business development", "bdr", "sdr",
            "sales manager", "sales representative", "sales executive", "account manager",
            "territory manager", "revenue", "partnership", "channel sales",
            "retail sales", "inside sales", "field sales", "key account manager",
            "business development manager", "sales engineer", "customer success manager",
        ),
        (
            "sales", "revenue", "quota", "pipeline", "business development", "prospecting",
            "account management", "crm", "salesforce", "negotiation", "client acquisition",
            "upselling", "territory",
        ),
        ("marketing", "management", "customer_support", "banking"),
        ("sales", "business development", "account executive", "account manager"),
        ("sales executive", "business development manager", "account manager", "sales manager"),
    ),
    "marketing": CategoryDef(
        "marketing",
        "Marketing, Content & Communications",
        (
            "marketing", "digital marketing", "seo", "sem", "content", "copywriter",
            "social media", "brand", "growth", "communications", "public relations",
            "pr manager", "campaign", "performance marketing", "email marketing",
            "product marketing", "marketing manager", "marketing executive",
            "content writer", "content strategist", "growth hacker", "media buyer",
        ),
        (
            "marketing", "campaign", "seo", "sem", "content", "social media", "brand",
            "advertising", "copywriting", "email marketing", "google analytics",
            "digital", "communications", "public relations", "lead generation",
        ),
        ("sales", "design", "product", "data_ai"),
        ("marketing", "digital marketing", "content", "brand", "communications"),
        ("marketing manager", "digital marketing", "content writer", "brand manager"),
    ),
    "hr": CategoryDef(
        "hr",
        "Human Resources & Recruiting",
        (
            "human resources", "hr", "recruiter", "recruitment", "talent acquisition",
            "people operations", "hr generalist", "hr manager", "hr officer",
            "compensation", "benefits", "organizational development", "learning and development",
            "talent management", "hr business partner", "hris", "personnel",
        ),
        (
            "recruitment", "talent acquisition", "onboarding", "employee relations",
            "performance management", "compensation", "benefits", "hr policy",
            "payroll", "hris", "workforce planning",
        ),
        ("admin", "management", "operations"),
        ("hr", "human resources", "recruiter", "talent acquisition"),
        ("human resources", "recruiter", "talent acquisition", "hr manager"),
    ),
    "customer_support": CategoryDef(
        "customer_support",
        "Customer Support & Service",
        (
            "customer support", "customer service", "call centre", "call center",
            "help desk", "helpdesk", "support agent", "technical support",
            "customer success", "client services", "customer care",
            "customer experience", "service desk", "complaint", "support specialist",
        ),
        (
            "customer support", "customer service", "ticketing", "zendesk", "call center",
            "call centre", "help desk", "troubleshooting", "customer satisfaction",
            "sla", "chat support", "client support",
        ),
        ("operations", "sales", "technology"),
        ("customer support", "customer service", "call center", "help desk"),
        ("customer service", "customer support", "call center agent", "help desk"),
    ),
    "operations": CategoryDef(
        "operations",
        "Operations, Supply Chain & Admin",
        (
            "operations", "supply chain", "logistics", "procurement", "warehouse",
            "inventory", "production", "project coordinator", "facilities",
            "admin officer", "office manager", "operations manager", "dispatch",
            "fleet", "store manager", "quality assurance", "quality control",
            "health and safety", "hse", "plant manager",
        ),
        (
            "operations", "supply chain", "logistics", "procurement", "warehouse",
            "inventory", "production", "quality", "process improvement", "sop",
            "facilities", "vendor management", "scheduling",
        ),
        ("management", "admin", "customer_support", "engineering"),
        ("operations", "supply chain", "logistics", "procurement"),
        ("operations manager", "supply chain", "logistics", "procurement officer"),
    ),
    "management": CategoryDef(
        "management",
        "Management & Strategy",
        (
            "general manager", "operations manager", "project manager", "program manager",
            "business manager", "management consultant", "strategy", "strategy manager",
            "chief operating officer", "coo", "chief executive", "ceo", "managing director",
            "director", "head of", "business analyst", "business consultant",
            "change manager", "transformation manager", "pmo",
        ),
        (
            "leadership", "strategy", "stakeholder", "project management", "program management",
            "business analysis", "change management", "governance", "operating model",
            "p&l", "budget", "consulting",
        ),
        ("finance", "sales", "marketing", "operations", "hr", "product"),
        ("management", "manager", "strategy", "consultant", "director"),
        ("business analyst", "project manager", "operations manager", "management consultant"),
    ),
    "healthcare": CategoryDef(
        "healthcare",
        "Healthcare & Medicine",
        (
            "nurse", "doctor", "physician", "medical", "clinical", "pharmacist",
            "healthcare", "hospital", "dentist", "therapist", "lab technician",
            "radiologist", "surgeon", "paramedic", "medical officer", "physiotherapist",
            "psychologist", "nutritionist", "midwife", "caregiver",
        ),
        (
            "patient", "clinical", "medical", "healthcare", "hospital", "diagnosis",
            "treatment", "nursing", "pharmacy", "surgery", "health",
        ),
        ("management", "admin", "education"),
        ("healthcare", "medical", "nurse", "doctor", "clinical"),
        ("registered nurse", "medical officer", "pharmacist", "clinical officer"),
    ),
    "education": CategoryDef(
        "education",
        "Education & Training",
        (
            "teacher", "lecturer", "professor", "tutor", "education", "academic",
            "school", "instructor", "curriculum", "principal", "trainer",
            "teaching assistant", "education manager", "coordinator education",
        ),
        (
            "teaching", "curriculum", "classroom", "students", "lesson", "education",
            "academic", "training", "pedagogy", "assessment",
        ),
        ("management", "hr"),
        ("education", "teacher", "lecturer", "tutor", "trainer"),
        ("teacher", "lecturer", "instructor", "education coordinator"),
    ),
    "engineering": CategoryDef(
        "engineering",
        "Engineering & Construction",
        (
            "civil engineer", "mechanical engineer", "electrical engineer",
            "structural engineer", "site engineer", "chemical engineer",
            "industrial engineer", "construction manager", "architect",
            "project engineer", "design engineer", "maintenance engineer",
            "quantity surveyor", "hvac engineer", "telecom engineer",
        ),
        (
            "civil", "mechanical", "electrical", "structural", "construction",
            "autocad", "hvac", "manufacturing", "maintenance", "site", "engineering",
        ),
        ("technology", "operations"),
        ("engineering", "civil engineer", "mechanical engineer", "electrical engineer"),
        ("civil engineer", "mechanical engineer", "electrical engineer", "site engineer"),
    ),
    "legal": CategoryDef(
        "legal",
        "Legal & Compliance",
        (
            "lawyer", "legal", "attorney", "counsel", "paralegal", "compliance officer",
            "litigation", "corporate counsel", "legal advisor", "legal manager",
            "contract manager", "regulatory", "company secretary",
        ),
        (
            "legal", "litigation", "contract", "compliance", "regulatory", "law",
            "court", "legal advice", "policy",
        ),
        ("finance", "management", "banking"),
        ("legal", "lawyer", "compliance", "counsel"),
        ("legal advisor", "compliance officer", "paralegal", "corporate counsel"),
    ),
    "design": CategoryDef(
        "design",
        "Design & Creative",
        (
            "designer", "ui designer", "ux designer", "graphic designer",
            "product designer", "visual designer", "illustrator", "animator",
            "creative", "art director", "motion designer", "brand designer",
        ),
        (
            "figma", "sketch", "adobe", "photoshop", "illustrator", "ux", "ui",
            "wireframe", "prototype", "visual design", "branding", "creative",
        ),
        ("marketing", "technology", "product"),
        ("design", "designer", "ux", "ui", "creative"),
        ("product designer", "graphic designer", "ux designer", "ui designer"),
    ),
    "product": CategoryDef(
        "product",
        "Product Management",
        (
            "product manager", "product owner", "product lead", "product analyst",
            "product management", "head of product", "vp product", "product director",
        ),
        (
            "product strategy", "roadmap", "product discovery", "user research",
            "backlog", "product lifecycle", "product management", "stakeholder",
        ),
        ("technology", "design", "marketing", "data_ai"),
        ("product", "product manager", "product owner"),
        ("product manager", "product owner", "product analyst"),
    ),
    "admin": CategoryDef(
        "admin",
        "Administration & Office Support",
        (
            "administrative assistant", "executive assistant", "receptionist",
            "secretary", "data entry", "office assistant", "office administrator",
            "front desk", "clerical", "admin assistant", "personal assistant",
        ),
        (
            "administration", "scheduling", "filing", "office", "reception",
            "data entry", "calendar", "correspondence", "clerical",
        ),
        ("hr", "operations", "customer_support"),
        ("admin", "administrative", "assistant", "receptionist"),
        ("administrative assistant", "executive assistant", "receptionist", "data entry"),
    ),
    "other": CategoryDef(
        "other",
        "Other",
        (),
        (),
        (),
        (),
        (),
    ),
}

CATEGORY_ORDER: tuple[str, ...] = tuple(key for key in CATEGORIES if key != "other")

_STOPWORDS = {
    "a", "an", "the", "me", "my", "best", "top", "latest", "new", "some", "any",
    "jobs", "job", "roles", "role", "positions", "position", "vacancies", "vacancy",
    "openings", "opening", "work", "working", "opportunities", "opportunity",
    "entry", "level", "entry-level", "junior", "senior", "mid", "lead", "principal",
    "intern", "internship", "remote", "hybrid", "onsite", "on", "site", "office",
    "in", "near", "at", "for", "as", "of", "and", "or", "with", "find", "show",
    "search", "looking", "want", "need", "please", "based", "only", "full", "time",
    "part", "contract", "permanent", "temporary", "now", "today", "currently",
}

_SENIORITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("intern", re.compile(r"\b(intern|internship|trainee|placement)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr|entry[- ]level|entry level|graduate|fresher|associate)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr|experienced|specialist)\b", re.I)),
    ("lead", re.compile(r"\b(lead|principal|staff|head of|director|vp)\b", re.I)),
    ("manager", re.compile(r"\b(manager|management)\b", re.I)),
)

_EMPLOYMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("internship", re.compile(r"\b(internship|intern)\b", re.I)),
    ("part-time", re.compile(r"\bpart[- ]time\b", re.I)),
    ("contract", re.compile(r"\b(contract|contractor|freelance|temporary)\b", re.I)),
    ("full-time", re.compile(r"\bfull[- ]time\b", re.I)),
)


def category_def(key: str) -> CategoryDef:
    return CATEGORIES.get(key or "other", CATEGORIES["other"])


def category_options() -> list[dict[str, str]]:
    """Canonical categories for filter menus, ordered by label."""
    return [
        {"key": definition.key, "label": definition.label}
        for definition in sorted(CATEGORIES.values(), key=lambda item: item.label)
    ]


@lru_cache(maxsize=2048)
def _term_re(term: str) -> re.Pattern[str]:
    escaped = re.escape(term).replace(r"\ ", r"[\s\-]+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.I)


# Matching 587 category terms against every posting with one regex per term was
# the second big CPU sink (12s of a 400-posting search). Terms are plain words
# or short phrases, so we tokenise the text once and look up whole tokens and
# n-grams in a prebuilt index. Only terms with unusual punctuation (``c++``,
# ``.net developer``) still need a regex.
_TERM_WORD_RE = re.compile(r"[a-z0-9]+")
_PLAIN_TERM_RE = re.compile(r"[a-z0-9\s\-]+")


@dataclass(frozen=True)
class _TermRef:
    category: str
    kind: str  # "title" or "body"
    phrase: str  # normalised lookup key
    term: str  # original term, used for prefix bonuses


def _term_words(term: str) -> tuple[str, ...]:
    return tuple(_TERM_WORD_RE.findall((term or "").lower()))


def _build_term_index(
    categories: dict[str, CategoryDef],
) -> tuple[
    dict[str, tuple[_TermRef, ...]],
    set[str],
    int,
    tuple[tuple[_TermRef, re.Pattern[str]], ...],
]:
    refs: dict[str, list[_TermRef]] = {}
    seen: set[tuple[str, str, str]] = set()
    starters: set[str] = set()
    special: list[tuple[_TermRef, re.Pattern[str]]] = []
    max_words = 1

    def register(ref: _TermRef) -> bool:
        marker = (ref.category, ref.kind, ref.phrase)
        if marker in seen:
            # "Full stack" and "full-stack" are the same term written twice;
            # counting one posting twice for it would inflate the score.
            return False
        seen.add(marker)
        refs.setdefault(ref.phrase, []).append(ref)
        return True

    for key, definition in categories.items():
        for kind, terms in (
            ("title", definition.title_terms),
            ("body", definition.body_terms),
        ):
            for term in terms:
                cleaned = " ".join((term or "").lower().split())
                if not cleaned:
                    continue
                if not _PLAIN_TERM_RE.fullmatch(cleaned):
                    ref = _TermRef(key, kind, cleaned, cleaned)
                    if register(ref):
                        special.append((ref, _term_re(term)))
                    continue
                words = _term_words(cleaned)
                if not words:
                    continue
                phrase = " ".join(words)
                if register(_TermRef(key, kind, phrase, cleaned)) and len(words) > 1:
                    starters.add(words[0])
                    max_words = max(max_words, len(words))
    return (
        {phrase: tuple(items) for phrase, items in refs.items()},
        starters,
        max_words,
        tuple(special),
    )


_TERM_INDEX, _TERM_STARTERS, _TERM_MAX_WORDS, _SPECIAL_TERMS = _build_term_index(CATEGORIES)


def _matched_phrases(text: str) -> set[str]:
    lowered = (text or "").lower()
    if not lowered:
        return set()
    words = _TERM_WORD_RE.findall(lowered)
    total = len(words)
    found: set[str] = set()
    for position, word in enumerate(words):
        if word in _TERM_INDEX:
            found.add(word)
        if word not in _TERM_STARTERS:
            continue
        phrase = word
        for offset in range(1, _TERM_MAX_WORDS):
            following = position + offset
            if following >= total:
                break
            phrase = f"{phrase} {words[following]}"
            if phrase in _TERM_INDEX:
                found.add(phrase)
    for ref, pattern in _SPECIAL_TERMS:
        if ref.phrase not in found and pattern.search(lowered):
            found.add(ref.phrase)
    return found


def category_scores(
    title: str,
    description: str = "",
    tags: Iterable[str] | None = None,
    department: str = "",
) -> dict[str, float]:
    title_text = title or ""
    title_hits = _matched_phrases(title_text)
    department_hits = _matched_phrases(department) if department else set()
    title_norm = title_text.lower().strip()

    # Title and department matches are cheap to collect; body text is only
    # scanned for the four strongest title candidates.
    candidates: dict[str, float] = {}
    title_refs: dict[str, list[_TermRef]] = {}
    for phrase in title_hits:
        for ref in _TERM_INDEX.get(phrase, ()):
            if ref.kind == "title":
                title_refs.setdefault(ref.category, []).append(ref)
    for key, refs in title_refs.items():
        score = 0.0
        for ref in refs:
            score += 7.0
            if title_norm.startswith(ref.term):
                score += 2.0
        candidates[key] = score

    if department_hits:
        dept_categories = {
            ref.category
            for phrase in department_hits
            for ref in _TERM_INDEX.get(phrase, ())
            if ref.kind == "title"
        }
        for key in dept_categories:
            candidates[key] = candidates.get(key, 0.0) + 3.5

    if candidates:
        shortlist = {
            key
            for key, _value in sorted(candidates.items(), key=lambda item: item[1], reverse=True)[:4]
        }
    else:
        shortlist = {key for key in CATEGORIES if key != "other"}

    body_text = (
        f"{description[:6000] if description else ''} "
        f"{' '.join(tags or [])} {department or ''}"
    )
    body_counts: dict[str, int] = {}
    for phrase in _matched_phrases(body_text):
        for ref in _TERM_INDEX.get(phrase, ()):
            if ref.kind == "body":
                body_counts[ref.category] = body_counts.get(ref.category, 0) + 1

    scores: dict[str, float] = {}
    for key, definition in CATEGORIES.items():
        if key not in shortlist:
            continue
        score = candidates.get(key, 0.0)
        score += min(body_counts.get(key, 0), 5) * 0.8
        if score:
            scores[key] = round(score, 2)
    return scores


def classify_category(
    title: str,
    description: str = "",
    tags: Iterable[str] | None = None,
    department: str = "",
) -> tuple[str, float]:
    scores = category_scores(title, description, tags, department)
    if not scores:
        return "other", 0.0
    key = max(scores, key=lambda item: scores[item])
    score = scores[key]
    if score < 3.0:
        return "other", round(min(score / 3.0, 0.9), 2)
    ordered = sorted(scores.values(), reverse=True)
    confidence = score / (score + (ordered[1] if len(ordered) > 1 else 0.0) + 2.0)
    return key, round(min(0.99, max(0.2, confidence)), 2)


def detect_seniority(text: str) -> str:
    for key, pattern in _SENIORITY_PATTERNS:
        if pattern.search(text or ""):
            return key
    return ""


def detect_employment_type(text: str) -> str:
    for key, pattern in _EMPLOYMENT_PATTERNS:
        if pattern.search(text or ""):
            return key
    return ""


def detect_workplace(text: str) -> str:
    lowered = (text or "").lower()
    if re.search(r"\b(hybrid)\b", lowered):
        return "hybrid"
    if re.search(r"\b(on[- ]?site|in[- ]office|office[- ]based)\b", lowered):
        return "onsite"
    if re.search(r"\b(remote|work from home|wfh|anywhere)\b", lowered):
        return "remote"
    return ""


def _strip_stopwords(text: str) -> str:
    tokens = geo.normalize(text).split()
    kept = [token for token in tokens if token not in _STOPWORDS and len(token) > 1]
    return " ".join(kept)


@dataclass
class SearchIntent:
    raw_query: str = ""
    query: str = ""
    keywords: list[str] = field(default_factory=list)
    category: str = ""
    category_label: str = ""
    related_categories: list[str] = field(default_factory=list)
    seniority: str = ""
    workplace_type: str = ""
    employment_type: str = ""
    location_text: str = ""
    city: str = ""
    region: str = ""
    country: str = ""
    country_code: str = ""
    remote_only: bool = False
    search_terms: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_category(self) -> bool:
        return bool(self.category and self.category != "other")

    @property
    def has_location(self) -> bool:
        return bool(self.location_text)

    def describe(self) -> dict:
        return {
            "query": self.query,
            "category": self.category,
            "category_label": self.category_label,
            "related_categories": self.related_categories,
            "seniority": self.seniority,
            "workplace_type": self.workplace_type,
            "employment_type": self.employment_type,
            "location": self.location_text,
            "city": self.city,
            "region": self.region,
            "country": self.country,
            "search_terms": self.search_terms,
            "notes": self.notes,
        }


def parse_intent(
    query: str = "",
    location: str = "",
    *,
    category: str = "",
    workplace_type: str = "",
    seniority: str = "",
    employment_type: str = "",
    remote_only: bool = False,
) -> SearchIntent:
    """Interpret a query before sources are contacted."""
    raw_query = (query or "").strip()
    explicit_location = (location or "").strip()
    intent = SearchIntent(raw_query=raw_query)

    # Location can live in either the dedicated field or the query text.
    place = None
    if explicit_location:
        place = geo.find_location(explicit_location)
    if place is None and raw_query:
        place = geo.find_location(raw_query)
    if place is not None and place.kind != "remote":
        intent.location_text = explicit_location or place.label
        intent.city = place.city
        intent.region = place.region
        intent.country = place.country
        intent.country_code = place.country_code
        intent.notes.append(f"Location interpreted as {place.label}.")
        for token in {place.name, place.city, place.region, place.country}:
            if token:
                raw_query = re.sub(rf"(?i)\b{re.escape(token)}\b", " ", raw_query)
    elif explicit_location:
        intent.location_text = explicit_location
        intent.notes.append(f"Location “{explicit_location}” was not recognised; it will be matched textually.")

    if remote_only or re.search(r"\bremote\b", raw_query, re.I):
        intent.workplace_type = "remote"
        intent.remote_only = True
    else:
        intent.workplace_type = workplace_type or detect_workplace(raw_query)

    intent.seniority = seniority or detect_seniority(raw_query)
    intent.employment_type = employment_type or detect_employment_type(raw_query)

    cleaned = _strip_stopwords(raw_query)
    intent.query = cleaned
    intent.keywords = [token for token in cleaned.split() if len(token) > 2]

    explicit_category = (category or "").strip()
    if explicit_category:
        key = _canonical_category(explicit_category)
        intent.category = key
    else:
        key, confidence = classify_category(cleaned or raw_query)
        if key == "other":
            key = _category_from_synonym(cleaned)
        intent.category = key
    if intent.category and intent.category != "other":
        definition = category_def(intent.category)
        intent.category_label = definition.label
        intent.related_categories = list(definition.related)
        if not intent.query:
            intent.query = definition.search_terms[0]
        terms = [intent.query] if intent.query else []
        for term in definition.search_terms:
            if term and term.lower() not in {value.lower() for value in terms}:
                terms.append(term)
        intent.search_terms = terms[:5]
        intent.notes.append(f"Category interpreted as {definition.label}.")
    else:
        intent.search_terms = [intent.query] if intent.query else []
    if not intent.search_terms:
        intent.search_terms = [intent.query] if intent.query else []

    intent.workplace_type = intent.workplace_type or "any"
    return intent


def _canonical_category(value: str) -> str:
    normalized = value.strip().lower().replace("&", "and").replace("-", " ")
    for key, definition in CATEGORIES.items():
        if normalized in (key, definition.label.lower(), definition.label.lower().replace("&", "and")):
            return key
        if any(normalized == synonym for synonym in definition.synonyms):
            return key
    return "other"


def _category_from_synonym(text: str) -> str:
    best = ("other", 0)
    for key, definition in CATEGORIES.items():
        if key == "other":
            continue
        for synonym in (key.replace("_", " "), *definition.synonyms):
            if _term_re(synonym).search(text or ""):
                score = len(synonym)
                if score > best[1]:
                    best = (key, score)
    return best[0]


def category_of_job(
    title: str,
    description: str = "",
    tags: Iterable[str] | None = None,
    department: str = "",
) -> tuple[str, str, float]:
    key, confidence = classify_category(title, description, tags, department)
    return key, category_def(key).label, confidence


@dataclass
class Relevance:
    accepted: bool
    tier: str = "related"
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    category: str = "other"
    category_label: str = ""


def relevance_for_job(
    job,
    intent: SearchIntent,
    *,
    include_related: bool = True,
) -> Relevance:
    """Grade category/role/seniority fit.  Location is graded separately."""
    reasons: list[str] = []
    category = getattr(job, "category", "other") or "other"
    if category == "other" and not getattr(job, "category_label", ""):
        key, _confidence = classify_category(job.title, job.description)
        category = key
        job.category = key
        job.category_label = category_def(key).label
    definition = category_def(category)
    label = definition.label

    if intent.has_category:
        primary = intent.category
        if category == primary:
            tier_rank = 2
            reasons.append(f"Category match: {label}.")
        elif category in intent.related_categories or primary in definition.related:
            tier_rank = 1
            reasons.append(f"Related occupation: {label} → {intent.category_label}.")
        else:
            return Relevance(False, "mismatch", 0.0, [f"Different occupation: {label}."], category, label)
    else:
        tier_rank = 1

    title = geo.normalize(job.title or "")
    body = geo.normalize(job.description or "")[:6000]
    title_hits = 0
    body_hits = 0
    for term in [intent.query, *intent.keywords]:
        if not term:
            continue
        if geo._contains(title, term):
            title_hits += 1
        elif geo._contains(body, term):
            body_hits += 1
    if intent.query and not title_hits and not body_hits:
        reasons.append("The requested role terms do not appear; the category is the only match.")
        tier_rank = min(tier_rank, 1)
    elif title_hits:
        reasons.append("Role title matches the request.")
        tier_rank = 2
    elif body_hits:
        reasons.append("Role keywords appear in the description.")

    if intent.seniority:
        actual = (job.seniority or "unspecified").lower()
        wanted = intent.seniority
        if actual != "unspecified":
            if wanted in ("intern", "junior") and actual in ("senior", "lead", "manager"):
                return Relevance(False, "mismatch", 0.0, [f"Seniority mismatch: {actual} role for a {wanted} search."], category, label)
            if wanted == "senior" and actual in ("intern", "junior"):
                return Relevance(False, "mismatch", 0.0, [f"Seniority mismatch: {actual} role for a senior search."], category, label)
            if wanted == "lead" and actual in ("intern", "junior"):
                return Relevance(False, "mismatch", 0.0, [f"Seniority mismatch: {actual} role for a lead search."], category, label)
            reasons.append(f"Seniority: {actual}.")

    if intent.employment_type:
        actual_type = geo.normalize(job.employment_type or "")
        wanted_type = intent.employment_type.replace("-", " ")
        if actual_type and wanted_type and wanted_type not in actual_type:
            return Relevance(False, "mismatch", 0.0, [f"Employment type mismatch: {job.employment_type}."], category, label)

    if tier_rank == 2:
        tier = "exact"
    elif tier_rank == 1:
        tier = "related"
    else:
        tier = "unrelated"
    if tier == "related" and not include_related:
        return Relevance(False, tier, 0.0, reasons, category, label)
    if tier == "unrelated":
        return Relevance(False, tier, 0.0, reasons, category, label)

    score = 55.0 if tier == "exact" else 28.0
    score += min(25.0, title_hits * 12.0)
    score += min(10.0, body_hits * 4.0)
    if intent.seniority and (job.seniority or "").lower() == intent.seniority:
        score += 10.0
    if intent.employment_type and intent.employment_type in geo.normalize(job.employment_type or ""):
        score += 5.0
    if job.skills and intent.keywords:
        overlap = {skill.lower() for skill in job.skills} & set(intent.keywords)
        if overlap:
            score += min(10.0, len(overlap) * 3.0)
            reasons.append(f"Skill overlap: {', '.join(sorted(overlap)[:4])}.")
    return Relevance(True, tier, round(min(100.0, score), 1), reasons, category, label)


def generated_source_terms(intent: SearchIntent) -> list[str]:
    terms = list(intent.search_terms)
    if intent.query and intent.query not in terms:
        terms.insert(0, intent.query)
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        cleaned = term.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            result.append(cleaned)
    return result[:5]
