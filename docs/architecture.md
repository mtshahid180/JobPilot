# Architecture

JobPilot is a local-first, multi-account application. It deliberately keeps the runtime small enough to inspect: one SQLite file, one Flask process, a typed tool registry, and six MCP servers that reuse the same services. Every user-owned row is scoped by `user_id`; the web app binds the signed-in account for the duration of each request.

## Layers

| Layer | Modules | Responsibility |
| --- | --- | --- |
| **Interface** | `ui/app.py`, `ui/templates/`, `ui/static/`, `ui/cli.py` | Accounts, server-rendered pages, JSON API, CLI, approval and memory surfaces |
| **Harness** | `harness/loop.py`, `context.py`, `llm.py`, `permissions.py`, `guardrails.py`, `tracing.py` | Orchestration, context budgets, model calls, capability gates, verification, per-user traces and budget |
| **Domain** | `services/*.py` | Accounts/auth, jobs, profile, matching, tailoring, tracking, memory, sessions, stats |
| **Memory** | `memory/store.py`, `policy.py`, `summarizer.py` | Long-term recall, write policy, conflict detection, rolling summaries |
| **Pipelines** | `pipelines/extract.py`, `dedupe.py`, `embedding.py` | Normalize postings, skill extraction, fingerprints, vectors |
| **Sources** | `sources/*.py` | Greenhouse, Workable, free feeds, RSS, manual import, disabled Indeed/LinkedIn stubs |
| **MCP** | `servers/*_server.py` | Typed stdio servers over the domain layer |
| **Persistence** | `core/context.py`, `core/db.py`, `core/models.py` | Request identity, explicit SQL schema, Pydantic models, migrations |
| **Config** | `core/config.py` | Environment-backed typed settings |

Dependencies point downward. The UI and MCP servers both call services; services call pipelines and memory; only `core` is shared by everything. No layer imports the UI.

## Identity and isolation

```
HTTP request
  → load_current_user() reads the signed session
  → set_current_user_id(user_id)  (ContextVar)
  → service queries include WHERE user_id = ?
  → teardown resets the ContextVar
```

Headless callers use the same mechanism: `resolve_workspace_user()` reads `JOBPILOT_USER_ID` or `JOBPILOT_USER_EMAIL`, defaulting to the legacy `local` workspace. Services never accept an arbitrary user id from a request body.

## Request flows

### Web page

```
GET /jobs
  → create_app route
  → render_template("jobs.html", ...)
  → base.html + components.html
  → static/js/app.js + jobs.js
  → fetch("/api/jobs?…")
```

Pages are server-rendered shells. Interactive data is loaded through the JSON API so filters and actions do not reload the document.

### Agent run

```
POST /api/agent/run
  → AgentLoop.run(goal, session_id, mode)
  → SessionService.add_message(user)
  → MemoryService.recall(goal) + rules()
  → ContextManager.build(...)
  → Run + Tracer created
  → LLM mode or deterministic local planner
      → ToolRegistry.call(name, args)
      → PermissionPolicy.decide(name)
          → read / write_local: execute + trace
          → confirmation / external: create Approval, pause
  → AgentReply + assistant message + session summary
  → RunStore.finish + tracer.finish
```

A run ends as `completed`, `awaiting_approval`, or `failed`. Approvals can be decided later; an approved action is replayed from its stored arguments, not from model output.

### Job search

The job explorer and the agent's `search_jobs` tool call the same service, so a role cannot be found by one and hidden by the other.

```
free-text request
  → taxonomy.parse_intent()
      → category, keywords, seniority, workplace, employment
      → locations: city / region / country / remote scope
  → per-source query terms (category paths, geo slugs, API params)
  → SourceRegistry.search()          [all enabled adapters, in parallel, 45s budget]
      → RawJob lists                 [a slow source is recorded as `timeout`]
  → extract: HTML, skills, category, workplace, salary, dates, eligibility
  → dedupe: fingerprint + near-duplicate merge
  → mandatory filters
      → category relevance (exact / related / mismatch)
      → geography + remote eligibility (verified / unverified / mismatch)
      → workplace, seniority, employment, recency
  → rank (tier, relevance, recency, company) and annotate reasons
  → upsert into jobs, cache the ranked id list for this query
  → page slice → MatchService score for the returned page
```

Three rules make the result set trustworthy:

1. **Nothing is invented.** A source that fails is reported with its status and error; an empty search says so instead of falling back to sample data.
2. **Geography is not substring matching.** `workplace_type`, `job_location`, `company_location` and `eligible_locations` are separate fields. A company's headquarters never stands in for where a remote employee may live, and unknown eligibility is surfaced as `unverified` rather than assumed.
3. **Occupation filtering is mandatory.** Results whose category is unrelated to the request are dropped and counted; related occupations are kept but labelled `related`.

Caching is per query, in-process, and five minutes by default. The cached value is the full ranked id list, so page changes never trigger another source fan-out.

