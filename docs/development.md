# Development guide

## Environment

```powershell
# Windows
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e ".[dev]"
cp .env.example .env
```

The editable install exposes the `jobpilot` console script and puts `src/` on the import path. `pytest` is configured with `pythonpath = ["src"]` and `testpaths = ["tests"]`.

## Everyday commands

| Task | Command |
| --- | --- |
| Run the app | `jobpilot serve` or `flask --app jobpilot.ui.app run --debug` |
| Interactive agent | `jobpilot chat` |
| Check runtime/sources | `jobpilot doctor --force` |
| Initialize workspace | `jobpilot init` |
| Reset local state | `jobpilot reset --yes` |
| Tests | `python -m pytest -q` |
| One test file | `python -m pytest tests/test_services.py -q` |
| Coverage | `python -m pytest --cov=jobpilot --cov-report=term-missing` |
| Evaluation | `python -m eval.run_eval` |
| Eval JSON | `python -m eval.run_eval --json` |

## Search performance

A live search fans out to every enabled source in parallel, then does all of its text work on the main thread. Two things dominate that second phase, and both are covered by tests:

- **Skills and category matching are index lookups, not regex scans.** `pipelines/extract.py` tokenises a document once and looks up whole tokens and short phrases in `_SKILL_INDEX`; `pipelines/taxonomy.py` does the same with `_TERM_INDEX`. Running one compiled regex per alias or per category term was 70s and 12s of CPU for a 400-posting search respectively; the index forms are 1.6s and 1.2s. When adding skills or category terms, they must be plain words or short phrases to hit the fast path — terms with punctuation (`c++`, `ci/cd`) still fall back to a single regex.
- **The HTTP layer is pooled.** One `httpx.Client` per adapter keeps connections alive; building a client per call re-reads the certificate store and re-handshakes TLS.

Measure with `time.perf_counter()` around phases. `cProfile` cumtimes are misleading here because the work is spread across threads.

## Testing rules

- Every test runs against a temporary SQLite file and temporary trace directory (`tests/conftest.py`).
- `OPENAI_API_KEY` is forced empty, `EMBEDDING_PROVIDER=local`, and sources are replaced by `FakeRegistry`.
- **No test may reach the network.** If you need HTTP behavior, add a fixture or monkeypatch the adapter.
- Use the `services`, `sample_profile`, `sample_job`, and `client` fixtures instead of constructing singletons.
- `sample_profile` loads `eval/dataset/profile.json` through `eval.fixtures.load_eval_profile()`, so tests and the benchmark share one stable persona without shipping sample data in the product.
- Reset module singletons between tests; `reset_singletons()` in `conftest.py` covers the known globals.

When adding a service singleton, add it to `reset_singletons()` or tests will leak state.

## Docker

Docker is optional and uses the same image for local runs and deployment smoke tests.

```bash
cp .env.example .env
docker compose up --build
```

- The app is published on `127.0.0.1:5055` and Flask runs with `DEBUG=0`.
- `DATA_DIR` and `TRACE_DIR` point at `/app/data` and `/app/traces`, mounted from the repository so state survives container restarts.
- Compose reads `.env` when present for secrets and source settings (optional `env_file` requires Compose v2.24+). Change `SECRET_KEY` before sharing the app.
- Rebuild after changing `pyproject.toml` or source: `docker compose up --build`.
- Stop with `docker compose down`; add `-v` only if you also want to remove named volumes.

The container healthcheck calls `GET /api/health` every 30 seconds. The compose port mapping is deliberately bound to localhost; set a strong `SECRET_KEY` before exposing the app beyond your machine.

### Example: service test

```python
def test_memory_merge(services):
    first = services.memory.remember("I only want remote roles", kind="preference", confirmed=True)
    second = services.memory.remember("I only want remote roles", kind="preference", confirmed=True)
    assert first.memory is not None
    assert second.action in ("merged", "duplicate")
```

### Example: API test

