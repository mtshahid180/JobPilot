# JobPilot

**An MCP-powered job-search agent with a production-style harness, persistent memory, and verified CV tailoring.**

JobPilot finds jobs from free sources, scores them against your real profile, tailors a CV and cover letter without inventing anything, and tracks every application. It remembers your preferences and decisions across sessions, and it never takes an external action without your approval.

The whole product runs locally with **no API key required**. Optional OpenAI-compatible models can be enabled through one guarded gateway. Each person who signs up gets a **private workspace** — profile, jobs, applications, memories, drafts, runs, approvals and budget are isolated per account.

---

## Why this project exists

Searching for a job is a full-time job: new postings every day, a CV to adjust for each one, applications to track, follow-ups to remember. JobPilot automates the repetitive parts while keeping a human in control of everything that leaves the machine. It is not limited to one industry or one city: ask for a finance role in Karachi, nursing in Toronto, or remote support open to Pakistan.

It also demonstrates three things AI engineering roles ask for:

| Pillar | What it shows |
| --- | --- |
| **MCP** | Capabilities exposed as separate typed servers instead of hard-coded functions |
| **Harness** | The runtime around the model: loop control, permissions, guardrails, budget, tracing, evals |
| **Context & memory** | Deliberate control of what the model sees and what the system remembers between sessions |

---

## What it does

- **Searches live job sources** — Mustakbil (Pakistan), Himalayas, The Muse, Jobicy, Remotive, Arbeitnow, RemoteOK, Greenhouse and Workable public APIs, RSS feeds, Adzuna (optional key), and manual URL/text imports. Indeed and LinkedIn remain disabled by policy. Every enabled source is contacted in parallel on each search; nothing stops early because "enough" results arrived.
- **One pipeline for the agent and the job board** — free text is parsed into category, location, workplace, seniority and employment constraints, then used to build per-source queries. Results are normalised, deduplicated, filtered, graded and ranked by the same service whether the request came from the UI or the agent.
- **Real geography, not substring matching** — a listing carries workplace type, job location, company location and *eligible* locations separately. An on-site role in Lahore is not a match for Karachi; a remote role open only to Pakistan is not a match for Toronto; remote eligibility that no source states is labelled **unverified**, never verified.
- **Covers more than tech** — finance, accounting, banking, marketing, sales, HR, customer support, operations, management, healthcare, education, engineering, legal, design, product and admin, each with its own titles, body terms and related-occupation graph. Unrelated occupations are rejected rather than blended in, and related ones are labelled.
- **Resume-assisted profiles** — upload a PDF, DOCX, TXT or Markdown resume; JobPilot extracts what is literally present, shows every field for review, and merges only what you tick. Files are stored per account and can be replaced or deleted.
- **Isolates every account** — email/password signup with hashed passwords. Every job, application, memory, draft, run, approval and budget row is scoped to the signed-in user.
- **Scores every role against your profile** — a transparent rubric combining skills, target role, semantic similarity, location, seniority, and recency, with a per-factor breakdown and matched/missing skills.
- **Tailors a verified CV** — bullets are selected from real profile experience. Every generated claim is checked against profile evidence; unsupported claims are rejected, not softened.
- **Drafts a cover letter** — grounded in the same verified facts, with company-specific context.
- **Tracks applications** — statuses, notes, history, follow-up dates, and a dashboard. Nothing is submitted externally.
- **Remembers what matters** — preferences, rules, and past decisions are proposed by the agent and confirmed by you. Near-duplicates merge; contradictions are flagged.
- **Shows its work** — every agent run writes a JSONL trace with steps, tool calls, arguments, latency, tokens, and cost. Runs and approvals are inspectable in the UI.
- **Runs offline first** — deterministic planner, local hashing embeddings, and template-based tailoring all work with zero keys and zero cost.

---

## The interface

Nine workspace pages plus a focused first-run setup screen, built with Flask, Jinja2, and vanilla CSS/JS. Dark-first with a light theme, a command palette, keyboard shortcuts, and no Node build step.

| Page | What you can do |
| --- | --- |
| **Sign up / Sign in** | Create a private account, sign in, and sign out |
| **Setup** | First-run onboarding per account: add basics, targets and skills, paste an existing CV, or upload a resume file for review |
| **Dashboard** | Pipeline health, top matches, follow-ups, budget, recent runs, pending approvals |
| **Jobs** | Search every enabled source at once, filter by category, workplace, seniority and recency, page through results, inspect eligibility and scoring, save, import, tailor |
| **Agent** | Chat with the harness, watch tool steps and a real activity heartbeat, approve or reject external actions |
| **Profile** | Edit structured profile data, upload and review a resume, paste and parse a CV, review claim evidence |
| **Applications** | Kanban-style tracker with statuses, notes, and follow-ups |
| **Memory** | Review pending memories, approve, delete, and inspect provenance |
| **Drafts** | Verified CVs and cover letters with claim checks and copy/download |
| **Runs** | Browse traces, steps, tokens, cost, and per-run timelines |
| **Settings** | Runtime status, source health, MCP servers, budget, tool catalog, account and password |

