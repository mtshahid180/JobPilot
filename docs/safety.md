# Safety, permissions, and memory

JobPilot is a personal assistant that reads untrusted job postings and can write local records. Its safety model assumes three things:

1. **Scraped text is hostile.** Job descriptions can contain prompt injection, fake instructions, or attempts to exfiltrate data.
2. **Generated text is guilty until verified.** A language model can invent experience. Nothing reaches a draft unless it is grounded in the profile.
3. **External actions need a human.** The system may prepare an application, but it never sends one.

## Trust boundaries

| Source | Trust | Handling |
| --- | --- | --- |
| User profile and confirmed memories | Trusted | Treated as the only source of CV facts |
| User messages | Trusted for intent, not for facts | Cannot silently alter the profile |
| Job descriptions, RSS, imports | **Untrusted** | Quoted, scanned, never executed as instructions |
| Model output | **Untrusted** | Schema-checked; claims verified; external tools gated |
| MCP/tool arguments | **Untrusted** | Validated against JSON Schema and permission tier |
| Local database | Trusted | Multi-account, on-disk, rows scoped by `user_id` |

## Permission tiers

Every tool declares a tier in `services/tools.py`.

| Tier | Tools | Behavior |
| --- | --- | --- |
| `read` | `search_jobs`, `get_job`, `get_profile`, `list_skills`, `search_experience`, `score_job`, `rank_jobs`, `skill_gap`, `list_applications`, `list_followups`, `recall`, `list_memories`, `check_sources`, `get_stats` | Runs immediately; traced |
| `write_local` | `import_job`, `tailor_cv`, `draft_cover_letter`, `add_application`, `update_status`, `remember` | Runs immediately; traced; affects only local SQLite |
| `confirmation` | `forget`, profile-affecting `remember` proposals | Pauses with a pending approval |
| `external` | `request_submission` | Always pauses for explicit approval |

Unknown tiers default to `external`, so a new tool without a tier fails closed.

### Approval replay

An approval stores the tool name, arguments, tier, and run ID. When you approve:

1. The approval status becomes `approved`.
2. `execute_approval()` calls the tool with a context marked `approved=True`.
3. The result is returned to the UI and written to the trace.

The model does not get to rephrase the request at replay time. It replays the exact stored arguments. A second decision on the same approval returns `409`.

`request_submission` is intentionally a **dry-run**: even after approval it records the application locally and reports that no email or external request was sent.

## Claim verification

The verifier in `harness/guardrails.py` checks every generated bullet and cover-letter claim against `profile_evidence()`: name, headline, summary, skills, languages, target roles, experience bullets, education, and any supplied source bullet.

A claim is rejected when:

- a **number** in the claim does not appear verbatim in the evidence;
- a **named skill** is not in the profile;
- fewer than **60%** of its content words are grounded.

Rejection means the claim is **dropped**, not softened. The draft records a `ClaimCheck` per claim with the reason and the best matching evidence line. `verified` is true only when `unsupported_count == 0`.

This is a lexical guardrail, not a semantic entailment model. It is conservative on numbers and skills, and it can reject a true paraphrase or accept a loosely worded claim. The eval suite includes a challenge set with known true and false claims to keep both error rates visible.

## Resumes

An uploaded resume is personal data, so the flow is deliberately narrow:

- **Per-account storage.** Files live under the data directory in a folder keyed by `user_id`; the `resumes` row is scoped to the same account. One account cannot list, read, or delete another's file.
- **Local parsing only.** PDF text is read with `pypdf`, DOCX with the standard library, TXT/Markdown read as text. Nothing is uploaded to a model provider to extract it, and no OCR service is called.
- **Draft, then review.** Parsing produces a `ResumeDraft` — a proposed `Profile` plus the list of fields that would change. It is stored with the resume, not applied. The saved profile changes only when the user ticks fields and confirms; the API requires the field list, and the UI sends the values the user edited.
- **Only literal text.** The parser copies name, email, phone, links, headline, location, summary, experience, education and skills that appear in the document. It does not infer employers, dates, or achievements, and it does not invent seniority.
- **Replacement and deletion.** Uploading again replaces the current resume; delete removes the stored file and its draft and leaves the saved profile untouched.
- **Size and type limits.** `MAX_RESUME_MB` (10 by default) and an allow-list of extensions. A scanned PDF with no text layer fails with an explicit message instead of producing an empty profile.
- **Resume text does not narrow search.** A finance resume does not force finance-only searches; the profile informs ranking, while the query still decides the occupation.

Resumes are the most sensitive data the app stores. Treat `data/` as personal data, and do not commit it.

## Prompt-injection defense

Untrusted text passes through two functions:

- `screen_untrusted(text)` — pattern scan for instruction overrides, role hijacks, prompt exfiltration, data exfiltration, approval bypass, tool coercion, obfuscation, and role markers. Returns `none`, `low` (one reason), or `high` (two or more).
- `wrap_untrusted(text, label)` — wraps the text in an `<untrusted>` envelope that explicitly says it is data, not instructions, and includes the risk level.

Where it applies:

- `get_job` scans the description and returns `injection_scan` alongside the wrapped text.
- `tailor_cv`/`draft_cover_letter` wrap the target-job brief before any LLM call.
- The local planner never treats posting text as a command; it only selects from a fixed intent set.

Limits: pattern matching is not a classifier. Novel phrasing can evade it, and a single pattern hit only yields `low`. The defense reduces risk; it does not make an untrusted document safe.

## Budget and failure caps