```python
def test_search_endpoint(client, sample_job):
    response = client.post("/api/jobs/search", json={"query": "AI engineer", "limit": 5})
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
```

## Adding a source adapter

1. Subclass `BaseSource` in `src/jobpilot/sources/`.
2. Set `name`, `priority`, `requires_key`, and `enabled` (see below).
3. Implement `_probe()` and `fetch()`.
4. Return `RawJob` objects; the registry handles extraction, dedupe, and storage.
5. Register the adapter in `SourceRegistry.__init__`.
6. Add a settings flag if the source should be switchable, plus `.env.example` and README rows.
7. Add it to `jobpilot doctor` expectations and a test with a fake response.

```python
from jobpilot.core.models import JobQuery, RawJob
from jobpilot.sources.base import BaseSource, excerpt


class ExampleSource(BaseSource):
    name = "example"
    priority = 2
    requires_key = False

    @property
    def enabled(self) -> bool:
        # Class attributes cannot read settings; a property can.
        return self.settings.enable_example

    def _probe(self) -> None:
        self.get_json("https://api.example.com/health")

    def fetch(self, query: JobQuery) -> list[RawJob]:
        payload = self.get_json(
            "https://api.example.com/jobs",
            params={"q": query.query, "limit": self._limit(query)},
        )
        return [
            RawJob(
                source=self.name,
                external_id=str(item["id"]),
                title=item["title"],
                company=item.get("company", ""),
                location=item.get("location", ""),
                description=item.get("description", ""),
                url=item.get("url", ""),
                remote=bool(item.get("remote")),
                tags=item.get("tags", []),
                raw=item,
            )
            for item in payload.get("results", [])
        ]
```

Guidelines:

- **Use the shared client.** `self.get_json()` and `self.get_text()` go through one pooled `httpx.Client` per adapter. Do not build a client per request: that reloads the certificate store and forces a new TLS handshake every call. If you need a custom request, use `self._client()` and let the adapter own it.
- **Do not retry transport failures.** A second 20-second wait inside a 45-second budget starves every other source. Retry only on an HTTP status the API documents as transient (for example, a rejected filter parameter), and only when the retry is cheap.
- Never raise out of `fetch()` for a single bad record; skip it and keep going.
- The registry catches per-source exceptions and records `status: "error"` with the message; a source that outlives `SOURCE_BUDGET_SECONDS` is recorded as `timeout` and the rest of the results still return.
- Declare geography honestly: set `workplace_type`, and fill `eligible_locations`/`remote_scope` only from what the source states. Unknown eligibility must stay empty so the service can label the role `unverified`.
- Respect rate limits and terms of service.
- Keep `_probe()` cheap; it is called by `doctor` and cached for 300 seconds.
- Prefer official APIs. Do not add browser automation without a clear opt-in and legal review.

### Adding a search source flag

```python
# core/config.py
enable_example: bool = True
```

```dotenv
# .env.example
ENABLE_EXAMPLE=1
```

Adapters read the flag through `self.settings` inside the `enabled` property, so a search can be narrowed without editing the registry.


## Adding a tool

Tools live in `services/tools.py`. A tool needs:

- a unique name and namespace,
- a description written for a model,
- a permission tier,
- a JSON Schema,
- a handler.

```python
add(
    "count_saved_jobs",
    "jobs",
    "Count jobs the user has saved.",
    READ,
    _obj({}),
    lambda ctx, **_: {"count": self.jobs.stats()["saved"]},
)
```

Rules:

- Handlers receive `ctx: ToolContext` first and keyword arguments after validation.
- Raise `ToolError` for expected failures; the loop converts it to a tool observation.
- Never perform an external action in a `write_local` handler.
- Add the tool to tests that assert the tier and schema.
- If the tool should be available over MCP, add it to the relevant server too.

`ToolRegistry.validate()` drops unknown keys and coerces primitives, but it does not enforce `minimum`/`maximum` or nested object schemas. Enforce those in the handler or extend `validate()` with a real JSON Schema library.

## Adding an MCP server