---

## Quick start

### Prerequisites

- Python **3.11+** (tested on 3.12.10)
- No Node.js, database server, or API key required

### Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]"

Copy-Item .env.example .env
jobpilot doctor
jobpilot init
jobpilot serve
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e ".[dev]"

cp .env.example .env
jobpilot doctor
jobpilot init
jobpilot serve
```

Open **http://127.0.0.1:5000**. Create an account on the signup screen, then the setup screen opens for that account: add your name, targets and skills, or paste an existing CV to prefill them. `jobpilot init` creates an empty database and does not load sample data. To wipe everything and start over, run `jobpilot reset --yes`.

### Accounts and data isolation

- Passwords are salted and hashed with Werkzeug; plaintext is never stored.
- Each account owns its profile, jobs, applications, memories, CV drafts, cover letters, chat sessions, runs, approvals and LLM budget.
- The web UI binds the signed-in account for every request. There is no shared or global workspace in the browser.
- Sessions last 30 days by default and use `HttpOnly` + `SameSite=Lax` cookies. Set `DEBUG=0` behind TLS to enable `Secure` cookies.
- The CLI and MCP servers are headless: bind them to an account with `JOBPILOT_USER_EMAIL` (or `JOBPILOT_USER_ID`). With neither set they use the legacy `local` workspace. Rows migrated from a pre-account database live in that workspace and are not shown to web accounts.

```powershell
$env:JOBPILOT_USER_EMAIL = "you@example.com"
jobpilot search "AI engineer" --remote
```

### Optional: enable a real model

Set an OpenAI-compatible key in `.env`:

```dotenv
OPENAI_API_KEY=sk-...
JOBS_FAST_MODEL=gpt-4o-mini
JOBS_STRONG_MODEL=gpt-4o-mini
EMBEDDING_PROVIDER=local
```

The gateway adds response caching, retries with backoff, token accounting, and hard total and daily budget caps. If a call fails or the budget is exhausted, the agent falls back to the deterministic local planner and says so in the trace.

### Docker (optional)

Requires Docker Desktop with Compose v2.24+. Docker is not required for local development.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open **http://127.0.0.1:5055**. The compose service binds to localhost only, runs Flask without debug mode, and persists `data/` and `traces/` through bind mounts. Put `OPENAI_API_KEY` in `.env` before starting if you want the LLM gateway. Change `SECRET_KEY` in `.env` before exposing the app beyond your machine.

```powershell
docker compose logs -f jobpilot   # follow logs
docker compose down               # stop the service
```

---

## CLI

```bash
jobpilot doctor          # runtime, database, profile, budget, tools, sources, MCP servers
jobpilot init            # create an empty local database and workspace directories
jobpilot search "AI engineer" --remote --limit 15 [--json]
jobpilot chat            # interactive agent console (/exit, /new, /mode local|auto)
jobpilot serve [--port 5000] [--debug]
jobpilot eval [--k 5] [--json]
jobpilot reset [--yes]   # delete the database and traces, then reinitialize
```

`jobpilot` is installed as an editable console script. You can also run modules directly, for example `python -m jobpilot.ui.cli doctor`.

---

## MCP servers

Each server does one job, speaks MCP over stdio, and reuses the same service layer as the web UI and agent loop.

| Server | Tools | Responsibility |
| --- | --- | --- |
| `jobs` | `search_jobs`, `get_job`, `refresh_jobs`, `add_job_from_url` | Discovery, imports, full descriptions |
| `profile` | `get_profile`, `search_experience`, `list_skills` | Single source of truth for CV facts |
| `memory` | `remember`, `recall`, `forget`, `list_memories` | Durable preferences, rules, and episodes |
| `match` | `score_job`, `rank_jobs`, `skill_gap` | Rubric scoring and gap analysis |
| `tailor` | `tailor_cv`, `draft_cover_letter` | Grounded, verified generation |
| `tracker` | `add_application`, `update_status`, `list_applications`, `list_followups` | Local application tracking |

Run any server directly:

```bash
python -m jobpilot.servers.jobs_server
python -m jobpilot.servers.profile_server
python -m jobpilot.servers.memory_server
python -m jobpilot.servers.match_server
python -m jobpilot.servers.tailor_server
python -m jobpilot.servers.tracker_server
```

Example MCP client configuration (replace the interpreter path):

```json
{
  "mcpServers": {
    "jobpilot-jobs": {
      "command": "C:\\path\\to\\Jobpilot Project\\.venv\\Scripts\\python.exe",
      "args": ["-m", "jobpilot.servers.jobs_server"]
    },
    "jobpilot-memory": {
      "command": "C:\\path\\to\\Jobpilot Project\\.venv\\Scripts\\python.exe",
      "args": ["-m", "jobpilot.servers.memory_server"]
    }
  }
}
```

The harness itself calls the same typed registry in-process, so the UI stays fast and does not need to spawn subprocesses. The MCP servers are the external integration surface and the compatibility shim supports both MCP SDK v1 (`FastMCP`) and v2 (`MCPServer`); this project is tested with `mcp 2.3.0`.

MCP servers run headless. Add `JOBPILOT_USER_EMAIL` to their environment so tools read and write your account instead of the legacy `local` workspace:

```json
{
  "mcpServers": {
    "jobpilot-jobs": {
      "command": "C:\\path\\to\\Jobpilot Project\\.venv\\Scripts\\python.exe",
      "args": ["-m", "jobpilot.servers.jobs_server"],
      "env": { "JOBPILOT_USER_EMAIL": "you@example.com" }
    }
  }
}
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  UI: Flask pages + JSON API        CLI: doctor/search/chat/eval  │
│  Approvals, memory review, traces live here                      │
└───────────────────────────────┬──────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│  HARNESS                                                         │
│  ┌──────────────┐ ┌────────────────┐ ┌────────────────────────┐  │
│  │ Agent loop   │ │ Context        │ │ Safety                 │  │
│  │ plan→act→    │ │ budgets,       │ │ permissions, claim     │  │
│  │ observe      │ │ compaction     │ │ verifier, injection    │  │
│  └──────────────┘ └────────────────┘ │ defense, approvals     │  │
│  ┌──────────────┐ ┌────────────────┐ └────────────────────────┘  │
│  │ LLM gateway  │ │ Tracing        │                             │
│  │ cache, $cap  │ │ JSONL per run  │                             │
│  └──────────────┘ └────────────────┘                             │
└──────┬───────────┬──────────┬──────────┬──────────┬──────────────┘
       │ typed tool registry + MCP stdio servers
 ┌─────▼───┐ ┌─────▼────┐ ┌────▼────┐ ┌───▼────┐ ┌───▼─────┐ ┌────▼────┐
 │ jobs    │ │ profile  │ │ memory  │ │ match  │ │ tailor  │ │ tracker │
 └─────┬───┘ └─────┬────┘ └────┬────┘ └───┬────┘ └───┬─────┘ └────┬────┘
       │           └───────────┴──────────┴──────────┴────────────┘
 ┌─────▼──────────┐                              ┌────────────────────┐
 │ Source adapters│                              │ SQLite             │
 │ fallback chains│                              │ jobs, profile,     │
 │ probe + fetch  │                              │ memory, tracker,   │
 └────────────────┘                              │ runs, traces       │
                                                 └────────────────────┘
