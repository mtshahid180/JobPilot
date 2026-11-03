# HTTP API

The Flask app serves server-rendered pages and a JSON API from the same origin. Every account has an isolated workspace; requests are authenticated with the signed-in session. Bind to `127.0.0.1` for local use and see [safety.md](safety.md#deployment-checklist) before exposing it.

## Conventions

- Requests and responses are JSON unless noted.
- Successful responses include `"ok": true`. Failures return `"ok": false` and an `"error"` string.
- Error status codes: `400` validation, `401` authentication required, `404` missing resource, `409` already decided, `429` LLM unavailable or budget exhausted, `500` unexpected.
- Datetimes are ISO 8601 UTC strings.
- IDs are prefixed: `job_…`, `app_…`, `mem_…`, `run_…`, `apr_…`, `cv_…`, `cl_…`.
- Unknown JSON fields are ignored by the API; tool arguments are also schema-filtered by the registry.

Example error:

```json
{ "ok": false, "error": "job not found: job_abc" }
```

## Pages

All pages except `/login` and `/signup` require an authenticated session. Anonymous HTML requests redirect to `/login?next=...`; anonymous API requests receive `401` JSON.

| Method | Path | Page |
| --- | --- | --- |
| GET/POST | `/signup` | Create an account and start a session |
| GET/POST | `/login` | Sign in |
| POST | `/logout` | Sign out (CSRF-protected) |
| POST | `/account/password` | Change password (CSRF-protected) |
| GET | `/` | Dashboard |
| GET | `/setup` | First-run onboarding (redirects to `/` once a profile name exists) |
| GET | `/jobs` | Job search and detail |
| GET | `/agent` | Agent console |
| GET | `/profile` | Profile editor |
| GET | `/tracker` | Application tracker |
| GET | `/memory` | Memory review |
| GET | `/drafts` | CV and cover-letter drafts |
| GET | `/runs` | Run traces |
| GET | `/settings` | Runtime, sources, tools, budget, account |

Unknown pages render `404.html`; unhandled errors render `error.html`.

### First-run behavior

A new account starts with a blank, private profile. `GET /` redirects to `/setup` while the profile has no real name, experience or skills. The setup screen saves through `PUT /api/profile` or pre-fills through `POST /api/profile/parse-cv`. Add `?skip=1` to `GET /` to view the dashboard before completing setup; `/setup` redirects to `/` after a name is saved.

## Health and dashboard

### `GET /api/health`

Public. Anonymous calls return a liveness signal only; authenticated calls add per-account database counts, `user` and `budget`.

```json
{
  "ok": true,
  "app": "JobPilot",
  "version": "0.1.0",
  "llm_enabled": false,
  "database": { "ok": true, "users": 1, "size_kb": 412, "jobs": 12, "applications": 2, "memories": 4 },
  "budget": {
    "spent_total": 0.0,
    "spent_today": 0.0,
    "max_total": 1.5,
    "max_daily": 0.5,
    "remaining": 0.5
  },
  "user": { "id": "usr_...", "email": "you@example.com" },
  "time": "2026-10-24T10:58:00+00:00"
}
```

### `GET /api/dashboard`

Returns aggregated stats plus the five highest-scoring jobs.

| Field | Meaning |
| --- | --- |
| `jobs`, `applications`, `memory`, `runs` | Per-domain stats objects |
| `profile_completeness`, `profile_name` | Profile summary |
| `budget`, `llm_enabled` | Gateway state |
| `pending_approvals` | Count of undecided approvals |
| `recent_applications`, `recent_runs`, `followups`, `top_jobs`, `top_skills`, `sources` | Dashboard lists |
| `has_activity` | Whether jobs or applications exist |

## Jobs

### `POST /api/jobs/search`

Runs every enabled source in parallel, normalises and deduplicates the results, applies the mandatory category and geography filters, then ranks and paginates them. The same service backs the job explorer and the agent's `search_jobs` tool.

```json
{
  "query": "Finance Manager",
  "location": "Karachi, Pakistan",
  "remote_only": false,
  "category": "finance",
  "workplace_type": "remote",
  "seniority": "manager",
  "employment_type": "full-time",
  "include_related": true,
  "include_unverified": true,
  "posted_within_days": 30,
  "page": 1,
  "page_size": 12,
  "refresh": false,
  "sources": []
}
```

`category`, `workplace_type`, `seniority`, `employment_type`, `posted_within_days` and `sources` are optional; anything omitted is inferred from `query`/`location` or left unconstrained. `page_size` is clamped to 1–100. Empty `sources` means all enabled adapters. `refresh: true` bypasses the in-process cache.

Response:

```json
{
  "ok": true,
  "count": 12,
  "total": 77,
  "page": 1,
  "page_size": 12,
  "total_pages": 7,
  "jobs": [
    {
      "id": "job_…",
      "title": "Financial Planning & Analysis Manager",
      "company": "Lexitas",
      "location": "Remote",
      "category": "finance",
      "category_label": "Finance & Financial Analysis",
      "workplace_type": "remote",
      "job_location": "Remote",
      "company_location": "",
      "eligible_locations": ["Worldwide"],
      "remote_scope": "worldwide",
      "location_confidence": "verified",
      "match_tier": "exact",
      "availability": "active",
      "score": 87.4,
      "rationale": "Strong skills overlap…",
      "breakdown": { "skills": 0.9, "role": 0.8, "semantic": 0.7, "location": 1.0, "seniority": 1.0, "recency": 0.6 }
    }
  ],
  "meta": {
    "counts": { "fetched": 330, "exact": 26, "related": 31, "unverified": 19, "category_rejected": 223, "location_rejected": 31 },
    "interpreted": { "category": "finance", "city": "Karachi", "country": "Pakistan", "seniority": "manager" },
    "freshness": { "newest_posted": "…", "active": 54, "stale": 22, "unknown": 0 },
    "source_runs": [
      { "name": "mustakbil", "status": "ok", "count": 19, "elapsed_ms": 4120, "error": "" }
    ],
    "notes": ["Location interpreted as Karachi, Sindh, Pakistan."],
    "cache": { "hit": false, "age_seconds": 0 },
    "elapsed_ms": 8120
  }
}
```

`match_tier` is `exact`, `related` or `unverified`. `unverified` means the role is remote but no source stated which countries it can hire in — it is returned only while `include_unverified` is true, and is never presented as a verified match. A source that does not answer inside `SOURCE_BUDGET_SECONDS` appears in `source_runs` with `status: "timeout"`; the rest of the results are still returned.

### `GET /api/jobs`

Query parameters: `q`, `location`, `remote=1`, `saved=1`, `status`, `min_score`, `source`, `category`, `workplace`, `sort` (`score` default), `limit` (60), `offset` (0).

Returns the saved library, including `description`, `url`, `fingerprint`, `score_breakdown`, and timestamps.

### `GET /api/jobs/<job_id>`

Returns `job`, a freshly computed `match`, and the linked `application` (or `null`).

### `POST /api/jobs/<job_id>/save`

Body `{ "saved": true }`. If `saved` is omitted, the flag toggles.

```json
{ "ok": true, "saved": true, "job": { "id": "job_…", "saved": true } }
```

### `POST /api/jobs/<job_id>/score`

Recomputes the match and returns `{ "ok": true, "match": { … } }`. Returns `404` for an unknown job.

### `POST /api/jobs/import`

Body `{ "value": "https://…" }` or `{ "value": "<full posting text>" }`. Returns the stored `job` and its `match`.

## Profile

### `GET /api/profile`

```json
{
  "ok": true,
  "profile": { "full_name": "Ayesha Khan", "target_roles": ["AI Engineer"], "skills": ["Python"] },
  "completeness": 82,
  "skills": [{ "skill": "Python", "evidence_count": 5 }]
}
```

### `PUT /api/profile`

Body is a partial profile patch. Nested `experience` and `education` arrays replace their stored values when supplied.

```json
{
  "full_name": "Ayesha Khan",
  "headline": "AI Engineer",
  "target_roles": ["AI Engineer", "ML Engineer"],
  "remote_preference": "remote",
  "skills": ["Python", "PyTorch"]
}
```

Returns the updated `profile` and `completeness`. Invalid data returns `400`.

### `POST /api/profile/parse-cv`

Body `{ "text": "<pasted CV>" }`, minimum 40 characters. Parses name, links, skills, experience, and education. A header line such as `Senior Financial Analyst | Karachi, Pakistan` fills both `headline` and `location`. **Review every field before saving**; parsing is heuristic.

Returns `profile`, `completeness`, and a message.

## Resumes

Uploaded resumes are stored per account and never applied automatically. Extraction produces a draft profile plus the list of fields a merge would change; nothing reaches the saved profile until you apply it.

### `GET /api/resumes`

```json
{ "ok": true, "resumes": [{ "id": "res_…", "filename": "cv.pdf", "status": "needs_review", "text_chars": 2411, "is_current": true }] }
```

`status` is `uploaded`, `needs_review`, `extracted`, `failed`, or `deleted`.

### `POST /api/resumes`

`multipart/form-data` with one `file` field. Accepts PDF, DOCX, TXT and Markdown up to `MAX_RESUME_MB`. Returns `resume`, `profile_draft` (a full `Profile`, never saved), `changed_fields`, and `warnings`.

A scanned PDF with no text layer fails with an explicit message; OCR is not installed.

### `GET /api/resumes/<resume_id>`

Returns the stored resume and the draft extracted from it.

### `POST /api/resumes/<resume_id>/apply`

```json
{ "fields": ["full_name", "headline", "skills"], "patch": { "headline": "Senior Financial Analyst" } }
```

Merges only the named fields into the saved profile; `patch` lets the UI send the values the user edited during review. Returns the updated `profile`.

### `POST /api/resumes/<resume_id>/current`

Marks this resume as the account's current one. The file itself is not re-parsed.

### `DELETE /api/resumes/<resume_id>`

Removes the stored file and its extraction draft. The saved profile is unchanged.

## Tailoring and drafts

### `POST /api/tailor/cv`

Body `{ "job_id": "job_…" }`. Returns a `TailoredCV`:

```json
{
  "ok": true,
  "cv": {
    "id": "cv_…",
    "job_id": "job_…",
    "headline": "AI Engineer",
    "summary": "…",
    "skills": ["Python", "LLM"],
    "bullets": [{ "text": "Built …", "evidence": "AI Intern @ Acme", "supported": true }],
    "checks": [{ "claim": "Built …", "supported": true, "reason": "…" }],
    "unsupported_count": 0,
    "verified": true,
    "method": "template",
    "job": { "id": "job_…", "title": "AI Engineer" }
  }
}
```

`method` is `template` or `llm:<model>`. Unsupported claims are removed before the response; `verified` is false if any remain.

### `POST /api/tailor/cover-letter`

Body `{ "job_id": "job_…" }`. Same verification contract; returns `cover_letter` with `body`, `checks`, `unsupported_count`, and `verified`.

### `GET /api/drafts`

Returns the 30 newest `cvs` and `letters`.

### `GET /api/drafts/cv/<cv_id>` · `GET /api/drafts/letter/<letter_id>`

Returns one stored draft, or `404`.

## Applications

### `GET /api/applications`

Optional `?status=applied`. Returns `applications` (up to 200), `stats`, and the canonical `statuses` list.

### `POST /api/applications`

```json
{ "job_id": "job_…", "status": "applied", "notes": "Referred by Sam" }
```

Creates a local tracking record. **Nothing is submitted.** Returns the `application`; invalid job or status returns `400`.

### `PATCH /api/applications/<application_id>`

```json
{ "status": "interview", "note": "Recruiter call booked" }
```

Updates status and appends a history event.

### `DELETE /api/applications/<application_id>`

Returns `{ "ok": true }` when removed, `false` otherwise.

## Memory

### `GET /api/memories`

```json
{
  "ok": true,
  "memories": [{ "id": "mem_…", "kind": "preference", "content": "Remote only", "status": "active" }],
  "pending": [{ "id": "mem_…", "status": "pending" }],
  "stats": { "active": 4, "pending": 2 }
}
```

`memories` contains active memories; `pending` is the confirmation queue. Aggregate counts for both are in `stats`.

### `POST /api/memories`

```json
{ "content": "I only want remote roles", "kind": "preference" }
```

`kind` is optional. The write policy classifies the text, merges near-duplicates, flags conflicts, and may mark the memory `pending`.

Response includes `action` (`created`, `merged`, `conflict`, or `pending_confirmation`), a human `message`, and the stored `memory`. A conflict is stored as pending for review.

### `POST /api/memories/<memory_id>/approve`

Promotes a pending memory to active. `404` if missing.

### `DELETE /api/memories/<memory_id>`

Permanently forgets the memory. Returns `{ "ok": true }` on success, `false` if it was missing or already forgotten.

## Approvals

### `GET /api/approvals`

Returns all recent approvals (up to 50) plus the `pending` count.

### `POST /api/approvals/<approval_id>/decide`

```json
{ "approve": true }
```

On approval, the stored tool arguments are replayed through `execute_approval()`. The response includes the updated `approval` and an `execution` result. On rejection, nothing runs.

Returns `404` if the approval is unknown, `409` if it was already decided.

## Agent and sessions

### `POST /api/agent/run`

```json
{ "message": "Find remote AI engineer roles and tailor my CV for the top one", "session_id": "", "mode": "auto" }
```

`mode` is `auto` or `local`. An empty `session_id` starts a new session.

```json
{
  "ok": true,
  "reply": {
    "run_id": "run_…",
    "session_id": "ses_…",
    "status": "awaiting_approval",
    "message": "I drafted a CV and prepared one approval.",
    "steps": [{ "index": 0, "tool": "search_jobs", "ok": true, "latency_ms": 412.3 }],
    "approvals": [{ "id": "apr_…", "tool": "request_submission", "status": "pending" }],
    "artifacts": { "cv": { "id": "cv_…" } },
    "mode": "local",
    "tokens": 0,
    "cost": 0.0
  }
}
```

`status` is `completed`, `awaiting_approval`, or `failed`. If the gateway is unavailable or the budget is exhausted, the harness falls back to the local planner and reports `mode: "local-fallback"`. The endpoint returns `429` only when the failure cannot be recovered.

### Sessions

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/sessions` | 30 most recent sessions |
| POST | `/api/sessions` | Create `{ "title": "…" }` |
| GET | `/api/sessions/<id>` | Session metadata and messages |
| DELETE | `/api/sessions/<id>` | Delete session and messages |

## Runs, sources, tools

### `GET /api/runs`

Returns the 50 newest runs and aggregate stats.

### `GET /api/runs/<run_id>`

Returns the run record and its full `trace` events.

### `GET /api/sources`

Cached source health from `registry.doctor(force=False)`.

### `POST /api/sources/check`

Forces a live probe of every source. Can take several seconds.

### `GET /api/tools`

Returns the tool catalog: name, namespace, description, tier, and parameter schema.

## Client examples

PowerShell:

```powershell
$body = @{ query = "AI engineer"; remote_only = $true; limit = 10 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/api/jobs/search `
  -ContentType "application/json" -Body $body
```

curl:

```bash
curl -s http://127.0.0.1:5000/api/applications | jq '.applications[].job.title'
```

Python:

```python
import httpx

reply = httpx.post(
    "http://127.0.0.1:5000/api/agent/run",
    json={"message": "What do you remember about my preferences?"},
    timeout=60,
).json()
print(reply["reply"]["message"])
```

## Approval lifecycle

```
agent proposes external action
        │
        ▼
POST /api/agent/run → approvals:[{id, tool, args, status:"pending"}]
        │
        ├── POST /api/approvals/<id>/decide {"approve": false}
        │        → status "rejected", no execution
        │
        └── POST /api/approvals/<id>/decide {"approve": true}
                 → status "approved"
                 → execute_approval(stored args)
                 → execution result returned
                 → trace event "approval"
```

A decided approval cannot be replayed through the API; `409` protects against double execution.