1. Create `src/jobpilot/servers/<name>_server.py`.
2. Create the server with `create_server(name, instructions)`.
3. Decorate functions with `@server.tool()`.
4. Reuse services; do not duplicate business logic.
5. Add `main()` and the `if __name__ == "__main__": main()` guard.
6. Import the module in `cmd_doctor` so it is checked at startup.
7. Document the tools in the README table.

```python
from jobpilot.servers.mcp_compat import create_server, run_server

server = create_server("jobpilot-example", instructions="One clear sentence.")


@server.tool()
def ping() -> dict:
    """Return a health payload."""
    return {"ok": True}


def main() -> None:
    run_server(server)


if __name__ == "__main__":
    main()
```

The compatibility shim supports MCP SDK v1 (`FastMCP`) and v2 (`MCPServer`). Test with the installed version (`mcp 2.3.0` here).

## Adding a page

1. Add a route in `create_app()` in `ui/app.py`.
2. Create `ui/templates/<page>.html` extending `base.html`.
3. Add `<script src="{{ url_for('static', filename='js/<page>.js') }}">` in the page's scripts block.
4. Use shared helpers from `static/js/app.js` (`api`, toasts, modals, formatting).
5. Add the nav item in `base.html` if it is a top-level page.
6. Follow the existing CSS variables and components in `static/css/style.css`; do not introduce a framework.
7. Verify both dark and light themes and a narrow viewport.

Page conventions:

- Set `active` and `title` in the template context.
- Put page-specific state in `data-*` attributes or a small page script.
- Keep fetch calls in the page script; shared error handling stays in `app.js`.
- Never interpolate untrusted job text into `innerHTML`; use text nodes or an escape helper.

## Configuration changes

1. Add the field to `Settings` in `core/config.py` with a safe default.
2. Add it to `.env.example` with a comment if users should tune it.
3. Add it to `public_dict()` only if it is safe to show in the UI.
4. Document it in the README table.
5. Add a test if the default or validator matters.

## Code style

- Python 3.11+, `from __future__ import annotations`, type hints on public functions.
- Pydantic models for data crossing a boundary (API, storage, tools).
- Explicit SQL with parameters; no string-built values.
- Pure functions for guardrails and scoring so they are easy to test.
- Keep modules focused; services orchestrate, pipelines transform.
- Prefer standard library and the existing dependencies. Adding a dependency needs a clear reason.
- No Node build step. Vanilla JS and CSS only.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `jobpilot: command not found` | Virtualenv not active or editable install missing | Activate `.venv`, then `pip install -e ".[dev]"` |
| PowerShell blocks activation | Execution policy | `Set-ExecutionPolicy -Scope Process Bypass` |
| Unicode errors in the console | Windows code page | `$env:PYTHONIOENCODING = "utf-8"` |
| Port already in use | Another server is running | `jobpilot serve --port 5055` |
| Search returns nothing | Sources blocked or query too narrow | `jobpilot doctor --force`, broaden the query, import manually |
| Search is slow or one source reports `timeout` | A public source is stalling | Check `source_runs` in the response; lower `SOURCE_BUDGET_SECONDS` or disable that source (`ENABLE_*`) |
| Resume upload says no text found | Scanned/image PDF, and OCR is not installed | Use a text-based PDF or paste the CV text instead |
| LLM replies never happen | No key, or budget exhausted | Check Settings → runtime; local mode is expected without a key |
| Tests try to use the real DB | A singleton leaked | Add it to `reset_singletons()`; use the `db` fixture |
| Stale local state | Previous database or traces | `jobpilot reset --yes` then `jobpilot init` |
| Traces missing | `TRACE_DIR` not writable | Check the path and permissions |

## Before opening a pull request

```bash
python -m pytest -q
python -m eval.run_eval
jobpilot doctor
```

- All tests pass.
- All eval gates pass.
- `doctor` is clean (source warnings are acceptable if a public API is down).
- No secrets, database files, or traces are staged.
- README or docs updated for user-visible behavior.