The match score is precomputed for UI results but never trusted as the only signal: the agent can call `score_job` or `skill_gap` for a deeper look, and the rubric breakdown is always attached.

## Agent loop modes

| Mode | When | Behavior |
| --- | --- | --- |
| `local` | No key, or the user selects local | Deterministic intent planner. Covers search, rank, tailor, draft, track, memory, sources, and help. |
| `auto` | Key present (default) | Native tool calling through the gateway, with step caps and duplicate-call suppression. |
| `local-fallback` | LLM error, unavailable endpoint, or budget exhaustion | The same local planner runs; the trace records why. |

Both modes share the tool registry, permission policy, guardrails, context manager, and tracer. The local planner is not a mock: it performs real searches and real verified generation.

## Context assembly

`ContextManager.build()` produces a budgeted message list in a stable order:

1. **System prompt + tool schemas** — stable prefix.
2. **Pinned rules** — standing user rules, preserved verbatim.
3. **Recalled memories** — top-k by embedding similarity, importance, and recency.
4. **Rolling summary** — compacted older turns.
5. **Recent turns** — verbatim message history.
6. **Current goal** — the user request.

Each section has a token ceiling. Overflow is truncated or summarized by policy rather than silently dropped. The context report (`sections`, `total_tokens`) is written into the trace at run start.

Large objects are never stuffed into context. The agent sees compact job cards and fetches a full description by `job_id` only when needed.

## Data model

SQLite schema version 2. Connections are short-lived, which keeps the module safe under Flask threads and MCP subprocesses.

| Table | Holds |
| --- | --- |
| `users` | Account identity: email, display name, password hash, active flag, login timestamps |
| `jobs` | Normalized postings scoped to a user, fingerprint, extracted skills, score and breakdown, saved/status flags |
| `profile` | One JSON profile row per user (`user_id`) plus `updated_at` |
| `applications` | One row per job per user, status, notes, follow-up date, JSON history |
| `memories` | Kind, content, tags, provenance, confidence, importance, status, embedding blob, usage |
| `tailored_cvs` | Bullets, verification checks, unsupported count, verified flag |
| `cover_letters` | Body, checks, unsupported count, verified flag |
| `approvals` | Tool, stored arguments, tier, status, timestamps |
| `runs` | Goal, mode, status, steps, tokens, cost, summary |
| `trace_events` | Append-only run events mirroring the JSONL trace |
| `sessions` / `messages` | Conversation history and rolling summaries |
| `llm_cache` | Provider response cache keyed per user + model + request |
| `budget` | Per-user, per-day spend, calls, and tokens |
| `kv` | Small internal metadata |

Every user-owned table carries `user_id`; the migration from schema v1 assigns legacy rows to the `local` workspace. Every write goes through `Database.execute()` with parameters. There is no ORM and no dynamic SQL built from user input except controlled `WHERE` clause assembly in list queries.

## Tool registry

`services/tools.py` is the single definition of agent capabilities. Each `ToolSpec` declares:

- name and namespace
- description shown to the model
- permission tier
- JSON Schema parameters
- handler

The registry validates required fields, coerces primitive types, and drops unknown keys before calling a handler. `tool_catalog()` exposes the same metadata to the Settings page.

The MCP servers register a subset of this surface with SDK decorators. If you add a tool, decide whether it belongs in the agent registry, the MCP servers, or both. See [development.md](development.md).

## Source adapters

Every adapter implements `probe()` and `fetch()` and returns `RawJob` objects. `SourceRegistry`:

- probes with a TTL cache,
- skips disabled or unhealthy adapters,
- collects partial results and per-source errors,
- deduplicates before storage,
- reports latency to `jobpilot doctor`.

A source failure is isolated: the search returns what worked and lists the error in `meta.errors`.

## Design tradeoffs

| Decision | Why | Cost |
| --- | --- | --- |
| Local hashing embeddings | Zero cost, deterministic tests, no model download | Weaker semantics than neural embeddings |
| In-process tool registry + separate MCP servers | Fast UI, no subprocess per tool call, still MCP-shaped | Two surfaces to keep aligned |
| Template tailoring by default | No key required; verifiable output | Less stylistic variation than an LLM |
| SQLite | Inspectable, portable, no server | Single writer; logical isolation is application-enforced, not one database per account |
| Server-rendered pages + fetch | No Node toolchain | Some duplicated client rendering logic |
| Hard step and failure caps | Predictable cost and safe failure | Complex goals may need multiple runs |

## Where to extend

- **New job source:** implement an adapter in `sources/` and register it.
- **New capability:** add a service and a `ToolSpec`; expose it through MCP if it is useful externally.
- **New page:** add a route, template, and page script; follow `base.html` blocks.
- **New guardrail:** add a pure function in `harness/guardrails.py` and cover it in tests and eval.
- **New metric:** add to `eval/metrics.py`, wire into `eval/run_eval.py`, and add a quality gate.
