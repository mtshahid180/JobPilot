"""``jobpilot`` command line: doctor, search, chat, serve, eval, init, reset."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from jobpilot.core.config import get_settings
from jobpilot.core.context import set_current_user_id
from jobpilot.core.db import get_db
from jobpilot.core.models import JobQuery
from jobpilot.services.users import resolve_workspace_user

BANNER = r"""
   _  ___  ___  ___  ___  _  _____
  | |/ _ \| _ )| _ \/ _ \| ||_   _|
  |_| (_) | _ \|  _/ (_) | |__| |
      \___/|___/|_|  \___/|____|_|
  local-first AI job-search agent
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _configure_output() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass


def _heading(text: str) -> None:
    print(f"\n{text}")
    print("-" * len(text))


def _table(rows: list[list[str]], headers: list[str]) -> None:
    if not rows:
        print("  (nothing to show)")
        return
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    print(line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)))


def _truncate(value: str, width: int) -> str:
    value = str(value)
    return value if len(value) <= width else value[: width - 1] + "…"


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    from jobpilot.harness.llm import get_gateway
    from jobpilot.services.jobs import get_jobs_service
    from jobpilot.services.profile import ProfileService
    from jobpilot.services.tools import tool_catalog

    settings = get_settings()
    failures = 0

    _heading("Runtime")
    _table(
        [
            ["environment", settings.environment],
            ["database", str(settings.db_path)],
            ["traces", str(settings.trace_dir)],
            ["llm", "enabled" if settings.llm_enabled else "local-only"],
            ["embedding", settings.embedding_provider],
        ],
        ["key", "value"],
    )

    _heading("Database")
    health = get_db().health()
    if health.get("ok"):
        _table(
            [
                ["size", f"{health.get('size_kb', 0)} KB"],
                ["jobs", health.get("jobs", 0)],
                ["applications", health.get("applications", 0)],
                ["memories", health.get("memories", 0)],
            ],
            ["key", "value"],
        )
    else:
        failures += 1
        print(f"  FAIL  {health.get('error')}")

    _heading("Profile")
    profile = ProfileService().get()
    print(f"  {profile.full_name} · {profile.completeness()}% complete · {len(profile.skills)} skills")
    if profile.completeness() < 50:
        print("  hint: run `jobpilot serve` and finish the setup screen")

    _heading("Budget")
    budget = get_gateway().budget_snapshot()
    _table(
        [
            ["spent total", f"${budget['spent_total']:.4f}"],
            ["spent today", f"${budget['spent_today']:.4f}"],
            ["remaining", f"${budget['remaining']:.2f}"],
        ],
        ["key", "value"],
    )

    _heading("Tools")
    tools = tool_catalog()
    by_tier: dict[str, int] = {}
    for tool in tools:
        by_tier[tool["tier"]] = by_tier.get(tool["tier"], 0) + 1
    print(f"  {len(tools)} tools · " + " · ".join(f"{tier}: {count}" for tier, count in sorted(by_tier.items())))

    _heading("Sources")
    rows = []
    for status in get_jobs_service().registry.doctor(force=bool(args.force)):
        probe = status.last_probe
        rows.append(
            [
                status.name,
                status.status,
                f"{round(probe.latency_ms)}ms" if probe and probe.latency_ms else "—",
                _truncate(probe.detail if probe else "", 44),
            ]
        )
        if status.enabled and probe and not probe.ok:
            failures += 1
    _table(rows, ["source", "status", "latency", "detail"])
    if args.force:
        print("\n  (forced probe completed)")

    _heading("MCP servers")
    from jobpilot.servers import jobs_server, match_server, memory_server, profile_server, tailor_server, tracker_server

    for module in (jobs_server, profile_server, memory_server, match_server, tailor_server, tracker_server):
        print(f"  ok  {module.__name__}")

    print()
    if failures:
        print(f"doctor finished with {failures} warning(s)")
        return 1
    print("doctor finished clean")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from jobpilot.services.jobs import get_jobs_service
    from jobpilot.services.match import get_match_service
    from jobpilot.services.profile import ProfileService

    query = JobQuery(
        query=args.query,
        location=args.location,
        remote_only=args.remote,
        limit=args.limit,
    )
    print(f"searching sources for: {args.query or '(all roles)'}" + (" · remote only" if args.remote else ""))
    jobs, meta = get_jobs_service().search(query)
    profile = ProfileService().get()
    matcher = get_match_service()
    scored = sorted(
        ((job, matcher.score_job(job, profile)) for job in jobs),
        key=lambda pair: pair[1].score,
        reverse=True,
    )
    _heading(f"Results · {len(scored)} roles")
    _table(
        [
            [f"{index + 1:>2}", _truncate(job.title, 44), _truncate(job.company, 22), f"{match.score:.1f}", job.location or "—", job.source]
            for index, (job, match) in enumerate(scored[: args.limit])
        ],
        ["#", "role", "company", "match", "location", "source"],
    )
    if meta.get("errors"):
        print(f"\n  source warnings: {len(meta['errors'])}")
    if args.json:
        payload = [
            {
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "remote": job.remote,
                "score": match.score,
                "url": job.url,
                "source": job.source,
            }
            for job, match in scored[: args.limit]
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    from jobpilot.harness.loop import get_agent_loop

    _configure_output()
    print(BANNER)
    print("Type your goal. Commands: /exit, /new, /mode local|auto\n")
    loop = get_agent_loop()
    session_id = ""
    mode = args.mode
    while True:
        try:
            message = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return 0
        if not message:
            continue
        if message in ("/exit", "/quit"):
            print("bye")
            return 0
        if message == "/new":
            session_id = ""
            print("(new session)")
            continue
        if message.startswith("/mode"):
            parts = message.split()
            if len(parts) == 2 and parts[1] in ("local", "auto"):
                mode = parts[1]
                print(f"(mode: {mode})")
            else:
                print("usage: /mode local|auto")
            continue
        try:
            reply = loop.run(message, session_id=session_id, mode=mode)
        except Exception as exc:  # noqa: BLE001 - CLI should not crash
            print(f"error › {exc}")
            continue
        session_id = reply.session_id or session_id
        print(f"\njobpilot › {reply.message}\n")
        for step in reply.steps:
            status = "ok" if step.ok else "fail"
            print(f"  [{status}] {step.tool:<22} {_truncate(step.observation, 88)}")
        if reply.approvals:
            print()
            for approval in reply.approvals:
                print(f"  approval required: {approval.tool} ({approval.id})")
        print(f"  run {reply.run_id} · mode {reply.mode} · {reply.tokens} tokens · ${reply.cost:.4f}\n")


def cmd_serve(args: argparse.Namespace) -> int:
    from jobpilot.ui.app import create_app

    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    debug = settings.debug if args.debug is None else args.debug
    print(BANNER)
    print(f"serving JobPilot on http://{host}:{port}  (Ctrl+C to stop)")
    create_app().run(host=host, port=port, debug=debug, use_reloader=debug)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    try:
        from eval.run_eval import main as run_eval_main
    except ImportError as exc:
        print(f"evaluation suite is not available yet: {exc}")
        return 1
    argv = ["--dataset", args.dataset] if args.dataset else []
    if args.k:
        argv += ["--k", str(args.k)]
    if args.json:
        argv.append("--json")
    return int(run_eval_main(argv) or 0)


def cmd_init(args: argparse.Namespace) -> int:
    from jobpilot.services.profile import ProfileService

    settings = get_settings()
    get_db().init()
    profile = ProfileService().get()
    print(f"workspace ready · database: {settings.db_path}")
    print(f"traces: {settings.trace_dir}")
    if not profile.full_name.strip() or profile.full_name.strip() == "Your Name":
        print("profile is empty — run `jobpilot serve`, then finish the setup screen")
    else:
        print(f"profile: {profile.full_name} ({profile.completeness()}% complete)")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not args.yes:
        answer = input(f"Delete {settings.db_path} and all traces? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("cancelled")
            return 0
    removed = 0
    for path in (Path(settings.db_path), Path(str(settings.db_path) + "-wal"), Path(str(settings.db_path) + "-shm")):
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except OSError as exc:
            print(f"could not remove {path}: {exc}")
    trace_dir = Path(settings.trace_dir)
    if trace_dir.exists():
        for file in trace_dir.glob("*.jsonl"):
            try:
                file.unlink()
                removed += 1
            except OSError:
                pass
    get_db().init()
    print(f"reset complete · removed {removed} file(s) · fresh database created")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobpilot",
        description="Local-first, MCP-powered job-search agent.",
    )
    parser.add_argument("--version", action="version", version="jobpilot 0.1.0")
    sub = parser.add_subparsers(dest="command")

    doctor = sub.add_parser("doctor", help="check runtime, sources, tools and budget")
    doctor.add_argument("--force", action="store_true", help="probe every source even if cached")
    doctor.set_defaults(func=cmd_doctor)

    search = sub.add_parser("search", help="search live sources and rank results")
    search.add_argument("query", nargs="?", default="", help="role, skill or company")
    search.add_argument("--location", default="", help="location filter")
    search.add_argument("--remote", action="store_true", help="remote roles only")
    search.add_argument("--limit", type=int, default=20, help="number of results (default 20)")
    search.add_argument("--json", action="store_true", help="print results as JSON")
    search.set_defaults(func=cmd_search)

    chat = sub.add_parser("chat", help="interactive agent console")
    chat.add_argument("--mode", choices=["local", "auto"], default="auto")
    chat.set_defaults(func=cmd_chat)

    serve = sub.add_parser("serve", help="run the Flask UI")
    serve.add_argument("--host", default="")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--debug", action="store_true", default=None)
    serve.set_defaults(func=cmd_serve)

    ev = sub.add_parser("eval", help="run the offline evaluation suite")
    ev.add_argument("--dataset", default="", help="path to a JSONL dataset")
    ev.add_argument("--k", type=int, default=5, help="ranking cut-off")
    ev.add_argument("--json", action="store_true", help="emit JSON")
    ev.set_defaults(func=cmd_eval)

    init = sub.add_parser("init", help="create the local database and workspace directories")
    init.set_defaults(func=cmd_init)

    reset = sub.add_parser("reset", help="delete the database and traces")
    reset.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    reset.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    if args.command not in (None, "serve"):
        try:
            set_current_user_id(resolve_workspace_user())
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 2
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
