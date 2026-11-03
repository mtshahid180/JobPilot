"""HTML/text cleanup and structured extraction from job descriptions."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from functools import lru_cache

from jobpilot.core import locations as geo
from jobpilot.core.models import Job, RawJob
from jobpilot.pipelines import taxonomy

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"[ \t\r\f\v]+")
BLANK_RE = re.compile(r"\n{3,}")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")

SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "Python": ("python",),
    "JavaScript": ("javascript", "js"),
    "TypeScript": ("typescript", "ts"),
    "React": ("react.js", "reactjs", "react"),
    "Next.js": ("next.js", "nextjs"),
    "Node.js": ("node.js", "nodejs", "node"),
    "Vue": ("vue.js", "vuejs", "vue"),
    "Angular": ("angular",),
    "HTML": ("html5", "html"),
    "CSS": ("css3", "css"),
    "Tailwind": ("tailwindcss", "tailwind"),
    "Flask": ("flask",),
    "Django": ("django",),
    "FastAPI": ("fastapi",),
    "REST APIs": ("rest api", "restful", "rest apis"),
    "GraphQL": ("graphql",),
    "SQL": ("sql",),
    "PostgreSQL": ("postgresql", "postgres"),
    "MySQL": ("mysql",),
    "SQLite": ("sqlite",),
    "MongoDB": ("mongodb", "mongo"),
    "Redis": ("redis",),
    "Docker": ("docker",),
    "Kubernetes": ("kubernetes", "k8s"),
    "AWS": ("aws", "amazon web services"),
    "GCP": ("gcp", "google cloud"),
    "Azure": ("azure",),
    "Git": ("git", "github", "gitlab"),
    "Linux": ("linux", "unix"),
    "CI/CD": ("ci/cd", "continuous integration", "continuous delivery"),
    "Terraform": ("terraform",),
    "Machine Learning": ("machine learning", "ml "),
    "Deep Learning": ("deep learning",),
    "NLP": ("natural language processing", "nlp"),
    "Computer Vision": ("computer vision",),
    "PyTorch": ("pytorch",),
    "TensorFlow": ("tensorflow",),
    "scikit-learn": ("scikit-learn", "sklearn", "scikit learn"),
    "pandas": ("pandas",),
    "NumPy": ("numpy",),
    "LLMs": ("large language model", "llm", "llms"),
    "RAG": ("retrieval augmented generation", "rag "),
    "LangChain": ("langchain",),
    "MCP": ("model context protocol", "mcp"),
    "OpenAI": ("openai", "gpt-4", "gpt4"),
    "Prompt Engineering": ("prompt engineering",),
    "Data Analysis": ("data analysis", "data analytics"),
    "Data Engineering": ("data engineering",),
    "ETL": ("etl", "elt"),
    "Airflow": ("airflow",),
    "Spark": ("spark", "pyspark"),
    "Tableau": ("tableau",),
    "Power BI": ("power bi", "powerbi"),
    "Excel": ("excel",),
    "Java": ("java",),
    "C++": ("c++", "cpp"),
    "C#": ("c#", ".net"),
    "Go": ("golang", "go "),
    "Rust": ("rust",),
    "PHP": ("php",),
    "Laravel": ("laravel",),
    "Spring": ("spring boot", "spring"),
    "Selenium": ("selenium",),
    "Playwright": ("playwright",),
    "Agile": ("agile",),
    "Scrum": ("scrum",),
    "Figma": ("figma",),
    "Unity": ("unity",),
    "Excel VBA": ("vba",),
    "Salesforce": ("salesforce",),
    "Communication": ("communication",),
    "Leadership": ("leadership",),
    # --- finance, accounting & banking -----------------------------------
    "Financial Reporting": ("financial reporting", "financial statements"),
    "Financial Analysis": ("financial analysis", "financial modelling", "financial modeling"),
    "IFRS": ("ifrs",),
    "GAAP": ("gaap", "us gaap"),
    "Bookkeeping": ("bookkeeping", "book keeping"),
    "Accounts Payable": ("accounts payable", "accounts receivable", "ap/ar"),
    "Payroll": ("payroll",),
    "Taxation": ("taxation", "tax compliance", "tax returns"),
    "Auditing": ("auditing", "internal audit", "audit"),
    "Budgeting": ("budgeting", "budget management"),
    "Forecasting": ("forecasting", "forecast"),
    "Cash Flow": ("cash flow", "cashflow"),
    "Risk Management": ("risk management", "risk assessment"),
    "QuickBooks": ("quickbooks",),
    "SAP": ("sap", "sap erp"),
    "Oracle": ("oracle", "oracle erp"),
    "Tally": ("tally erp", "tally"),
    "Banking Operations": ("banking operations", "branch banking"),
    "Credit Analysis": ("credit analysis", "credit appraisal"),
    "KYC": ("kyc", "know your customer"),
    "AML": ("anti-money laundering", "aml"),
    # --- marketing, sales & support --------------------------------------
    "SEO": ("seo", "search engine optimization"),
    "Google Ads": ("google ads", "adwords", "ppc"),
    "Meta Ads": ("meta ads", "facebook ads"),
    "Content Marketing": ("content marketing", "copywriting"),
    "Email Marketing": ("email marketing", "mailchimp"),
    "CRM": ("crm", "hubspot"),
    "Market Research": ("market research",),
    "B2B Sales": ("b2b sales", "business development"),
    "Account Management": ("account management", "key account"),
    "Negotiation": ("negotiation",),
    "Customer Service": ("customer service", "customer support"),
    "Zendesk": ("zendesk", "freshdesk"),
    # --- people, operations & management ---------------------------------
    "Recruitment": ("recruitment", "talent acquisition", "hiring"),
    "Onboarding": ("onboarding",),
    "HRIS": ("hris", "workday", "bamboohr"),
    "Employee Relations": ("employee relations",),
    "Project Management": ("project management", "pmp"),
    "Operations Management": ("operations management", "supply chain"),
    "Inventory Management": ("inventory management", "warehouse"),
    "Procurement": ("procurement", "purchasing"),
    "Lean": ("lean", "six sigma"),
    # --- healthcare, education & legal -----------------------------------
    "Patient Care": ("patient care", "clinical"),
    "Medical Coding": ("medical coding", "icd-10"),
    "Nursing": ("nursing", "registered nurse"),
    "Curriculum Development": ("curriculum", "lesson planning"),
    "Teaching": ("teaching", "classroom"),
    "Contract Review": ("contract review", "contract drafting"),
    "Compliance": ("compliance", "regulatory"),
    "Litigation": ("litigation",),
    # --- design & product -------------------------------------------------
    "UI Design": ("ui design", "user interface"),
    "UX Design": ("ux design", "user experience", "ux research"),
    "Adobe Photoshop": ("photoshop",),
    "Adobe Illustrator": ("illustrator",),
    "Prototyping": ("prototyping", "wireframing"),
    "Product Management": ("product management", "roadmap"),
    "Jira": ("jira", "confluence"),
    "Data Visualization": ("data visualization", "data visualisation", "dashboards"),
}

SENIORITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("intern", re.compile(r"\b(intern|internship|trainee)\b", re.I)),
    ("junior", re.compile(r"\b(junior|entry[- ]level|graduate|fresh graduate|associate)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|experienced)\b", re.I)),
    ("lead", re.compile(r"\b(lead|principal|staff|head of)\b", re.I)),
    ("manager", re.compile(r"\b(engineering manager|manager|director)\b", re.I)),
]

REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|distributed team|anywhere)\b", re.I)
ONSITE_RE = re.compile(r"\b(on[- ]?site|in[- ]office|hybrid)\b", re.I)

SALARY_RE = re.compile(
    r"(?<![A-Za-z])(?:(?:PKR|Rs\.?|USD|\$|EUR|€|GBP|£)\s?\d[\d,.]*(?:\s?[kKmM])?"
    r"(?:\s?[-–to]+\s?(?:PKR|Rs\.?|USD|\$|EUR|€|GBP|£)?\s?\d[\d,.]*(?:\s?[kKmM])?)?"
    r"(?:\s?(?:per|/)\s?(?:year|annum|month|hour))?)",
    re.I,
)


def strip_html(value: str) -> str:
    if not value:
        return ""
    # Some boards (notably Greenhouse) HTML-escape their markup, so unescape
    # before removing tags or the tags leak into the description as text.
    for _ in range(2):
        text = TAG_RE.sub(" ", html.unescape(value))
        if text == value:
            break
        value = text
    text = value.replace("\xa0", " ")
    text = SPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = BLANK_RE.sub("\n\n", text)
    return text.strip()


def clean_title(title: str) -> str:
    title = strip_html(title)
    title = re.sub(r"\s*[\(\[][^)\]]*(remote|hybrid|onsite|on-site)[^)\]]*[\)\]]", "", title, flags=re.I)
    title = re.sub(r"\s*[-–—|]\s*(remote|hybrid|on[- ]?site)\b.*$", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" -–|")
    return title[:160]


def detect_seniority(title: str, description: str = "") -> str:
    for label, pattern in SENIORITY_PATTERNS:
        if pattern.search(title or ""):
            return label
    years = re.search(r"(\d+)\+?\s*(?:years?|yrs?)", description, re.I)
    if years:
        count = int(years.group(1))
        if count <= 1:
            return "junior"
        if count <= 3:
            return "mid"
        if count <= 6:
            return "senior"
        return "lead"
    return "mid" if description else "unspecified"


def detect_remote(title: str, location: str, description: str, flagged: bool | None) -> bool:
    if flagged is not None:
        return flagged
    haystack = f"{title} {location}"
    if REMOTE_RE.search(haystack):
        return True
    if ONSITE_RE.search(haystack):
        return False
    return bool(REMOTE_RE.search(description[:800])) and not ONSITE_RE.search(haystack)


def detect_salary(description: str) -> str:
    match = SALARY_RE.search(strip_html(description))
    return match.group(0).strip() if match else ""


@lru_cache(maxsize=512)
def _alias_pattern(alias: str) -> re.Pattern[str]:
    alias = alias.strip()
    if alias.endswith(" "):
        return re.compile(rf"(?<![a-z0-9]){re.escape(alias)}")
    return re.compile(rf"(?<![a-z0-9+#.]){re.escape(alias)}(?![a-z0-9+#])")


# Scanning a 12k-character description once per alias took ~70s of CPU for a
# single 450-posting search (359 aliases × every posting). Tokenising once and
# looking up whole tokens and short phrases in a prebuilt index is orders of
# magnitude cheaper; only aliases with unusual punctuation still need a regex.
# The token pattern keeps ``+``, ``#``, ``/``, ``-`` and internal dots so that
# ``c++``, ``c#``, ``ci/cd``, ``node.js`` and ``scikit-learn`` stay whole,
# while a dot only continues a token when a letter follows it.
_WORD_RE = re.compile(r"[a-z0-9](?:[a-z0-9+#/-]|\.(?=[a-z0-9+#/-]))*")


def _build_skill_index(
    aliases: dict[str, tuple[str, ...]],
) -> tuple[dict[str, str], set[str], int, tuple[tuple[str, re.Pattern[str]], ...]]:
    index: dict[str, str] = {}
    starters: set[str] = set()
    special: list[tuple[str, re.Pattern[str]]] = []
    max_words = 1
    for skill, extra in aliases.items():
        for alias in (skill.lower(), *extra):
            alias = alias.strip().lower()
            if not alias:
                continue
            parts = alias.split()
            if parts and all(_WORD_RE.fullmatch(part) for part in parts):
                index.setdefault(alias, skill)
                if len(parts) > 1:
                    starters.add(parts[0])
                    max_words = max(max_words, len(parts))
            else:
                special.append((skill, _alias_pattern(alias)))
    return index, starters, max_words, tuple(special)


_SKILL_INDEX, _SKILL_STARTERS, _SKILL_MAX_WORDS, _SKILL_SPECIAL = _build_skill_index(
    SKILL_ALIASES
)


def extract_skills(text: str, limit: int = 18) -> list[str]:
    lowered = strip_html(text)[:12000].lower()
    if not lowered:
        return []
    words = _WORD_RE.findall(lowered)
    total = len(words)
    matched: set[str] = set()
    for position, word in enumerate(words):
        skill = _SKILL_INDEX.get(word)
        if skill is not None:
            matched.add(skill)
        if word not in _SKILL_STARTERS:
            continue
        phrase = word
        for offset in range(1, _SKILL_MAX_WORDS):
            following = position + offset
            if following >= total:
                break
            phrase = f"{phrase} {words[following]}"
            skill = _SKILL_INDEX.get(phrase)
            if skill is not None:
                matched.add(skill)
    for skill, pattern in _SKILL_SPECIAL:
        if skill not in matched and pattern.search(lowered):
            matched.add(skill)
    if not matched:
        return []
    # Preserve the declared order so callers see a stable, curated list.
    return [skill for skill in SKILL_ALIASES if skill in matched][:limit]


def extract_links(text: str, limit: int = 5) -> list[str]:
    seen: list[str] = []
    for match in URL_RE.findall(text or ""):
        cleaned = match.rstrip(".,;)")
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return seen


def fingerprint(title: str, company: str, location: str, url: str = "") -> str:
    basis = "|".join(
        [
            re.sub(r"[^a-z0-9]", "", (title or "").lower())[:80],
            re.sub(r"[^a-z0-9]", "", (company or "").lower())[:80],
            re.sub(r"[^a-z0-9]", "", (location or "").lower())[:40],
            (url or "").split("?")[0].rstrip("/").lower()[-120:],
        ]
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _parse_date(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        for parser in (
            lambda v: datetime.fromisoformat(v),
            lambda v: datetime.strptime(v, "%a, %d %b %Y %H:%M:%S %z"),
            lambda v: datetime.strptime(v, "%Y-%m-%d"),
        ):
            try:
                parsed = parser(text)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except ValueError:
                continue
    return None


def extract_job(raw: RawJob) -> Job:
    description = strip_html(raw.description)
    title = clean_title(raw.title)
    tag_skills = extract_skills(" , ".join(raw.tags), limit=12) if raw.tags else []
    description_skills = extract_skills(f"{title} {description}")
    skills = list(dict.fromkeys([*tag_skills, *description_skills]))[:24]

    department = raw.department or str(raw.raw.get("department") or "")
    category, category_label, _confidence = taxonomy.category_of_job(
        title, description, raw.tags, department
    )

    workplace = raw.workplace_type
    if workplace == "unknown":
        inferred = geo.detect_workplace_type(
            title,
            raw.job_location or raw.location,
            description,
            remote_flag=raw.remote,
        )
        workplace = inferred
    info = geo.infer_location(
        job_location=raw.job_location or raw.location,
        company_location=raw.company_location,
        title=title,
        description=description,
        remote_flag=workplace in ("remote", "hybrid") or raw.remote,
        workplace_hint=workplace if workplace != "unknown" else "",
        eligible_hint=raw.eligible_locations,
    )
    if raw.remote_scope != "unknown" and not raw.eligible_locations:
        info.remote_scope = raw.remote_scope
    if raw.location_confidence != "unknown" and not info.eligible_locations:
        info.location_confidence = raw.location_confidence
    if raw.eligible_locations and not info.eligible_locations:
        info.eligible_locations = list(raw.eligible_locations)
    if info.workplace_type == "unknown" and workplace != "unknown":
        info.workplace_type = workplace

    posted_at = _parse_date(raw.posted_at)
    expires_at = _parse_date(raw.expires_at)
    now = datetime.now(UTC)
    if expires_at and expires_at < now:
        availability = "expired"
    elif posted_at and (now - posted_at).days > 60:
        availability = "stale"
    elif posted_at:
        availability = "active"
    else:
        availability = "unknown"

    seniority_hint = raw.seniority_hint or str(raw.raw.get("seniority") or "")
    seniority = _normalise_seniority(seniority_hint) or detect_seniority(title, description)

    display_location = info.job_location
    if not display_location and info.workplace_type in ("remote", "hybrid"):
        display_location = geo.describe_remote(info)

    return Job(
        source=raw.source,
        external_id=raw.external_id,
        title=title,
        company=strip_html(raw.company) or "Unknown",
        location=strip_html(display_location),
        description=description,
        url=raw.url,
        salary=raw.salary or detect_salary(description),
        employment_type=raw.employment_type,
        remote=info.workplace_type in ("remote", "hybrid"),
        seniority=seniority,
        skills=skills,
        posted_at=posted_at,
        expires_at=expires_at,
        fingerprint=fingerprint(title, raw.company, raw.job_location or raw.location, raw.url),
        raw=raw.raw,
        category=category,
        category_label=category_label,
        workplace_type=info.workplace_type,
        company_location=info.company_location,
        job_location=info.job_location,
        eligible_locations=info.eligible_locations[:12],
        remote_scope=info.remote_scope,
        location_confidence=info.location_confidence,
        location_restrictions=info.restrictions[:12],
        availability=availability,
        last_seen_at=now,
    )


def _normalise_seniority(value: str) -> str:
    text = (value or "").lower()
    if not text:
        return ""
    if re.search(r"\b(intern|internship|trainee|placement)\b", text):
        return "intern"
    if re.search(r"\b(junior|jr|entry|graduate|fresher|associate)\b", text):
        return "junior"
    if re.search(r"\b(manager|management|director|head|vp|chief|c-level|executive)\b", text):
        return "manager"
    if re.search(r"\b(lead|principal|staff)\b", text):
        return "lead"
    if re.search(r"\b(senior|sr|experienced|specialist)\b", text):
        return "senior"
    if re.search(r"\b(mid|intermediate)\b", text):
        return "mid"
    return ""
