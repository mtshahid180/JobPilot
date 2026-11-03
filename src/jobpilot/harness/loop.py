"""The JobPilot agent loop.

Plan → act → observe, with hard step caps, permission checks, guardrail-aware
tool results, retrieval-backed context, and a full trace for every run.

Without an API key the loop uses a deterministic planner that still exercises
the same tools, permissions and traces. With a key it switches to native LLM
tool calling.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from jobpilot.core.config import Settings, get_settings
from jobpilot.core.db import Database, get_db
from jobpilot.core.models import (
    AgentReply,
    Approval,
    Job,
    Profile,
    Run,
    RunStep,
    new_id,
)
from jobpilot.harness.context import ContextManager
from jobpilot.harness.llm import (
    BudgetExceeded,
    Gateway,
    LLMUnavailable,
    get_gateway,
    parse_json,
)
from jobpilot.harness.permissions import (
    ApprovalService,
    PermissionPolicy,
)
from jobpilot.harness.tracing import RunStore, Tracer
from jobpilot.memory.summarizer import summarize_messages
from jobpilot.pipelines import taxonomy
from jobpilot.services.jobs import JobsService
from jobpilot.services.match import MatchService
from jobpilot.services.memory import MemoryService
from jobpilot.services.profile import ProfileService
from jobpilot.services.sessions import SessionService
from jobpilot.services.tailor import TailorService
from jobpilot.services.tools import ToolContext, ToolError, ToolRegistry, get_tool_registry
from jobpilot.services.tracker import TrackerService

HELP_TEXT = (
    "I can search live job sources, score roles against your profile, tailor a "
    "verified CV or cover letter, track applications, and manage long-term memory.\n\n"
    "Try:\n"
    "• “Find AI Engineer jobs in Islamabad or remote”\n"
    "• “Tailor my CV for the top match”\n"
    "• “Draft a cover letter for job_…”\n"
    "• “Remember that I only want remote roles”\n"
    "• “What do you remember about my preferences?”\n"
    "• “Show my applications and follow-ups”\n"
    "• “Check my job sources”"
)

STOPWORDS = {
    "a", "an", "the", "my", "me", "for", "to", "and", "or", "of", "in", "on",
    "with", "this", "that", "please", "can", "you", "job", "jobs", "role",
    "roles", "top", "best", "one", "latest", "new", "find", "search", "my",
}


@dataclass
class ToolOutcome:
    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    approval: Approval | None = None
    latency_ms: float = 0.0
    error: str = ""


class AgentLoop:
    def __init__(
        self,
        settings: Settings | None = None,
        db: Database | None = None,
        gateway: Gateway | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db = db or get_db()
        self.gateway = gateway or get_gateway()
        self.profiles = ProfileService(self.db)
        self.jobs = JobsService(self.db)
        self.match = MatchService(self.db, self.jobs, self.profiles)
        self.tailor = TailorService(self.db, self.jobs, self.profiles, self.gateway)
        self.tracker = TrackerService(self.db, self.jobs)
        self.memory = MemoryService(self.db)
        self.sessions = SessionService(self.db)
        self.tools = get_tool_registry()
        self.permissions = PermissionPolicy(self.tools.tiers())
        self.approvals = ApprovalService(self.db)
        self.context = ContextManager(self.settings.context_token_budget)
        self.runs = RunStore(self.db)

    # ------------------------------------------------------------------ run
    def run(
        self,
        goal: str,
        session_id: str = "",
        mode: str = "auto",
        history: list[dict] | None = None,
    ) -> AgentReply:
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal is required")
        session = self.sessions.get_or_create(session_id or None)
        prior = history if history is not None else self.sessions.history(session["id"], 20)
        session_context = self.sessions.get_context(session["id"])
        self.sessions.add_message(session["id"], "user", goal)

        profile = self.profiles.get()
        memories = self.memory.recall(goal, 8)
        pinned = [memory.content for memory in self.memory.rules()][:6]
        bundle = self.context.build(
            goal=goal,
            profile=profile,
            memories=memories,
            history=prior,
            summary=session.get("summary", ""),
            extra_rules=pinned,
        )

        run = Run(
            session_id=session["id"],
            goal=goal,
            mode="llm" if self.gateway.available and mode != "local" else "local",
        )
        self.runs.create(run.id, goal, session["id"], run.mode)
        tracer = Tracer(run.id, self.db, self.settings.trace_dir)
        tracer.event(
            "run_started",
            goal=goal,
            mode=run.mode,
            session_id=session["id"],
            profile_completeness=profile.completeness(),
        )
        tracer.event("context", sections=bundle.sections, tokens=bundle.total_tokens)

        steps: list[RunStep] = []
        artifacts: dict[str, Any] = {}
        approvals: list[Approval] = []
        tokens = 0
        cost = 0.0
        status = "completed"
        error = ""
        final_message = ""

        try:
            if run.mode == "llm":
                try:
                    final_message, tokens, cost = self._run_llm(
                        bundle.messages, run.id, tracer, steps, artifacts, approvals
                    )
                except (LLMUnavailable, BudgetExceeded) as exc:
                    tracer.event("llm_fallback", reason=str(exc))
                    final_message, steps, artifacts, approvals = self._run_local(
                        goal, profile, run.id, tracer, steps, artifacts, approvals,
                        context=session_context, session_id=session["id"],
                    )
                    run.mode = "local-fallback"
                except Exception as exc:
                    tracer.event("llm_error", error=str(exc))
                    final_message, steps, artifacts, approvals = self._run_local(
                        goal, profile, run.id, tracer, steps, artifacts, approvals,
                        context=session_context, session_id=session["id"],
                    )
                    run.mode = "local-fallback"
            else:
                final_message, steps, artifacts, approvals = self._run_local(
                    goal, profile, run.id, tracer, steps, artifacts, approvals,
                    context=session_context, session_id=session["id"],
                )
            status = "awaiting_approval" if any(a.status == "pending" for a in approvals) else "completed"
        except Exception as exc:  # pragma: no cover - safety net
            status = "failed"
            error = str(exc)
            final_message = (
                "I hit an unexpected error and stopped safely. "
                f"Nothing external was executed. Details: {exc}"
            )
            tracer.event("error", error=str(exc))

        if artifacts.get("search_query"):
            self.sessions.set_context(session["id"], artifacts["search_query"])

        reply = AgentReply(
            run_id=run.id,
            session_id=session["id"],
            status=status,
            message=final_message or HELP_TEXT,
            steps=steps,
            approvals=approvals,
            artifacts=artifacts,
            mode=run.mode,
            tokens=tokens,
            cost=round(cost, 6),
        )
        self.sessions.add_message(session["id"], "assistant", reply.message, run.id)
        summary = summarize_messages(
            prior + [{"role": "assistant", "content": reply.message}],
            session.get("summary", ""),
        )
        self.sessions.update_summary(session["id"], summary)
        self.runs.finish(
            run.id,
            status,
            summary=reply.message,
            steps=len(steps),
            tokens=tokens,
            cost=cost,
            error=error,
        )
        tracer.finish(
            status,
            reply.message,
            steps=len(steps),
            tokens=tokens,
            cost=round(cost, 6),
            approvals=[approval.id for approval in approvals],
        )
        return reply

    # ------------------------------------------------------------- LLM loop
    def _run_llm(
        self,
        messages: list[dict[str, Any]],
        run_id: str,
        tracer: Tracer,
        steps: list[RunStep],
        artifacts: dict,
        approvals: list[Approval],
    ) -> tuple[str, int, float]:
        conversation = list(messages)
        schemas = self.tools.openai_schemas()
        tokens = 0
        cost = 0.0
        failures = 0
        seen_calls: set[str] = set()
        final_message = ""

        for index in range(self.settings.max_steps):
            response = self.gateway.complete(
                conversation,
                model=self.settings.jobs_fast_model,
                kind="agent",
                max_tokens=1200,
                temperature=0.2,
                tools=schemas,
            )
            tokens += response.tokens
            cost += response.cost
            tracer.llm(
                response.model,
                response.tokens,
                response.cost,
                response.latency_ms,
                cached=response.cached,
                step=index,
            )
            tool_calls = response.raw.get("tool_calls") or []
            if not tool_calls:
                final_message = (response.text or "").strip() or "Done."
                break

            assistant_message = {
                "role": "assistant",
                "content": response.text or None,
                "tool_calls": [
                    {
                        "id": call["id"] or f"call_{index}_{position}",
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"]
                            if isinstance(call["arguments"], str)
                            else json.dumps(call["arguments"]),
                        },
                    }
                    for position, call in enumerate(tool_calls)
                ],
            }
            conversation.append(assistant_message)

            for call in assistant_message["tool_calls"]:
                name = call["function"]["name"]
                raw_args = call["function"]["arguments"]
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except (json.JSONDecodeError, TypeError, ValueError):
                    args = {}

                key = f"{name}:{json.dumps(args, sort_keys=True)}"
                if key in seen_calls:
                    observation = {"error": "duplicate call skipped; try a different approach"}
                    conversation.append(
                        {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(observation)}
                    )
                    continue
                seen_calls.add(key)

                outcome = self._execute(name, args, run_id, tracer, steps, artifacts, approvals)
                if outcome.approval:
                    approvals.append(outcome.approval)
                if not outcome.ok:
                    failures += 1
                else:
                    failures = 0
                payload = outcome.result if outcome.ok else {"error": outcome.error}
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(payload, ensure_ascii=False, default=str)[:8000],
                    }
                )
                if failures > self.settings.max_consecutive_failures:
                    final_message = (
                        "I stopped after several tool failures. Check the source status "
                        "and try again; no external action was taken."
                    )
                    return final_message, tokens, cost

        if not final_message:
            final_message = (
                "I reached the step limit before finishing. The trace shows exactly "
                "what ran; you can continue from here."
            )
        return final_message, tokens, cost

    # ----------------------------------------------------------- local loop
    def _run_local(
        self,
        goal: str,
        profile: Profile,
        run_id: str,
        tracer: Tracer,
        steps: list[RunStep],
        artifacts: dict,
        approvals: list[Approval],
        *,
        context: dict | None = None,
        session_id: str = "",
    ) -> tuple[str, list[RunStep], dict, list[Approval]]:
        context = dict(context or {})
        follow_up = _is_followup(goal, context)
        flags = self._intents(goal)
        if follow_up or _looks_like_role_query(goal):
            flags["search"] = True
        parts: list[str] = []
        top: dict | None = None

        # Memory-first intents return early.
        if flags["remember"]:
            content = re.sub(
                r"^(please\s+)?(remember|note)( that)?[:\s]+", "", goal, flags=re.I
            ).strip()
            outcome = self._execute(
                "remember", {"content": content}, run_id, tracer, steps, artifacts, approvals
            )
            if outcome.approval:
                approvals.append(outcome.approval)
            parts.append(_format_result("remember", outcome))
            return "\n\n".join(parts), steps, artifacts, approvals

        if flags["forget"]:
            memory_id = self._extract_memory_id(goal)
            memories = self.memory.list(limit=50)
            if not memory_id and memories:
                memory_id = memories[0].id
            if not memory_id:
                return "I could not find that memory. Nothing was deleted.", steps, artifacts, approvals
            outcome = self._execute(
                "forget", {"memory_id": memory_id}, run_id, tracer, steps, artifacts, approvals
            )
            if outcome.approval:
                approvals.append(outcome.approval)
            parts.append(_format_result("forget", outcome))
            return "\n\n".join(parts), steps, artifacts, approvals

        if flags["memories"]:
            outcome = self._execute("list_memories", {}, run_id, tracer, steps, artifacts, approvals)
            parts.append(_format_result("list_memories", outcome))
            return "\n\n".join(parts), steps, artifacts, approvals

        if flags["sources"]:
            outcome = self._execute("check_sources", {}, run_id, tracer, steps, artifacts, approvals)
            parts.append(_format_result("check_sources", outcome))
            return "\n\n".join(parts), steps, artifacts, approvals

        # Tracker-only questions.
        if flags["tracker"] and not (flags["search"] or flags["tailor"] or flags["cover"] or flags["apply"]):
            apps = self._execute("list_applications", {}, run_id, tracer, steps, artifacts, approvals)
            followups = self._execute("list_followups", {}, run_id, tracer, steps, artifacts, approvals)
            parts.append(_format_result("list_applications", apps))
            parts.append(_format_result("list_followups", followups))
            return "\n\n".join(parts), steps, artifacts, approvals

        # Search.
        if flags["search"]:
            args = _search_args(goal, context, profile)
            outcome = self._execute("search_jobs", args, run_id, tracer, steps, artifacts, approvals)
            jobs = (outcome.result or {}).get("jobs") or []
            top = jobs[0] if jobs else None
            parts.append(_format_result("search_jobs", outcome))

        # Dependent actions need a target job.
        needs_job = flags["tailor"] or flags["cover"] or flags["apply"]
        if needs_job and not top:
            picked = self._pick_job(goal)
            if not picked:
                outcome = self._execute(
                    "search_jobs",
                    _search_args(goal, context, profile, limit=8),
                    run_id,
                    tracer,
                    steps,
                    artifacts,
                    approvals,
                )
                jobs = (outcome.result or {}).get("jobs") or []
                top = jobs[0] if jobs else None
                parts.append(_format_result("search_jobs", outcome))
            else:
                top = {
                    "id": picked.id,
                    "title": picked.title,
                    "company": picked.company,
                    "score": picked.score,
                }

        if needs_job and not top:
            parts.append(
                "I could not find a job to work on. Search first, or import a posting by URL."
            )
            return "\n\n".join(parts), steps, artifacts, approvals

        if top and flags["tailor"]:
            outcome = self._execute(
                "tailor_cv", {"job_id": top["id"]}, run_id, tracer, steps, artifacts, approvals
            )
            parts.append(_format_result("tailor_cv", outcome))

        if top and flags["cover"]:
            outcome = self._execute(
                "draft_cover_letter",
                {"job_id": top["id"]},
                run_id,
                tracer,
                steps,
                artifacts,
                approvals,
            )
            parts.append(_format_result("draft_cover_letter", outcome))

        if top and flags["apply"]:
            if flags["submit"]:
                outcome = self._execute(
                    "request_submission",
                    {"job_id": top["id"], "notes": "Requested by user via agent console"},
                    run_id,
                    tracer,
                    steps,
                    artifacts,
                    approvals,
                )
                if outcome.approval:
                    approvals.append(outcome.approval)
                parts.append(_format_result("request_submission", outcome))
            else:
                outcome = self._execute(
                    "add_application",
                    {"job_id": top["id"], "status": "applied", "notes": "Added via agent console"},
                    run_id,
                    tracer,
                    steps,
                    artifacts,
                    approvals,
                )
                parts.append(_format_result("add_application", outcome))

        if not steps:
            return HELP_TEXT, steps, artifacts, approvals
        return "\n\n".join(part for part in parts if part), steps, artifacts, approvals

    # -------------------------------------------------------------- execute
    def _execute(
        self,
        name: str,
        args: dict,
        run_id: str,
        tracer: Tracer,
        steps: list[RunStep],
        artifacts: dict,
        approvals: list[Approval],
    ) -> ToolOutcome:
        started = time.perf_counter()
        try:
            spec = self.tools.get(name)
        except ToolError as exc:
            steps.append(
                RunStep(index=len(steps) + 1, tool=name, args=args, ok=False, observation=str(exc))
            )
            return ToolOutcome(False, error=str(exc))

        decision = self.permissions.decide(name, args)
        tracer.tool_call(name, args, spec.tier)
        if decision.requires_approval:
            approval = self.approvals.create(
                tool=name,
                args=args,
                reason=decision.reason,
                run_id=run_id,
                tier=decision.tier,
            )
            tracer.approval(approval.id, name, "pending")
            steps.append(
                RunStep(
                    index=len(steps) + 1,
                    thought=f"Paused: {decision.reason}",
                    tool=name,
                    args=args,
                    observation=f"Approval required ({decision.tier}).",
                    ok=True,
                )
            )
            return ToolOutcome(
                True,
                {
                    "status": "pending_approval",
                    "approval_id": approval.id,
                    "reason": decision.reason,
                },
                approval=approval,
                latency_ms=0.0,
            )

        try:
            result = self.tools.call(name, args, ToolContext(run_id=run_id, source="agent"))
            latency = round((time.perf_counter() - started) * 1000, 1)
            tracer.tool_result(name, True, latency, result)
            observation = _compact(result)
            steps.append(
                RunStep(
                    index=len(steps) + 1,
                    thought=self._thought_for(name),
                    tool=name,
                    args=args,
                    observation=observation,
                    ok=True,
                    latency_ms=latency,
                )
            )
            self._capture(name, result, artifacts)
            approval = None
            if result.get("status") == "pending_approval" and result.get("approval_id"):
                approval = self.approvals.get(result["approval_id"])
            return ToolOutcome(True, result, approval=approval, latency_ms=latency)
        except Exception as exc:
            latency = round((time.perf_counter() - started) * 1000, 1)
            tracer.tool_result(name, False, latency, error=str(exc))
            steps.append(
                RunStep(
                    index=len(steps) + 1,
                    thought=f"{name} failed",
                    tool=name,
                    args=args,
                    observation=str(exc)[:400],
                    ok=False,
                    latency_ms=latency,
                )
            )
            return ToolOutcome(False, error=str(exc), latency_ms=latency)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _capture(name: str, result: dict, artifacts: dict) -> None:
        if not isinstance(result, dict):
            return
        if name == "search_jobs":
            jobs = result.get("jobs") or []
            artifacts["jobs"] = jobs[:6]
            artifacts["job_ids"] = [job.get("id") for job in jobs[:6]]
            artifacts["sources"] = result.get("sources", [])
            artifacts["search_query"] = dict(result.get("query") or {})
        elif name == "tailor_cv":
            artifacts["cv"] = result.get("cv")
            artifacts["job_id"] = (result.get("cv") or {}).get("job_id")
        elif name == "draft_cover_letter":
            artifacts["cover_letter"] = result.get("cover_letter")
            artifacts["job_id"] = (result.get("cover_letter") or {}).get("job_id")
        elif name in ("add_application", "request_submission", "update_status"):
            artifacts["application"] = result.get("application")
        elif name == "remember":
            artifacts["memory"] = result.get("memory")
        elif name == "rank_jobs":
            artifacts["ranking"] = result.get("jobs")
        elif name == "skill_gap":
            artifacts["skill_gap"] = result.get("gap")
        elif name == "check_sources":
            artifacts["sources"] = result.get("sources")
        elif name == "list_applications":
            artifacts["applications"] = result.get("applications")
        elif name == "list_followups":
            artifacts["followups"] = result.get("followups")
        elif name in ("recall", "list_memories"):
            artifacts["memories"] = result.get("memories")

    @staticmethod
    def _intents(goal: str) -> dict[str, bool]:
        text = goal.lower()
        return {
            "search": any(
                word in text
                for word in (
                    "find", "search", "look for", "jobs", "roles", "openings",
                    "positions", "hiring", "matches",
                )
            ),
            "tailor": any(
                word in text
                for word in (
                    "tailor", "customise my cv", "customize my cv", "cv for",
                    "resume for", "adjust my cv", "rewrite my cv", "update my cv",
                )
            ),
            "cover": "cover letter" in text,
            "apply": bool(
                re.search(
                    r"\b(apply (to|for)|mark (?:(?:it|this|as) )?applied"
                    r"|submit\b[^.]{0,40}\bapplication|send\b[^.]{0,40}\bapplication)",
                    text,
                )
            ),
            "submit": bool(
                re.search(r"\b(submit|send)\b[^.]{0,40}\bapplication", text)
            ),
            "remember": bool(
                re.match(r"^(please\s+)?(remember|note)( that)?\b", text)
                or "remember that" in text
                or "keep in mind" in text
            ),
            "forget": "forget" in text,
            "memories": any(
                phrase in text
                for phrase in ("what do you remember", "list memories", "my memories", "show memories")
            ),
            "tracker": any(
                phrase in text
                for phrase in (
                    "my applications", "application status", "tracker", "follow up",
                    "follow-up", "followups",
                )
            ),
            "sources": bool(
                re.search(
                    r"(?:check|status|health|doctor)[^.]{0,24}sources?"
                    r"|sources?[^.]{0,24}(?:check|status|health|doctor)"
                    r"|check sources|source status|source health|doctor",
                    text,
                )
            ),
        }

    def _pick_job(self, goal: str) -> Job | None:
        match = re.search(r"job_[0-9a-f]{12}", goal, re.I)
        if match:
            return self.jobs.get(match.group(0))
        jobs = self.jobs.list(limit=200, sort="score")
        if not jobs:
            return None
        text = goal.lower()
        keywords = [
            token for token in re.findall(r"[a-z0-9]{4,}", text) if token not in STOPWORDS
        ]
        best: tuple[int, Job] | None = None
        for job in jobs:
            haystack = f"{job.title} {job.company}".lower()
            score = sum(1 for keyword in keywords if keyword in haystack)
            if score and (best is None or score > best[0]):
                best = (score, job)
        return best[1] if best else jobs[0]

    @staticmethod
    def _extract_role(goal: str, profile: Profile) -> str:
        intent = taxonomy.parse_intent(goal)
        if intent.query:
            return intent.query[:80]
        for target in profile.target_roles:
            if target and target.lower() in goal.lower():
                return target[:80]
        if intent.has_category:
            return taxonomy.category_def(intent.category).search_terms[0][:80]
        return (profile.target_roles[0] if profile.target_roles else "")[:80]

    @staticmethod
    def _extract_location(goal: str, profile: Profile) -> str:
        return taxonomy.parse_intent(goal).location_text[:80]

    @staticmethod
    def _extract_limit(goal: str) -> int:
        match = re.search(r"\b(\d{1,2})\s+(?:jobs?|roles?|matches?|results?)", goal, re.I)
        if match:
            return max(1, min(30, int(match.group(1))))
        return 12

    def _extract_memory_id(self, goal: str) -> str:
        match = re.search(r"mem_[0-9a-f]{12}", goal)
        return match.group(0) if match else ""

    @staticmethod
    def _thought_for(name: str) -> str:
        return {
            "search_jobs": "Searching live sources and deduplicating results.",
            "get_job": "Reading the full posting (untrusted data).",
            "score_job": "Scoring against profile evidence.",
            "rank_jobs": "Ranking stored jobs.",
            "tailor_cv": "Selecting verified evidence and checking every claim.",
            "draft_cover_letter": "Drafting and verifying the letter.",
            "add_application": "Recording the application locally.",
            "remember": "Applying the memory write policy.",
            "check_sources": "Probing every source adapter.",
        }.get(name, f"Calling {name}.")


_FOLLOWUP_FILLER = re.compile(
    r"\b(only|just|show|me|please|instead|now|also|switch|change|to|the|a|an|"
    r"jobs?|roles?|positions?|openings?|list|find|search|for|in|at|with|"
    r"remote|hybrid|on[- ]?site|onsite|office|junior|senior|mid|mid[- ]?level|"
    r"entry|entry[- ]?level|intern|internship|lead|principal|staff|any|all|"
    r"and|or|of|those|these|ones)\b",
    re.I,
)

_ROLE_WORDS = re.compile(
    r"\b(engineer|developer|programmer|manager|analyst|designer|scientist|"
    r"nurse|accountant|consultant|specialist|officer|architect|administrator|"
    r"marketer|recruiter|teacher|technician|director|associate|coordinator|"
    r"auditor|advisor|controller|writer|editor|researcher|strategist|planner|"
    r"supervisor|executive|assistant|clerk|paralegal|lawyer|pharmacist|doctor|"
    r"physician|therapist|chef|driver|representative|agent|broker|underwriter|"
    r"buyer|merchandiser|dispatcher|electrician|mechanic|surveyor|tester)\b",
    re.I,
)


def _role_text(goal: str) -> str:
    """Return the role words in a message, ignoring follow-up filler."""
    intent = taxonomy.parse_intent(goal)
    text = _FOLLOWUP_FILLER.sub(" ", intent.query or "")
    text = " ".join(text.split())
    return text if len(text) > 1 else ""


def _detect_workplace_request(goal: str) -> str:
    text = goal.lower()
    if re.search(r"\b(on[- ]?site|on[- ]?premises|in[- ]?office|office[- ]based)\b", text):
        return "onsite"
    if re.search(r"\bhybrid\b", text):
        return "hybrid"
    if re.search(r"\b(remote|work from home|wfh|from home)\b", text):
        return "remote"
    return ""


def _is_followup(goal: str, context: dict) -> bool:
    """True when the message only changes a constraint on the last search."""
    if not context.get("query"):
        return False
    if _role_text(goal):
        return False
    return bool(
        _detect_workplace_request(goal)
        or taxonomy.detect_seniority(goal)
        or taxonomy.detect_employment_type(goal)
        or taxonomy.parse_intent(goal).location_text
        or re.search(r"\b(instead|only|same|other|nearby)\b", goal, re.I)
    )


def _looks_like_role_query(goal: str) -> bool:
    """A bare role such as “AI Engineer” is a real search, not a greeting."""
    text = (goal or "").strip()
    if not text or len(text.split()) > 14:
        return False
    if _is_followup(text, {}):
        return False
    intent = taxonomy.parse_intent(text)
    if intent.has_category:
        return True
    return bool(_ROLE_WORDS.search(intent.query or text))


def _search_args(
    goal: str,
    context: dict,
    profile: Profile,
    *,
    limit: int = 0,
) -> dict:
    """Merge a new message with the session's last resolved search.

    Only constraints explicitly present in the new message change; everything
    else is preserved so “Only remote” or “In London instead” keeps the role.
    """
    intent = taxonomy.parse_intent(goal)
    new_role = _role_text(goal)
    follow = _is_followup(goal, context)
    carry = bool(follow or not new_role)

    new_workplace = _detect_workplace_request(goal)
    workplace = new_workplace or (context.get("workplace_type", "") if carry else "")
    location = intent.location_text or (context.get("location", "") if carry else "")
    seniority = intent.seniority or (context.get("seniority", "") if carry else "")
    employment = intent.employment_type or (
        context.get("employment_type", "") if carry else ""
    )
    category = (intent.category if intent.category != "other" else "") or (
        context.get("category", "") if carry else ""
    )
    query = (
        new_role
        or context.get("query", "")
        or (profile.target_roles[0] if profile.target_roles else "")
        or profile.headline
    )
    if not limit:
        match = re.search(r"\b(\d{1,2})\s+(?:jobs?|roles?|matches?|results?)\b", goal, re.I)
        limit = max(1, min(30, int(match.group(1)))) if match else 12
    args: dict[str, Any] = {
        "query": query,
        "location": location,
        "remote_only": workplace == "remote",
        "limit": limit,
        "category": category,
        "seniority": seniority,
        "employment_type": employment,
    }
    if workplace in ("remote", "hybrid", "onsite"):
        args["workplace_type"] = workplace
    return args


def _compact(result: Any) -> str:
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) > 600:
        text = text[:596] + " …"
    return text


def _format_result(name: str, outcome: ToolOutcome) -> str:
    if not outcome.ok:
        return f"⚠️ {name} failed: {outcome.error}"
    result = outcome.result or {}
    if result.get("status") == "pending_approval":
        return (
            "⏸ I paused for your approval before doing anything external. "
            "Open the Approvals panel to review the exact tool and arguments."
        )
    if name == "search_jobs":
        jobs = result.get("jobs") or []
        meta = result.get("meta") or {}
        counts = meta.get("counts") or {}
        total = meta.get("total", len(jobs))
        if not total:
            notes = meta.get("notes") or []
            message = result.get("message") or "No matching roles came back."
            return "\n".join([message, *[f"• {note}" for note in notes[:3]]])
        interpreted = meta.get("interpreted") or {}
        lines = [
            f"Found **{total} roles** for "
            f"{interpreted.get('category_label') or 'your search'}"
            f"{' in ' + interpreted['location'] if interpreted.get('location') else ''}."
        ]
        if counts:
            lines.append(
                f"Exact: {counts.get('exact', 0)} · Related: {counts.get('related', 0)} · "
                f"Unverified: {counts.get('unverified', 0)}"
            )
        for index, job in enumerate(jobs[:3], start=1):
            score = job.get("score")
            score_text = f"{score:.1f}" if isinstance(score, (int, float)) else "—"
            tier = job.get("match_tier") or "exact"
            lines.append(
                f"{index}. [{tier}] **{job.get('title')}** @ {job.get('company')} — "
                f"{job.get('location') or 'location n/a'} · profile match {score_text}"
            )
        sources = result.get("sources") or []
        if sources:
            lines.append(f"Sources that responded: {', '.join(sources)}.")
        failed = [run for run in meta.get("source_runs") or [] if run.get("status") == "error"]
        if failed:
            lines.append(f"Unavailable: {', '.join(run['name'] for run in failed)}.")
        notes = meta.get("notes") or []
        if notes:
            lines.append(f"_{notes[-1]}_")
        return "\n".join(lines)
    if name == "tailor_cv":
        cv = result.get("cv") or {}
        job = cv.get("job") or {}
        lines = [
            f"Tailored a CV for **{job.get('title', 'the role')}** @ {job.get('company', '—')} "
            f"using {len(cv.get('bullets') or [])} verified bullets. "
            f"Unsupported claims: {cv.get('unsupported_count', 0)}."
        ]
        changes = cv.get("changes") or []
        if changes:
            lines.append("Changes:")
            for change in changes[:5]:
                label = change.get("label") or change.get("field") or "Update"
                lines.append(f"• {label}: {change.get('detail', '')}".rstrip())
        downloads = result.get("downloads") or []
        if downloads:
            links = " · ".join(
                f"[{item.get('format', '').upper()}]({item.get('url')})" for item in downloads
            )
            lines.append(f"Download: {links}")
        else:
            lines.append("No downloadable file was generated.")
        return "\n".join(lines)
    if name == "draft_cover_letter":
        letter = result.get("cover_letter") or {}
        job = letter.get("job") or {}
        return (
            f"Drafted a cover letter for **{job.get('title', 'the role')}** @ "
            f"{job.get('company', '—')} with {len(letter.get('checks') or [])} claim checks."
        )
    if name == "remember":
        return result.get("message") or "Memory saved."
    if name == "forget":
        return result.get("message") or "Memory forgotten."
    if name in ("list_memories", "recall"):
        memories = result.get("memories") or []
        if not memories:
            return "No memories stored yet."
        return "I remember:\n" + "\n".join(f"• {memory.get('content')}" for memory in memories[:8])
    if name == "list_applications":
        apps = result.get("applications") or []
        if not apps:
            return "No applications tracked yet."
        return "Applications:\n" + "\n".join(
            f"• {app.get('job', {}).get('title', 'Role')} — {app.get('status')}" for app in apps[:8]
        )
    if name == "list_followups":
        followups = result.get("followups") or []
        if not followups:
            return "No follow-ups due in the next seven days."
        return "Upcoming follow-ups:\n" + "\n".join(
            f"• {app.get('job', {}).get('title', 'Role')} — due {str(app.get('follow_up_at'))[:10]}"
            for app in followups[:8]
        )
    if name == "check_sources":
        sources = result.get("sources") or []
        live = [source["name"] for source in sources if source.get("last_probe", {}).get("ok")]
        return f"Source health checked. Live: {', '.join(live) or 'none'}."
    if name == "add_application":
        return result.get("message") or "Application tracked."
    if name == "request_submission":
        return result.get("message") or "Submission queue updated."
    return result.get("message") or f"{name} completed."


_loop: AgentLoop | None = None


def get_agent_loop() -> AgentLoop:
    global _loop
    if _loop is None:
        _loop = AgentLoop()
    return _loop
