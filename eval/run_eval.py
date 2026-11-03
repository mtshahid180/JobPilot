"""Offline evaluation harness.

Runs the local (zero-key) pipeline against the labeled dataset:

* ranking quality (Spearman, Precision@k, Recall@k, nDCG@k) versus a TF-IDF
  keyword baseline;
* hallucinated claims in tailored CVs plus a claim-verifier challenge set;
* local planner tool-selection accuracy;
* memory recall versus a lexical no-memory ablation;
* prompt-injection detection;
* context-compaction behaviour, latency and cost.

Usage::

    python eval/run_eval.py                 # human-readable report
    python eval/run_eval.py --json          # machine-readable
    jobpilot eval --k 5 --json

Exit code is 0 when every quality gate passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import metrics  # noqa: E402
from eval.baselines import TfidfKeywordScorer, keyword_memory_ranking  # noqa: E402

from jobpilot.core.config import get_settings  # noqa: E402
from jobpilot.core.db import Database, set_db  # noqa: E402
from jobpilot.core.models import RawJob  # noqa: E402
from jobpilot.harness.context import ContextManager  # noqa: E402
from jobpilot.harness.guardrails import screen_untrusted, verify_claim  # noqa: E402
from jobpilot.harness.loop import AgentLoop  # noqa: E402
from jobpilot.pipelines.extract import extract_job  # noqa: E402
from jobpilot.services.jobs import JobsService  # noqa: E402
from jobpilot.services.match import MatchService  # noqa: E402
from jobpilot.services.memory import MemoryService  # noqa: E402
from eval.fixtures import load_eval_profile  # noqa: E402
from jobpilot.services.profile import ProfileService  # noqa: E402
from jobpilot.services.tailor import TailorService  # noqa: E402

DATASET_DIR = Path(__file__).resolve().parent / "dataset"
DEFAULT_REPORT = Path(__file__).resolve().parent / "eval-report.md"

CHALLENGE_CLAIMS: list[tuple[str, bool]] = [
    ("Built a retrieval-augmented generation service in Python and FastAPI.", True),
    ("Reduced LLM answer hallucinations by 38% with a citation-checking harness.", True),
    ("Designed a PostgreSQL and pgvector retrieval layer over 120,000 documents.", True),
    ("Led a team of 20 engineers across three continents.", False),
    ("Built production Kubernetes clusters for 50 microservices.", False),
    ("Increased revenue by 45% with a recommendation engine.", False),
    ("Five years of experience at Google.", False),
    ("Trained and evaluated scikit-learn models for customer churn.", True),
]


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        records.append(json.loads(line))
    return records


def resolve_dataset(path_arg: str) -> dict[str, list[dict[str, Any]]]:
    if path_arg:
        path = Path(path_arg)
        directory = path if path.is_dir() else path.parent
    else:
        directory = DATASET_DIR
    return {
        "jobs": load_jsonl(directory / "jobs.jsonl"),
        "tasks": load_jsonl(directory / "tasks.jsonl"),
        "injections": load_jsonl(directory / "injections.jsonl"),
        "memory": load_jsonl(directory / "memory.jsonl"),
    }


def reset_singletons() -> None:
    import jobpilot.harness.llm as llm_module
    import jobpilot.harness.loop as loop_module
    import jobpilot.services.jobs as jobs_module
    import jobpilot.services.match as match_module
    import jobpilot.services.sessions as sessions_module
    import jobpilot.services.tools as tools_module
    import jobpilot.services.tracker as tracker_module
    import jobpilot.sources.registry as registry_module

    for module, name in (
        (llm_module, "_gateway"),
        (loop_module, "_loop"),
        (jobs_module, "_jobs_service"),
        (match_module, "_match_service"),
        (sessions_module, "_sessions"),
        (tools_module, "_tools"),
        (tracker_module, "_tracker"),
        (registry_module, "_registry"),
    ):
        setattr(module, name, None)


def build_jobs(records: list[dict[str, Any]], jobs: JobsService) -> list:
    now = datetime.now(UTC)
    stored = []
    for record in records:
        raw = RawJob(
            source="eval-dataset",
            external_id=record["id"],
            title=record["title"],
            company=record["company"],
            location=record.get("location", ""),
            description=record.get("description", ""),
            url=f"https://eval.local/{record['id']}",
            salary=record.get("salary", ""),
            remote=record.get("remote"),
            posted_at=now - timedelta(days=float(record.get("posted_days_ago", 0))),
            tags=record.get("skills", []),
        )
        job = extract_job(raw)
        job.id = record["id"]
        job.seniority = record.get("seniority", job.seniority)
        job.skills = list(record.get("skills") or job.skills)
        stored.append(jobs.upsert(job))
    return stored


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------
def profile_text(profile) -> str:
    parts = [profile.headline, profile.summary, " ".join(profile.skills)]
    parts.extend(profile.all_bullets)
    parts.extend(profile.target_roles)
    return "\n".join(part for part in parts if part)


def job_text(job) -> str:
    return f"{job.title} {job.company} {job.location} {' '.join(job.skills)} {job.description}"


def ranking_metrics(scores: list[float], labels: list[int], k: int) -> dict[str, float]:
    order = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
    ranked_labels = [labels[index] for index in order]
    return {
        "spearman": metrics.spearman(scores, labels),
        "precision_at_k": metrics.precision_at_k(ranked_labels, k),
        "recall_at_k": metrics.recall_at_k(ranked_labels, k),
        "ndcg_at_k": metrics.ndcg_at_k(ranked_labels, k),
        "mrr": metrics.mean_reciprocal_rank([label >= 2 for label in ranked_labels]),
        "top_labels": ranked_labels[:k],
    }


def predict_tools(goal: str) -> tuple[list[str], set[str]]:
    flags = AgentLoop._intents(goal)
    true_flags = {name for name, value in flags.items() if value}

    if flags["remember"]:
        return ["remember"], true_flags
    if flags["forget"]:
        return ["forget"], true_flags
    if flags["memories"]:
        return ["list_memories"], true_flags
    if flags["sources"]:
        return ["check_sources"], true_flags
    if flags["tracker"] and not (flags["search"] or flags["tailor"] or flags["cover"] or flags["apply"]):
        return ["list_applications", "list_followups"], true_flags

    tools: list[str] = []
    if flags["search"]:
        tools.append("search_jobs")
    # The eval database is pre-seeded, so dependent actions can use the top job
    # without triggering an extra search.
    if flags["tailor"]:
        tools.append("tailor_cv")
    if flags["cover"]:
        tools.append("draft_cover_letter")
    if flags["apply"]:
        tools.append("request_submission" if flags["submit"] else "add_application")
    return tools, true_flags


def evaluate(data: dict[str, list[dict[str, Any]]], k: int) -> dict[str, Any]:
    timings: dict[str, float] = {}
    started_all = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="jobpilot-eval-") as tmp:
        import jobpilot.core.db as db_module

        previous_db = db_module._db
        get_settings.cache_clear()
        reset_singletons()
        database = Database(Path(tmp) / "eval.sqlite3")
        database.init()
        set_db(database)
        try:
            profiles = ProfileService(database)
            profile = profiles.save(load_eval_profile())
            jobs = JobsService(database)
            matcher = MatchService(database, jobs, profiles)
            tailor = TailorService(database, jobs, profiles)
            memory = MemoryService(database)

            # --- ranking ---------------------------------------------------
            stage = time.perf_counter()
            stored = build_jobs(data["jobs"], jobs)
            model_scored = [(job, matcher.score_job(job, profile, persist=False)) for job in stored]
            model_scores = [match.score for _, match in model_scored]
            labels = [int(record["label"]) for record in data["jobs"]]

            documents = [job_text(job) for job in stored]
            baseline = TfidfKeywordScorer().fit([profile_text(profile), *documents])
            baseline_scores = [baseline.score(profile_text(profile), document) for document in documents]
            timings["ranking_ms"] = round((time.perf_counter() - stage) * 1000, 1)

            model_ranking = ranking_metrics(model_scores, labels, k)
            baseline_ranking = ranking_metrics(baseline_scores, labels, k)

            # --- grounded tailoring ---------------------------------------
            stage = time.perf_counter()
            order = sorted(range(len(model_scores)), key=lambda i: model_scores[i], reverse=True)
            cv_results = []
            unsupported_total = 0
            for index in order[:3]:
                cv = tailor.tailor_cv(stored[index].id)
                unsupported_total += cv.unsupported_count
                cv_results.append(
                    {
                        "job_id": stored[index].id,
                        "verified": cv.verified,
                        "bullets": len(cv.bullets),
                        "unsupported": cv.unsupported_count,
                    }
                )

            challenge = []
            false_accepts = 0
            false_rejects = 0
            for claim, expected in CHALLENGE_CLAIMS:
                check = verify_claim(claim, profile)
                challenge.append(
                    {
                        "claim": claim,
                        "expected": expected,
                        "supported": check.supported,
                        "reason": check.reason,
                    }
                )
                if check.supported and not expected:
                    false_accepts += 1
                if not check.supported and expected:
                    false_rejects += 1
            timings["tailoring_ms"] = round((time.perf_counter() - stage) * 1000, 1)

            # --- tool selection -------------------------------------------
            stage = time.perf_counter()
            task_results = []
            tool_correct = 0
            tool_jaccard: list[float] = []
            intent_correct = 0
            for task in data["tasks"]:
                predicted, true_flags = predict_tools(task["goal"])
                expected = set(task.get("expected_tools") or [])
                exact = set(predicted) == expected
                union = set(predicted) | expected
                overlap = len(set(predicted) & expected) / len(union) if union else 1.0
                # Early-return intents (remember/forget/sources/...) take
                # precedence, so an extra lower-priority flag is harmless.
                intents_match = set(task.get("expected_intents") or []) <= true_flags
                tool_correct += int(exact)
                intent_correct += int(intents_match)
                tool_jaccard.append(overlap)
                task_results.append(
                    {
                        "id": task["id"],
                        "goal": task["goal"],
                        "expected": sorted(expected),
                        "predicted": sorted(predicted),
                        "exact": exact,
                        "intents_match": intents_match,
                    }
                )
            task_count = max(1, len(data["tasks"]))
            tool_selection = {
                "accuracy": round(tool_correct / task_count, 4),
                "mean_jaccard": round(sum(tool_jaccard) / task_count, 4),
                "intent_recall": round(intent_correct / task_count, 4),
                "tasks": task_results,
            }
            timings["tool_selection_ms"] = round((time.perf_counter() - stage) * 1000, 1)

            # --- memory recall --------------------------------------------
            stage = time.perf_counter()
            stored_memories = []
            for item in data["memory"]:
                result = memory.remember(
                    item["content"],
                    kind=item.get("kind"),
                    provenance="eval",
                    confirmed=True,
                )
                if result.memory is not None:
                    stored_memories.append(result.memory)
            contents = [item.content for item in stored_memories]

            model_hits = 0
            baseline_hits = 0
            reciprocal_ranks: list[float] = []
            memory_results = []
            for item in data["memory"]:
                recalled = memory.recall(item["query"], limit=3)
                expected = item["expected"].lower()
                rank = next(
                    (position for position, entry in enumerate(recalled, start=1) if expected in entry.content.lower()),
                    None,
                )
                model_hit = rank is not None
                model_hits += int(model_hit)
                reciprocal_ranks.append(1 / rank if rank else 0.0)

                keyword_ranking = keyword_memory_ranking(item["query"], contents, k=3)
                baseline_hit = any(
                    expected in contents[index].lower() for index in keyword_ranking
                )
                baseline_hits += int(baseline_hit)
                memory_results.append(
                    {
                        "id": item["id"],
                        "query": item["query"],
                        "expected": item["expected"],
                        "recall_rank": rank,
                        "keyword_hit": baseline_hit,
                    }
                )
            memory_count = max(1, len(data["memory"]))
            memory_metrics = {
                "recall_at_3": round(model_hits / memory_count, 4),
                "mrr": round(sum(reciprocal_ranks) / memory_count, 4),
                "keyword_recall_at_3": round(baseline_hits / memory_count, 4),
                "tasks": memory_results,
            }
            timings["memory_ms"] = round((time.perf_counter() - stage) * 1000, 1)

            # --- injection screening --------------------------------------
            stage = time.perf_counter()
            injection_results = []
            exact = 0
            detected = 0
            attacks = 0
            false_positives = 0
            benign = 0
            for item in data["injections"]:
                scan = screen_untrusted(item["text"])
                expected_risk = item["expected_risk"]
                exact += int(scan.risk == expected_risk)
                is_attack = expected_risk != "none"
                if is_attack:
                    attacks += 1
                    detected += int(scan.risk != "none")
                else:
                    benign += 1
                    false_positives += int(scan.risk != "none")
                injection_results.append(
                    {
                        "id": item["id"],
                        "expected": expected_risk,
                        "actual": scan.risk,
                        "reasons": scan.reasons,
                    }
                )
            injection_count = max(1, len(data["injections"]))
            injection_metrics = {
                "exact_accuracy": round(exact / injection_count, 4),
                "detection_rate": round(detected / attacks, 4) if attacks else 0.0,
                "false_positive_rate": round(false_positives / benign, 4) if benign else 0.0,
                "results": injection_results,
            }
            timings["injection_ms"] = round((time.perf_counter() - stage) * 1000, 1)

            # --- context compaction ---------------------------------------
            stage = time.perf_counter()
            history = [
                {
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": (
                        f"Turn {index}: we discussed role {index}, the stack, the "
                        "timeline, interview preparation and follow-up steps."
                    ),
                }
                for index in range(40)
            ]
            context = ContextManager(token_budget=2000)
            bundle = context.build(
                goal="Plan the next application",
                profile=profile,
                memories=[],
                history=history,
            )
            compacted_summary, compacted_history = context.compact(history, "")
            kept = max(0, len(bundle.messages) - 2)
            compaction = {
                "history_messages": len(history),
                "messages_kept": kept,
                "compaction_ratio": round(kept / len(history), 4),
                "compacted_messages": len(compacted_history),
                "summary_chars": len(compacted_summary),
                "estimated_tokens": bundle.total_tokens,
            }
            timings["context_ms"] = round((time.perf_counter() - stage) * 1000, 1)

            timings["total_ms"] = round((time.perf_counter() - started_all) * 1000, 1)

            result = {
                "dataset": {
                    "jobs": len(data["jobs"]),
                    "tasks": len(data["tasks"]),
                    "injections": len(data["injections"]),
                    "memory": len(data["memory"]),
                    "label_distribution": {
                        str(label): labels.count(label) for label in (3, 2, 1, 0)
                    },
                },
                "ranking": {
                    "jobpilot": model_ranking,
                    "tfidf_baseline": baseline_ranking,
                    "delta_precision_at_k": round(
                        model_ranking["precision_at_k"] - baseline_ranking["precision_at_k"], 4
                    ),
                    "delta_ndcg_at_k": round(
                        model_ranking["ndcg_at_k"] - baseline_ranking["ndcg_at_k"], 4
                    ),
                    "delta_spearman": round(
                        model_ranking["spearman"] - baseline_ranking["spearman"], 4
                    ),
                },
                "tailoring": {
                    "cvs": cv_results,
                    "unsupported_claims": unsupported_total,
                    "verified_cvs": sum(1 for item in cv_results if item["verified"]),
                    "challenge_claims": challenge,
                    "false_accepts": false_accepts,
                    "false_rejects": false_rejects,
                },
                "tool_selection": tool_selection,
                "memory": memory_metrics,
                "injection": injection_metrics,
                "compaction": compaction,
                "harness": {
                    "mode": "local",
                    "llm_cost_usd": 0.0,
                    "cost_per_task_usd": 0.0,
                    "timings_ms": timings,
                    "latency_per_task_ms": round(timings["total_ms"] / task_count, 1),
                },
            }
            result["gates"] = quality_gates(result)
            return result
        finally:
            set_db(previous_db)
            reset_singletons()


def quality_gates(result: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "zero_hallucinated_cv_claims": result["tailoring"]["unsupported_claims"] == 0,
        "verifier_rejects_false_claims": result["tailoring"]["false_accepts"] == 0,
        "injection_detection_complete": result["injection"]["detection_rate"] >= 0.99,
        "no_injection_false_positives": result["injection"]["false_positive_rate"] == 0.0,
        "tool_selection_above_80pct": result["tool_selection"]["accuracy"] >= 0.8,
        "memory_recall_above_75pct": result["memory"]["recall_at_3"] >= 0.75,
        "ranking_ndcg_beats_tfidf": result["ranking"]["delta_ndcg_at_k"] >= 0.0,
        "ranking_precision_not_worse": result["ranking"]["delta_precision_at_k"] >= -0.001,
        "context_compacts_history": result["compaction"]["messages_kept"]
        < result["compaction"]["history_messages"],
    }
    return {"passed": all(checks.values()), "checks": checks}


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def render_markdown(result: dict[str, Any], k: int) -> str:
    ranking = result["ranking"]
    jobpilot = ranking["jobpilot"]
    baseline = ranking["tfidf_baseline"]
    tailoring = result["tailoring"]
    tools = result["tool_selection"]
    memory = result["memory"]
    injection = result["injection"]
    harness = result["harness"]
    gates = result["gates"]

    def check(value: bool) -> str:
        return "PASS" if value else "FAIL"

    lines = [
        "# JobPilot evaluation report",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Mode: **{harness['mode']}** (zero API cost) · ranking cut-off: **@{k}**",
        "",
        "## Dataset",
        "",
        f"- {result['dataset']['jobs']} labeled job postings (labels: "
        + ", ".join(f"{label}={count}" for label, count in result["dataset"]["label_distribution"].items())
        + ")",
        f"- {result['dataset']['tasks']} planner tasks, "
        f"{result['dataset']['memory']} memory-recall tasks, "
        f"{result['dataset']['injections']} injection probes",
        "",
        "## Ranking quality",
        "",
        "| Metric | JobPilot (heuristic + embedding) | TF-IDF baseline |",
        "| --- | ---: | ---: |",
        f"| Spearman correlation | {jobpilot['spearman']:.3f} | {baseline['spearman']:.3f} |",
        f"| Precision@{k} | {jobpilot['precision_at_k']:.3f} | {baseline['precision_at_k']:.3f} |",
        f"| Recall@{k} | {jobpilot['recall_at_k']:.3f} | {baseline['recall_at_k']:.3f} |",
        f"| nDCG@{k} | {jobpilot['ndcg_at_k']:.3f} | {baseline['ndcg_at_k']:.3f} |",
        f"| MRR | {jobpilot['mrr']:.3f} | {baseline['mrr']:.3f} |",
        "",
        f"Top-{k} labels for JobPilot: `{jobpilot['top_labels']}` (2+ is relevant).",
        "",
        "## Grounded generation",
        "",
        f"- Tailored CVs verified: **{tailoring['verified_cvs']}/{len(tailoring['cvs'])}**",
        f"- Unsupported claims in those CVs: **{tailoring['unsupported_claims']}** (target 0)",
        f"- Verifier challenge set: **{len(tailoring['challenge_claims'])}** claims, "
        f"false accepts **{tailoring['false_accepts']}**, false rejects **{tailoring['false_rejects']}**",
        "",
        "## Planner and memory",
        "",
        "| Metric | JobPilot | Ablation |",
        "| --- | ---: | ---: |",
        f"| Tool-selection accuracy | {tools['accuracy']:.3f} | — |",
        f"| Mean tool Jaccard | {tools['mean_jaccard']:.3f} | — |",
        f"| Intent recall | {tools['intent_recall']:.3f} | — |",
        f"| Memory recall@3 | {memory['recall_at_3']:.3f} | {memory['keyword_recall_at_3']:.3f} (keyword) |",
        f"| Memory MRR | {memory['mrr']:.3f} | — |",
        "",
        "## Safety and harness",
        "",
        f"- Injection exact accuracy: **{injection['exact_accuracy']:.3f}**",
        f"- Attack detection rate: **{injection['detection_rate']:.3f}**",
        f"- Benign false-positive rate: **{injection['false_positive_rate']:.3f}**",
        f"- Context compaction kept **{result['compaction']['messages_kept']}/"
        f"{result['compaction']['history_messages']}** history messages at a 2k token budget",
        f"- Latency: **{harness['latency_per_task_ms']:.0f} ms/task** "
        f"(total {harness['timings_ms']['total_ms']:.0f} ms)",
        f"- LLM cost: **${harness['llm_cost_usd']:.2f}** (local pipeline)",
        "",
        "## Quality gates",
        "",
        "| Gate | Result |",
        "| --- | --- |",
    ]
    for name, value in gates["checks"].items():
        lines.append(f"| {name.replace('_', ' ')} | {check(value)} |")
    lines += ["", f"**Overall: {'PASS' if gates['passed'] else 'FAIL'}**", ""]
    return "\n".join(lines)


def print_summary(result: dict[str, Any], k: int) -> None:
    ranking = result["ranking"]
    print("\nJobPilot evaluation")
    print("=" * 60)
    print(
        f"dataset: {result['dataset']['jobs']} jobs · {result['dataset']['tasks']} tasks · "
        f"{result['dataset']['memory']} memories · {result['dataset']['injections']} injections"
    )
    print(
        f"ranking: spearman={ranking['jobpilot']['spearman']:.3f} "
        f"P@{k}={ranking['jobpilot']['precision_at_k']:.3f} "
        f"nDCG@{k}={ranking['jobpilot']['ndcg_at_k']:.3f} "
        f"(TF-IDF P@{k}={ranking['tfidf_baseline']['precision_at_k']:.3f})"
    )
    print(
        f"tailoring: {result['tailoring']['verified_cvs']}/{len(result['tailoring']['cvs'])} "
        f"verified · {result['tailoring']['unsupported_claims']} unsupported claims · "
        f"{result['tailoring']['false_accepts']} false accepts"
    )
    print(
        f"planner: {result['tool_selection']['accuracy']:.0%} exact tool sets · "
        f"{result['tool_selection']['intent_recall']:.0%} intent recall"
    )
    print(
        f"memory: recall@3={result['memory']['recall_at_3']:.0%} "
        f"(keyword ablation {result['memory']['keyword_recall_at_3']:.0%})"
    )
    print(
        f"injection: detection={result['injection']['detection_rate']:.0%} · "
        f"false positives={result['injection']['false_positive_rate']:.0%}"
    )
    print(f"latency: {result['harness']['latency_per_task_ms']:.0f} ms/task · cost $0.00")
    print("-" * 60)
    for name, value in result["gates"]["checks"].items():
        print(f"  [{'PASS' if value else 'FAIL'}] {name.replace('_', ' ')}")
    print("-" * 60)
    print("overall:", "PASS" if result["gates"]["passed"] else "FAIL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.run_eval", description=__doc__)
    parser.add_argument("--dataset", default="", help="dataset directory or jobs JSONL path")
    parser.add_argument("--k", type=int, default=5, help="ranking cut-off (default 5)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="markdown report path")
    parser.add_argument("--no-report", action="store_true", help="do not write a markdown report")
    args = parser.parse_args(argv)

    data = resolve_dataset(args.dataset)
    if not data["jobs"]:
        print(f"no jobs found in dataset: {args.dataset or DATASET_DIR}", file=sys.stderr)
        return 2

    result = evaluate(data, max(1, args.k))
    if not args.no_report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_markdown(result, args.k), encoding="utf-8")

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_summary(result, args.k)
        if not args.no_report:
            print(f"\nreport: {Path(args.report)}")
    return 0 if result["gates"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