The LLM gateway is the only path to a model provider.

- **Response cache** keyed by model, kind, and request — the same job is never paid for twice.
- **Retries** with backoff, bounded by `LLM_MAX_RETRIES`.
- **Hard caps** for total spend (`MAX_BUDGET_USD`) and daily spend (`DAILY_BUDGET_USD`).
- **Per-call timeout** (`LLM_TIMEOUT_SECONDS`).
- **Token and cost accounting** written to `runs`, `budget`, and the trace.
- **Graceful fallback** to the local planner on `LLMUnavailable` or `BudgetExceeded`.

The agent loop also enforces:

- `MAX_STEPS` per run (default 12),
- `MAX_CONSECUTIVE_FAILURES` (default 3),
- duplicate tool-call suppression,
- schema validation before execution.

A budget-exhausted API run returns `429`; it does not return a half-finished action.

## Memory policy

The model **proposes**; the harness **decides**.

### Classification

`memory/policy.py` classifies content:

| Kind | Trigger examples | Confirmation |
| --- | --- | --- |
| `semantic` (profile fact) | "my name is…", "I have 3 years…", "I graduated…" | **Required** |
| `preference` | "I prefer remote", "salary floor…" | No |
| `rule` | "always…", "never apply to…" | No |
| `episodic` | "I applied to…", "rejected after…" | No |

### Write rules

- Near-duplicates at cosine similarity ≥ **0.92** merge instead of duplicating.
- Contradictions (remote vs onsite, salary differing by more than 25%) are flagged.
- Each memory stores provenance, confidence, importance, tags, and a timestamp.
- Profile-affecting facts create a pending confirmation, surfaced in the Memory page and as an approval.
- `forget` is a confirmation-tier tool and permanent.

### Recall

Active memories are scored as:

```
0.65 × embedding similarity
+ 0.20 × importance
+ 0.15 × recency (120-day decay)
```

The top-k above a 0.08 threshold enter the context. `last_used_at` and `use_count` are updated. Rules are also pinned separately so standing constraints survive compaction.

### Compaction and summaries

`SessionService` stores a rolling summary. Older turns are summarized, while pinned rules and unresolved tasks remain. The context manager enforces per-section token ceilings, and the eval suite checks that compaction actually reduces history rather than silently overflowing.

## Tracing and privacy

Every run writes:

- a `runs` row (goal, mode, status, steps, tokens, cost, error),
- step records embedded in the reply and trace,
- `trace_events` rows,
- a JSONL file in `traces/` (configurable via `TRACE_DIR`).

Traces contain goals, tool arguments, observations, and model metadata. Treat them as personal data. `redact_secrets()` exists for API keys in text, but it is not a general PII scrubber. Do not commit `data/` or `traces/`; both are gitignored.

## Source and platform terms

- **Greenhouse and Workable** expose public, key-free ATS APIs. Use board tokens and account slugs you are entitled to read; respect their rate limits.
- **Mustakbil** serves public, server-rendered job listing pages under `/jobs`; its `robots.txt` permits them. The adapter reads only those public listing pages, sends no credentials, and stops at the site's own pagination. Query strings return `410`, so path-based category and city pages are used instead.
- **Himalayas, The Muse and Jobicy** expose public JSON endpoints for remote and general listings. Respect their rate limits and attribution; each can be switched off with `ENABLE_HIMALAYAS`, `ENABLE_THEMUSE`, `ENABLE_JOBICY`.
- **Indeed** retired its public Job Search API and prohibits scraping. The adapter is always disabled and explains the policy in `jobpilot doctor`.
- **LinkedIn** has no public jobs API, and automated scraping violates its Terms of Service. The adapter never fetches data (`ENABLE_LINKEDIN` is kept for compatibility only). Paste a LinkedIn job URL into manual import to track it; JobPilot shows this status honestly.
- **Adzuna** requires a free API key and has attribution terms. Supply your own key and follow its usage policy.
- **RSS and public APIs** have their own rate limits; the adapters use timeouts and isolated failures.
- **Manual import** is the user's responsibility: only import content you are allowed to access.

## Deployment checklist

JobPilot now supports multiple accounts, but it is still designed to run on a trusted machine. Before exposing it beyond localhost:

- [ ] Set a strong `SECRET_KEY` and disable `DEBUG`.
- [ ] Put it behind TLS and a reverse proxy with request limits.
- [ ] Move `data/` and `traces/` to encrypted, access-controlled storage.
- [ ] Review retention: traces and conversations accumulate indefinitely.
- [ ] Keep the LLM budget caps and monitor spend.
- [ ] Confirm source terms and attribution for every enabled adapter.
- [ ] Review account lifecycle: there is no email verification or password reset flow yet.

## Known limitations

- Pattern-based injection screening can miss novel attacks.
- Claim verification is lexical and conservative, not semantic entailment.
- The local planner covers a fixed set of intents.
- Authentication is session-based; there is no email verification, password reset, MFA or per-IP rate limiting in the shipped app.
- SQLite is shared by all accounts in one file; isolation is enforced by `user_id` scoping, not separate databases.
- SQLite is single-writer; concurrent MCP subprocesses can contend under load.
- `request_submission` is a dry run by design, not an ATS integration.
- Resume parsing is text extraction, not OCR. Image-only PDFs and scanned documents fail explicitly; no OCR engine ships with the app.
- Located-listing coverage depends on what each public source publishes. Remote eligibility is often unstated, and is shown as `unverified` rather than guessed.
