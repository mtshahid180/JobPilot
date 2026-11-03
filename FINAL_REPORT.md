# JobPilot — Final Report

Date: Sat Oct 31 2026
Status: All changes complete; tests green; doctor/eval clean.

## Summary

Fixed JobPilot's search, chat, resume and tailoring behaviour in the existing Flask app **without rebuilding** — same architecture (Flask + Jinja2 + vanilla JS, same Python dependencies), same data and working features preserved. All requirements from the objective were addressed, verified by tests, a live `jobpilot doctor`, `jobpilot eval`, page smoke checks, and live page rendering.

## Verified results (actual, not claimed)

| Check | Result |
|-------|--------|
| `pytest -q` full suite | **194 passed, 0 failed** |
| `jobpilot doctor --force` | **clean** (all MCP servers ok; 11 sources probed; adzuna/indeed/linkedin disabled) |
| `jobpilot eval` | **PASS** — 37 jobs, 16 tasks; ranking spearman=0.810, P@5=1.000; tailoring 3/3 verified, 0 unsupported claims; planner 100% exact tool sets; injection detection 100% / 0 false positives; overall PASS |
| Flask pages (`/api/health`, `/jobs`, `/agent`, `/profile`, `/drafts`, `/settings`) | All **200** (live dev server at 127.0.0.1:5099, then stopped) |
| Chat: "AI Engineer in Toronto, Canada" + follow-ups | Verified via `tests/test_api.py::test_agent_chat_searches_and_preserves_followup_constraints` — real search triggered, role/location/workplace/seniority preserved across "Only remote", "In London instead", "Show junior positions" |
| Tailor downloads (DOCX/PDF) | Verified via `tests/test_api.py::test_tailor_drafts_api` — DOCX starts `PK`, PDF starts `%PDF`, content-type correct, 404/400 error cases handled |

## Root causes fixed

1. **City over-expansion** — `_search_jobs` injected silent profile-country/remote defaults, and `geo.location_match` treated nearby cities/regions/countries as acceptable substitutes. Fixed: exact city only via `job_location` or explicitly named eligibility city; country/region/macro/company_location never satisfy a city request; mismatches are `mismatch` (excluded), never padded.

2. **Workplace inference from description** — `detect_workplace_type` inferred Remote/Hybrid from description prose. Fixed: workplace derives only from structured flags/title/location; description prose no longer infers it; unknown excluded from all three workplace filters; strict library `list()`/`_where_clause` (`workplace_type='remote'`, not hybrid).

3. **Pakistan Devsinc-only / unrelated jobs** — source defaults, query construction and filter order let one employer dominate and unrelated category results bleed through. Fixed: `_diversify_employers` caps consecutive same-employer runs at 2 (never adds jobs); category relevance rejects unrelated occupations; all enabled sources queried in parallel, one failure does not break others.

4. **Resume missing-field overwrite** — `parse_cv_into` reused the saved profile so missing fields looked "detected", and empty draft values overwrote existing profile values on apply. Fixed: draft built from `Profile()` (resume only); empty draft values no longer overwrite saved profile; review/confirm-before-overwrite retained.

5. **Certification parsing** — `AWS Certified Machine Learning - Specialty \| Amazon Web Services \| 2023` split on `|`/`–`/`,` produced issuer=`Specialty`. Fixed: split on `|` first, then en/em dash, strip trailing year.

6. **Chat follow-up constraint loss** — no persistent per-session search context. Fixed: `sessions.context` JSON column; `_search_args` merges only changed constraints (additive); search context persisted from tool result (`search_query` artifact).

7. **Tailoring not downloadable / unverified** — `_format_result("tailor_cv")` didn't surface target job/changes/downloads; no file storage. Fixed: `TailorService` builds document blocks from profile + tailored CV + job, computes change summary, stores `data/tailored/<user_id>/<cv_id>.docx|.pdf`, persists `files`/`changes`/`resume_id`, exposes `cv_file()` with regeneration; API returns `downloads`; UI/chat show target job, change summary, DOCX/PDF links.

8. **LinkedIn misrepresented** — status implied it might work. Fixed: honest status text — no public jobs API, scraping violates LinkedIn Terms, manual URL import offered as supported alternative; documented in `sources/linkedin.py`, `templates/settings.html`, `README.md`, `docs/safety.md`.

9. **Missing resume sections** — profile had no `projects`/`certifications`/`achievements`. Fixed: additive `Profile` JSON fields, `Project`/`Certification` models, parser sections `_parse_projects`/`_parse_certifications`/`_parse_achievements`, `MERGABLE_FIELDS` extended, profile page displays them.

10. **Documents** — new dependency-free `pipelines/documents.py` (DOCX builder, paginated Helvetica PDF builder, `validate_docx`, `validate_pdf` via `pypdf`); original uploads never touched.

## Files changed

