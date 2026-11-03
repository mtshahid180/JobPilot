# JobPilot

**An MCP-powered job search agent with a production-style harness and
persistent memory.**

JobPilot finds jobs from free sources, scores them against your real
profile, tailors your CV without inventing anything, and tracks every
application. It remembers your preferences and past decisions across
sessions, and it never takes an external action without your approval.

> **Archived design brief.** This is the original product brief, kept for
> history. It predates multi-account support, the current free source set,
> the multi-industry/multi-market search pipeline, resume upload, and the
> Flask UI's account/setup flow. See [README.md](README.md) for the source
> of truth on setup, architecture, commands, and capabilities.
> Sections marked *(planned)* are historical and may not reflect the
> shipped app.
>
> Details below that are specifically **out of date**:
>
> - Sources are **not** an ordered fallback chain. Every enabled adapter is
>   queried in parallel on each search and results are merged; `doctor`
>   reports per-source health.
> - The current sources are Mustakbil, Himalayas, The Muse, Jobicy,
>   Remotive, Arbeitnow, RemoteOK, Greenhouse, Workable, RSS, Adzuna
>   (optional key) and manual import. There is no Rozee or Playwright
>   adapter.
> - Search is not limited to engineering roles or one city; see
>   [docs/architecture.md](docs/architecture.md).
> - The Flask UI is shipped, not pending. Run `jobpilot serve`.

------------------------------------------------------------------------

## Why this project exists

Searching for an entry-level AI Engineer role is a full-time job: new
postings daily, a CV to adjust for each one, applications to track,
follow-ups to remember. JobPilot automates the repetitive parts and
keeps a human in control of everything that leaves the machine.

It is also built to demonstrate three things that AI engineering roles
ask for:

  -----------------------------------------------------------------------
  Pillar                              What it shows
  ----------------------------------- -----------------------------------
  **MCP**                             Tools exposed as separate, typed
                                      servers instead of hard-coded
                                      functions

  **Harness**                         The runtime around the model: loop
                                      control, permissions, guardrails,
                                      budget, tracing, evals

  **Context & memory**                Deliberate control of what the
                                      model sees, and what the system
                                      remembers between sessions
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## Architecture

    ┌────────────────────────────────────────────────────────────────┐
    │  UI: Flask web app / CLI        (approval prompts live here)  │
    └──────────────────────────────┬─────────────────────────────────┘
                                   │
    ┌──────────────────────────────▼─────────────────────────────────┐
    │  HARNESS                                                       │
    │  ┌──────────────┐ ┌────────────────┐ ┌───────────────────────┐ │
    │  │ Agent loop   │ │ Context        │ │ Safety                │ │
    │  │ plan→act→    │ │ manager        │ │ permissions, claim    │ │
    │  │ observe      │ │ budget, compact│ │ verifier, injection   │ │
    │  └──────────────┘ └────────────────┘ │ defense, approvals    │ │
    │  ┌──────────────┐ ┌────────────────┐ └───────────────────────┘ │
    │  │ LLM gateway  │ │ Tracing        │                           │
    │  │ cache, $cap  │ │ JSONL per run  │                           │
    │  └──────────────┘ └────────────────┘                           │
    └───────┬───────────┬──────────┬──────────┬──────────┬───────────┘
            │ MCP (stdio)
       ┌────▼───┐ ┌─────▼──┐ ┌─────▼──┐ ┌─────▼──┐ ┌─────▼──┐ ┌──────▼──┐
       │ jobs   │ │profile │ │ memory │ │ match  │ │ tailor │ │ tracker │
       └────┬───┘ └────┬───┘ └────┬───┘ └────┬───┘ └────┬───┘ └────┬────┘
            │          └──────────┴──────────┴──────────┴──────────┘
    ┌───────▼────────┐                     ┌───────────────────────────┐
    │ Source adapters│                     │ SQLite + sqlite-vec       │
    │ (fallback      │                     │ jobs, profile, memories,  │
    │  chains)       │                     │ applications, embeddings  │
    └────────────────┘                     └───────────────────────────┘

------------------------------------------------------------------------

## Pillar 1: MCP servers