```

### How a run works

1. The context manager assembles a budgeted prompt: system + tools, pinned rules, recalled memories, a rolling summary, recent turns, and compact tool results.
2. The loop plans and calls typed tools. Every argument is validated against its JSON schema.
3. Read and local-write tools run immediately and are logged. Confirmation and external tools pause for a human decision.
4. Job descriptions and other scraped text are wrapped as untrusted data and scanned for injection before the model sees them.
5. Generated CV and cover-letter claims are checked against profile evidence. Unsupported claims are dropped.
6. The run, its steps, and a JSONL trace are stored. Artifacts are persisted in SQLite.

Without a key the loop uses a deterministic planner that exercises the same tools, permissions, and traces. With a key it switches to native LLM tool calling and falls back safely on errors or budget exhaustion.

---

## Safety model

| Tier | Examples | Behavior |
| --- | --- | --- |
| `read` | `search_jobs`, `recall`, `score_job` | Runs automatically |
| `write_local` | `tailor_cv`, `add_application`, `remember` | Runs automatically, logged |
| `confirmation` | `forget` | Needs your confirmation |
| `external` | `request_submission` | Always needs explicit approval |

Additional guarantees:

- **No auto-submission.** JobPilot records intent; it does not send applications, email, or messages.
- **Claim verifier.** Every tailored bullet is checked against profile evidence. The eval target is zero unsupported claims.
- **Prompt-injection defense.** Untrusted job text is quoted, scanned, and never treated as instructions.
- **Schema validation** on every tool input.
- **Hard budget caps** on total and daily spend.
- **LinkedIn is disabled.** LinkedIn has no public jobs API and automated scraping violates its terms, so the adapter never fetches data. Paste a LinkedIn job URL into **Import a job** to track it manually.
- **Indeed is disabled by policy.** Indeed retired its public Job Search API and prohibits scraping, so the adapter never fetches jobs.
- **Full traces.** Every run is inspectable and exportable, including tool arguments and results.
- **Per-user authorization.** Every service query is scoped by `user_id`; unauthenticated HTML requests redirect to sign-in and API requests return `401`.
- **Secure defaults.** Passwords are hashed, session cookies are `HttpOnly` and `SameSite=Lax`, and CSRF tokens protect account forms. Change `SECRET_KEY` before exposing the app beyond your machine.

See [`docs/safety.md`](docs/safety.md) for the detailed threat model.

---

## Evaluation

The offline suite covers ranking quality, grounded generation, planner accuracy, memory recall, injection defense, context compaction, latency, and cost. It runs with zero API calls.

```bash
jobpilot eval
# or
python -m eval.run_eval --k 5
```

Current local-mode results (37 labeled postings, 16 planner tasks, 8 memory tasks, 11 injection probes):

| Area | Result |
| --- | --- |
| Ranking | Spearman **0.810**, nDCG@5 **0.925**, P@5 **1.000** |
| Baseline (TF-IDF) | Spearman 0.807, nDCG@5 0.878, P@5 1.000 |
| Grounded CVs | **3/3 verified**, **0** unsupported claims |
| Verifier challenge set | 8 claims, **0 false accepts**, 0 false rejects |
| Planner | Tool selection **100%**, intent recall **100%** |
| Memory | Recall@3 **100%** vs **75%** keyword ablation |
| Injection defense | **100%** detection, **0** false positives |
| Quality gates | **All PASS** |

The full generated report lives at [`eval/eval-report.md`](eval/eval-report.md); methodology is in [`docs/evaluation.md`](docs/evaluation.md).

---

## Configuration

Copy `.env.example` to `.env`. Every value is optional.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | dev value | Flask session signing; change in any shared environment |
| `DEBUG` | `1` | Flask debug mode and reloader |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Bind address for `jobpilot serve` |
| `DATA_DIR` / `TRACE_DIR` | `./data` / `./traces` | SQLite database and JSONL traces |
| `DB_NAME` | `jobpilot.sqlite3` | Database filename |
| `OPENAI_API_KEY` | empty | Enables the LLM gateway; empty means local-only |
| `OPENAI_BASE_URL` | OpenAI API | Any OpenAI-compatible endpoint |
| `JOBS_FAST_MODEL` / `JOBS_STRONG_MODEL` | `gpt-4o-mini` | Cheap vs strong model routing |
| `EMBEDDING_PROVIDER` | `local` | `local` (hashing) or `openai` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Used only when provider is `openai` |
| `MAX_BUDGET_USD` | `1.50` | Hard total budget |
| `DAILY_BUDGET_USD` | `0.50` | Hard daily budget |
| `LLM_TIMEOUT_SECONDS` | `60` | Per-call timeout |
| `LLM_MAX_RETRIES` | `3` | Retry attempts with backoff |
| `MAX_STEPS` | `12` | Agent steps per run |
| `MAX_CONSECUTIVE_FAILURES` | `3` | Failure circuit breaker |
| `CONTEXT_TOKEN_BUDGET` | `6000` | Context assembly ceiling |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | empty | Optional Adzuna source |
| `ADZUNA_COUNTRY` | `gb` | Adzuna market |
| `ENABLE_GREENHOUSE` | `1` | Enable the free Greenhouse board API |
| `GREENHOUSE_BOARDS` | `figma,vercel,gitlab,dropbox` | Comma-separated company board tokens |
| `ENABLE_WORKABLE` | `1` | Enable the free Workable account widget API |
| `WORKABLE_ACCOUNTS` | `devsinc-17,userwise-services,prime-system` | Comma-separated Workable account slugs |
| `ENABLE_INDEED` | `0` | Accepted for compatibility; Indeed stays disabled by policy |
| `ENABLE_MUSTAKBIL` | `1` | Pakistan's public job board (server-rendered listing pages) |
| `ENABLE_HIMALAYAS` | `1` | Himalayas remote-job API |
| `ENABLE_THEMUSE` | `1` | The Muse public jobs API |
| `ENABLE_JOBICY` | `1` | Jobicy remote-job API |
| `ENABLE_RSS_SOURCES` | `1` | Enable RSS adapters |
| `RSS_FEEDS` | We Work Remotely | Comma-separated feed URLs |
| `ENABLE_LINKEDIN` | `0` | Kept for compatibility; LinkedIn has no supported jobs API and never fetches data |
| `HTTP_TIMEOUT_SECONDS` | `20` | Per-request source timeout |
| `SOURCE_BUDGET_SECONDS` | `45` | Wall-clock budget for one live search; slow sources are reported as `timeout` |
| `SEARCH_CACHE_TTL_SECONDS` | `300` | Repeat searches are served from cache; pages are sliced without re-fetching |
| `MAX_RESUME_MB` | `10` | Maximum resume upload size |

---

## Project layout

```
Jobpilot Project/
├── pyproject.toml
├── README.md
├── LICENSE
├── Dockerfile
├── compose.yaml
├── .dockerignore
├── .env.example
├── docs/                     # architecture, API, safety, evaluation, development
├── eval/                     # dataset, metrics, baselines, report
│   └── dataset/              # 37 labeled jobs + task/probe files
├── src/jobpilot/
│   ├── core/                 # settings, identity context, models, SQLite schema
│   ├── harness/              # loop, context, gateway, permissions, guardrails, tracing
│   ├── memory/               # store, write/read policy, summarizer
│   ├── pipelines/            # extract, dedupe, embedding
│   ├── sources/              # adapters, registry, doctor
│   ├── services/             # jobs, profile, match, tailor, tracker, memory, tools
│   ├── servers/              # six MCP servers + SDK compatibility shim
│   └── ui/                   # Flask app, CLI, templates, vanilla JS/CSS
└── tests/                    # isolated tests, no network
```

---

## Development

```bash
# Tests (isolated temp DB and traces, network disabled)
.venv\Scripts\python.exe -m pytest -q