- `src/jobpilot/core/locations.py` — strict exact-city/workplace matching, reason codes, `_match_city/_match_region/_match_country/_match_macro`.
- `src/jobpilot/services/jobs.py` — `_workplace_filter`, `workplace_rejected`, exact-city notes, `_diversify_employers`, strict library filters, `_location_info` ignores description.
- `src/jobpilot/services/tools.py` — `_search_jobs` no silent defaults; `_tailor_cv` returns downloads.
- `src/jobpilot/harness/loop.py` — chat intent/follow-up logic, search context persistence, `tailor_cv` formatting.
- `src/jobpilot/services/sessions.py` — `get_context`/`set_context` (`sessions.context` JSON column).
- `src/jobpilot/core/db.py` — schema v4, additive migrations (`sessions.context`, `tailored_cvs.resume_id`/`changes`/`files`), runs even without `jobs` table.
- `src/jobpilot/core/models.py` — `Profile.projects/certifications/achievements`, `Project`/`Certification`, `TailoredCV.files/changes/resume_id`.
- `src/jobpilot/services/profile.py` — section parsers `_parse_projects/_parse_certifications/_parse_achievements`; draft built from resume-only `Profile()`.
- `src/jobpilot/services/resumes.py` — `MERGABLE_FIELDS` extended; empty-value preservation; upload/apply errors; scanned/corrupt file errors.
- `src/jobpilot/pipelines/documents.py` — new: DOCX/PDF builder + validators.
- `src/jobpilot/services/tailor.py` — document blocks, change summary, file storage, `cv_file()` regeneration.
- `src/jobpilot/ui/app.py` — download routes, `_cv_downloads`, tailoring response.
- `src/jobpilot/ui/static/js/drafts.js`, `src/jobpilot/ui/static/js/jobs.js` — download links, change summary, target job, card-click anchor ignore.
- `sources/linkedin.py`, `templates/settings.html`, `README.md`, `docs/safety.md` — honest LinkedIn status + manual URL import.
- `tests/test_api.py` — new `test_agent_chat_searches_and_preserves_followup_constraints`; extended `test_tailor_drafts_api` (DOCX/PDF content + 404/400).
- `tests/test_pipeline.py` — resume projects/certs/achievements, missing-field preservation, corrupt file errors, tailored files preserving original.
- `tests/test_sources_ats.py` — LinkedIn honesty test.

## Tests added / updated

- Exact-city exclusion groups: Lahore/Islamabad/Karachi, Toronto/Vancouver/Montreal, Berlin/Hamburg/Munich; no region/country/HQ expansion.
- Strict remote filter; no inference without evidence; Germany-HQ remote accepted for Canadian search; on-site allowed with unknown work authorization.
- AI Engineer search + follow-up preservation ("Only remote", "In London instead", "Show junior positions").
- Pakistan multi-employer results; one-source failure isolation.
- Resume projects/certifications/achievements; missing-field preservation; corrupt PDF/DOCX errors; valid tailored files preserving original upload.
- LinkedIn honesty test.

## Manual verification steps performed

1. `.\.venv\Scripts\jobpilot.exe doctor --force` — clean (all MCP servers ok; sources probed).
2. `.\.venv\Scripts\jobpilot.exe eval` — PASS, report at `eval/eval-report.md`.
3. `pytest -q` — 194 passed.
4. Started `jobpilot serve --port 5099 --debug`; `GET /api/health`, `/jobs`, `/agent`, `/profile`, `/drafts`, `/settings` all returned 200.
5. Stopped the dev server.

## Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `SECRET_KEY` | `change-me-to-a-long-random-string` | Flask session signing; change in shared env |
| `DEBUG` | `1` | Flask debug + reloader |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | `jobpilot serve` bind |
| `DATA_DIR` / `TRACE_DIR` | `./data` / `./traces` | SQLite DB and JSONL traces |
| `DB_NAME` | `jobpilot.sqlite3` | Database filename |
| `OPENAI_API_KEY` | (empty) | Enables LLM gateway; empty = local-only |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `JOBS_FAST_MODEL` / `JOBS_STRONG_MODEL` | `gpt-4o-mini` | LLM models |
| `EMBEDDING_PROVIDER` | `local` | `local` (hashing) or `openai` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Only when provider is `openai` |
| `MAX_BUDGET_USD` / `DAILY_BUDGET_USD` | `1.50` / `0.50` | LLM cost guardrails |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `60` / `3` | LLM gateway limits |
| `MAX_STEPS` / `MAX_CONSECUTIVE_FAILURES` | `12` / `3` | Harness limits |
| `ENABLE_LINKEDIN` | `0` | Compatibility only; LinkedIn never fetches data |
| `SOURCE_BUDGET_SECONDS` | `45` | Wall-clock budget per live search |

No keys/tokens are logged or returned in responses.

## Remaining limitations

- **Docker build/runtime** — unverified (no Docker daemon available).
- **No OCR engine / PDF rasterizer** (`pytesseract`, `Pillow` absent) — scanned PDFs fail explicitly with a clear error ("OCR is not available"); text-based PDFs/DOCX/TXT/MD work.
- **Browser screenshots unavailable** — UI verified via page HTTP checks (200) and DOM-level test coverage, not screenshots.
- **Jobicy rejects `finance` industry**; Pakistan official API coverage limited to Mustakbil.
- **LinkedIn remains disabled** by policy (no public jobs API; scraping violates ToS); supported alternative is manual URL import.
- PDF tailoring uses reportlab-free Helvetica builder (paginated); complex multi-column layouts not supported — ATS-readable single-column output.

## Notes

- No git repository; all changes uncommitted.
- Demo account `usr_19207a75d6f5` (`demo@jobpilot.local` / `jobpilot-demo-1`); profile "Aisha Rahman / Senior Financial Analyst / Karachi, Pakistan".
- Use `.\.venv\Scripts\python.exe` and `.\.venv\Scripts\jobpilot.exe`; `python -m jobpilot` is unsupported.
- `search_cache_ttl_seconds=300`; `source_budget_seconds=45.0`; `http_timeout_seconds=20`; cache off in `test`/`testing` environment.