Each server does one job, speaks MCP over stdio, and returns typed
(Pydantic) results. The agent discovers tools at startup, so adding a
capability means adding a server, not editing the agent.

  -----------------------------------------------------------------------
  Server                  Tools                   Notes
  ----------------------- ----------------------- -----------------------
  `jobs`                  `search_jobs`,          Pulls from source
                          `get_job`,              adapters, dedupes,
                          `refresh_jobs`,         extracts structured
                          `add_job_from_url`      fields

  `profile`               `get_profile`,          Single source of truth
                          `search_experience`,    for CV facts; read-only
                          `list_skills`           to the agent

  `memory`                `remember`, `recall`,   Long-term memory (see
                          `forget`,               Pillar 3)
                          `list_memories`         

  `match`                 `score_job`,            Embedding similarity
                          `skill_gap`,            plus an LLM rubric
                          `rank_jobs`             

  `tailor`                `tailor_cv`,            Generates only from
                          `draft_cover_letter`    profile facts, verified
                                                  before returning

  `tracker`               `add_application`,      Local SQLite;
                          `update_status`,        Calendar/Sheets export
                          `list_followups`        optional
  -----------------------------------------------------------------------

### Source adapters

Job sources use an ordered fallback chain. Every adapter implements
`probe()` (is this backend alive right now?) and `fetch()`. The first
healthy backend wins, and `jobpilot doctor` reports the status of each.
The pattern is inspired by
[Agent-Reach](https://github.com/Panniantong/Agent-Reach)'s channel
routing, reimplemented here as our own code.

``` python
class SourceAdapter(Protocol):
    name: str
    def probe(self) -> ProbeResult: ...
    def fetch(self, query: JobQuery) -> list[RawJob]: ...
```

  -----------------------------------------------------------------------
  Priority                Source                  Method
  ----------------------- ----------------------- -----------------------
  1                       Remotive, Arbeitnow,    Free/official APIs
                          RemoteOK, Adzuna (free  
                          key)                    

  2                       Company career pages,   `feedparser`, Jina
                          RSS feeds               Reader

  3                       Rozee.pk and other      Playwright, public
                          public job pages        pages only,
                                                  rate-limited

  4                       LinkedIn *(optional,    Browser automation with
                          off by default)*        a throwaway account

  5                       Manual                  Paste a job URL or text
  -----------------------------------------------------------------------

> **LinkedIn note:** automated scraping of LinkedIn violates its Terms
> of Service and can get an account restricted. It ships disabled; the
> project works fully without it. Check each API's terms and attribution
> requirements before use.

------------------------------------------------------------------------

## Pillar 2: The harness

The harness is everything around the model that makes an agent reliable.
In JobPilot it is explicit code, not a prompt.

**Agent loop.** Plan, call a tool, observe, decide. Hard caps on steps
per run and on consecutive failures. Every step is validated against the
tool's schema.

**Permission tiers.** Every tool is tagged:

  -----------------------------------------------------------------------
  Tier                    Examples                Behavior
  ----------------------- ----------------------- -----------------------
  `read`                  `search_jobs`, `recall` Runs automatically

  `write_local`           `add_application`,      Runs automatically,
                          `remember`              logged

  `external`              sending an email,       **Always requires
                          submitting anything     explicit user
                                                  approval**
  -----------------------------------------------------------------------

**Guardrails.** - **Claim verifier:** every bullet in a tailored CV or
cover letter is checked against the profile. Unsupported claims are
rejected, not softened. Target: zero hallucinated claims. -
**Prompt-injection defense:** scraped job descriptions are untrusted
data. They are passed to the model as quoted content, never as
instructions, and tool calls triggered right after reading external text
are re-checked. - **Schema validation** on every tool input and output.

**LLM gateway.** All model calls go through one module that provides
response caching (the same JD is never scored twice), token and cost
counting, retries with backoff, and a hard budget cap per session and
per day (`MAX_BUDGET_USD`). This keeps development inside a small API
budget.

**Tracing.** Every run writes a JSONL trace: prompts, tool calls,
arguments, results, latency, tokens, cost. Traces feed both debugging
and the eval suite.

**Eval harness.** Replays a labeled task set against the agent and
reports metrics (see Evaluation).

------------------------------------------------------------------------

## Pillar 3: Context and memory management

### Context management

The model only sees what the context manager assembles for each turn,
within a fixed token budget.

    [ system prompt + tool schemas ]      stable prefix (cache-friendly)
    [ pinned facts: user goals/rules ]    always included
    [ recalled memories ]                 top-k from long-term memory
    [ rolling summary of older turns ]    compacted history
    [ recent turns, verbatim ]            last N messages
    [ tool results (compact form) ]       see below

Techniques used:

-   **Compact tool results.** Large objects stay in the database. The
    agent sees a job *card* (id, title, company, location, top skills,
    score) and fetches the full description by `job_id` only when it
    needs it.
-   **Compaction.** When history crosses a threshold, older turns are
    summarized into a rolling summary; pinned facts and unresolved tasks
    are preserved verbatim.
-   **Retrieval, not stuffing.** Profile experience and past memories
    are retrieved by relevance instead of loading everything.
-   **Per-section budgets.** Each section of the context has a token
    ceiling; overflow is truncated or summarized by policy, never
    silently.
-   **Stable prefix ordering** so provider-side prompt caching can work.

### Memory

  -----------------------------------------------------------------------
  Type                    What it holds           Storage
  ----------------------- ----------------------- -----------------------
  **Working**             Scratchpad for the      In-process
                          current run (plan,      
                          intermediate results)   

  **Session**             Conversation and        SQLite
                          rolling summary         

  **Semantic**            Durable facts and       SQLite + vectors
                          preferences: target     
                          roles, locations,       
                          salary floor, "no       
                          onsite outside          
                          Islamabad", skills to   
                          avoid                   

  **Episodic**            What happened: jobs     SQLite + vectors
                          rejected and why,       
                          applications sent,      
                          feedback received       
  -----------------------------------------------------------------------

**Write policy.** The model *proposes* a memory; the harness decides
whether to store it. Profile-affecting facts need user confirmation.
Each memory carries provenance (which run, which message), a timestamp,
and a confidence score. Near-duplicates are merged, and contradicting
memories are flagged rather than silently overwritten.

**Read policy.** Memories are recalled by embedding similarity plus
recency and importance weighting, then injected into the
recalled-memories section of the context.

**User control.** `list_memories` and `forget` are exposed in the UI.
You can always see and delete what the system remembers.

------------------------------------------------------------------------

## Example flow

> "Find this week's best AI Engineer jobs in Islamabad or remote, and
> tailor my CV for the top one."

1.  Harness assembles context: pinned goals, recalled preferences (for
    example "remote preferred"), recent turns.
2.  Agent calls `jobs.search_jobs`. The source chain runs; results are
    deduped and extracted.
3.  Agent calls `match.rank_jobs`. It sees compact cards, not full
    descriptions.
4.  Agent calls `profile.search_experience` for the top job, then
    `tailor.tailor_cv`.
5.  Claim verifier checks every generated bullet against the profile.
6.  `tracker.add_application` logs it. Anything external (email,
    submission) pauses for approval.
7.  `memory.remember` proposes: "rejected Job X: requires 3+ years." You
    confirm.
8.  The full trace is written to disk.

------------------------------------------------------------------------

## Repository layout

    jobpilot/
    ├── pyproject.toml
    ├── .env.example
    ├── README.md
    ├── docs/
    │   ├── architecture.md
    │   └── eval-report.md
    ├── src/jobpilot/
    │   ├── core/
    │   │   ├── config.py          # settings
    │   │   ├── models.py          # Job, Profile, Match, Memory, Application
    │   │   └── db.py              # SQLite + migrations
    │   ├── harness/
    │   │   ├── loop.py            # agent loop, step/failure caps
    │   │   ├── context.py         # context assembly, budgets, compaction
    │   │   ├── llm.py             # gateway: cache, retries, cost cap
    │   │   ├── permissions.py     # tool tiers, approval gate
    │   │   ├── guardrails.py      # claim verifier, injection defense
    │   │   └── tracing.py         # JSONL traces
    │   ├── memory/
    │   │   ├── store.py           # semantic + episodic storage
    │   │   ├── policy.py          # write/read/merge/decay rules
    │   │   └── summarizer.py      # rolling summaries
    │   ├── sources/
    │   │   ├── base.py            # SourceAdapter protocol
    │   │   ├── registry.py        # fallback chains + doctor()
    │   │   ├── remotive.py  arbeitnow.py  adzuna.py
    │   │   ├── rss.py  web_reader.py  manual.py
    │   │   └── linkedin.py        # optional, disabled by default
    │   ├── servers/               # MCP servers
    │   │   ├── jobs_server.py  profile_server.py  memory_server.py
    │   │   └── match_server.py  tailor_server.py  tracker_server.py
    │   ├── pipelines/             # dedupe, extract, embed
    │   └── ui/
    │       ├── app.py             # Flask application and routes
    │       ├── cli.py             # jobpilot doctor | chat | eval
    │       ├── templates/         # Jinja2 HTML templates
    │       │   ├── base.html
    │       │   ├── index.html
    │       │   ├── jobs.html
    │       │   ├── profile.html
    │       │   └── tracker.html
    │       └── static/
    │           ├── css/
    │           │   └── style.css
    │           └── js/
    │               └── app.js
    ├── eval/
    │   ├── dataset/               # labeled job descriptions and tasks
    │   ├── baselines.py           # TF-IDF / keyword matcher
    │   ├── run_eval.py
    │   └── metrics.py
    ├── tests/
    └── docker/

------------------------------------------------------------------------

## Tech stack

Python 3.11+, MCP Python SDK (FastMCP), OpenAI API, Pydantic, SQLite
with `sqlite-vec`, `httpx`, `feedparser`, Playwright, Flask, Jinja2,
HTML/CSS/JavaScript, Bootstrap (optional), pytest, Docker (optional).

Models are configured by environment variable, so they can be swapped
without code changes. Use a small, cheap model for extraction and
scoring, and a stronger one only for tailoring. Embeddings can use an
OpenAI embedding model or a local `sentence-transformers` model for zero
cost.

------------------------------------------------------------------------

## Setup *(planned)*

``` bash
git clone <your-repo-url> && cd jobpilot
uv sync
uv add flask
cp .env.example .env        # add OPENAI_API_KEY, optional ADZUNA_KEY, MAX_BUDGET_USD
uv run jobpilot doctor      # check which sources are alive
uv run jobpilot chat        # CLI agent
uv run flask --app src.jobpilot.ui.app run --debug
# Open http://127.0.0.1:5000 in your browser
```

------------------------------------------------------------------------

## Flask UI

The web interface uses Flask with Jinja2 templates and static
CSS/JavaScript assets. It can provide job search, match scores, CV and
cover-letter drafts, application tracking, and memory management.

The Flask interface must be implemented in `src/jobpilot/ui/app.py` with
templates under `src/jobpilot/ui/templates/` and assets under
`src/jobpilot/ui/static/`. Updating this README does not automatically
convert any existing Streamlit code; replace Streamlit-specific UI code
with Flask routes and templates as part of implementation.

Run the web UI locally:

``` bash
uv run flask --app src.jobpilot.ui.app run --debug
```

Then visit `http://127.0.0.1:5000`.

------------------------------------------------------------------------

## Evaluation

A labeled set of 30-40 real job descriptions plus a set of end-to-end
tasks.

  -----------------------------------------------------------------------
  Metric                              What it measures
  ----------------------------------- -----------------------------------
  Match correlation                   Agreement between model scores and
                                      human labels

  Precision@5                         Quality of the top-ranked jobs

  **Hallucinated claims**             Unsupported statements in tailored
                                      CVs (target: 0)

  Tool-selection accuracy             Whether the agent calls the right
                                      tools

  Memory recall accuracy              Whether stored preferences are
                                      applied in later sessions

  Injection resistance                Behavior against job descriptions
                                      containing malicious instructions

  Cost and latency                    Dollars and seconds per task
  -----------------------------------------------------------------------

**Baselines:** TF-IDF/keyword matching for scoring; no-memory and
no-compaction ablations for the harness.

**Research question (FYP):** *Does an LLM-based matching agent with
structured memory and verified generation outperform keyword matching,
and what do the harness components (claim verifier, context compaction,
memory) each contribute?*

------------------------------------------------------------------------

## Roadmap

  -----------------------------------------------------------------------
  Weeks                               Milestone
  ----------------------------------- -----------------------------------
  1-2                                 `core/`, source adapters (2 APIs
                                      plus `doctor`), `jobs` server,
                                      minimal harness loop, CLI chat

  3-4                                 Pipelines (dedupe, extract, embed),
                                      `profile` and `match` servers, LLM
                                      gateway with cache and budget cap

  5-6                                 `memory` server and policies,
                                      context manager with compaction,
                                      `tailor` server plus claim verifier

  7                                   `tracker` server, permission tiers
                                      and approval UI, tracing

  8                                   Eval suite, baselines and
                                      ablations, Docker, demo video,
                                      final report

  Later                               Optional LinkedIn adapter,
                                      interview-prep server,
                                      Calendar/Sheets export
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## Safety and responsible use

-   Nothing is sent or submitted without explicit approval.
-   Generated CV content is restricted to facts in your profile and
    verified before display.
-   Scraped content is treated as untrusted input.
-   Respect each source's terms of service and robots rules; prefer
    official APIs and public pages.
-   Credentials stay in `.env` and are never logged or committed.

## License

MIT