# Coverage
.venv\Scripts\python.exe -m pytest --cov=jobpilot --cov-report=term-missing

# Evaluation with a JSON report
.venv\Scripts\python.exe -m eval.run_eval --json
```

See [`docs/development.md`](docs/development.md) for how to add a source adapter, a tool, or an MCP server.

---

## Design decisions and limitations

- **Local hashing embeddings, not a neural model.** Deliberate for zero cost and deterministic tests. Set `EMBEDDING_PROVIDER=openai` for better semantics; the interface is identical.
- **The deterministic planner is the default.** It is narrow but predictable: it covers search, ranking, tailoring, tracking, memory, and source checks. With a key, the LLM handles broader phrasing.
- **SQLite with application-enforced isolation.** One file keeps the app easy to inspect; every user-owned row is scoped by `user_id`. Logical isolation, not one database per account.
- **Source coverage depends on public APIs.** `jobpilot doctor` probes each source and reports latency and errors. Each search has a 45-second budget: a source that has not answered by then is reported as `timeout` and the rest of the results are still returned, with the failure visible in the UI.
- **Caching is in-process and per query.** A repeat search inside five minutes is served from memory, and paging slices the cached ranked list instead of contacting sources again. It is not a persistent index, so restarting the process starts cold.
- **Resume parsing is text-based.** PDFs are read with `pypdf` and DOCX with the standard library. Scanned image PDFs need OCR, which is not installed: they fail with an explicit message rather than silently producing an empty profile.
- **No Playwright/browser adapters yet.** Manual import and RSS cover the gap. LinkedIn remains off.
- **Docker is optional and unverified here.** The Dockerfile and Compose file are provided, but this environment has no Docker daemon, so the container path was not executed; the local virtualenv is the tested path.

---

## Roadmap

- [x] Dockerfile and Compose setup for reproducible one-command startup
- [ ] Optional Playwright adapter for public career pages, rate-limited and opt-in
- [ ] Calendar/ICS export for follow-ups
- [ ] `sqlite-vec` or provider-side vector index for larger memory stores
- [ ] Profile versioning and import/export
- [ ] Account hardening for public deployment: email verification, password reset, and per-IP rate limiting
- [ ] More labeled data and a held-out eval split

---

## License

MIT. See [`LICENSE`](LICENSE).

JobPilot is a personal job-search assistant, not an automated application bot. You remain responsible for the accuracy of your CV, the terms of the sources you use, and every application you choose to submit.
