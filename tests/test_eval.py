"""Evaluation metrics, baselines and planner helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import metrics
from eval.baselines import TfidfKeywordScorer, keyword_memory_ranking
from eval.run_eval import CHALLENGE_CLAIMS, predict_tools, quality_gates


def test_spearman_is_perfect_for_monotonic_ranks():
    assert metrics.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert metrics.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert metrics.spearman([1, 2, 3], [0, 0, 0]) == 0.0


def test_rank_ties_receive_average_rank():
    assert metrics.average_ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]


def test_precision_recall_and_ndcg():
    ranked = [3, 2, 0, 1, 3]
    assert metrics.precision_at_k(ranked, 2) == 1.0
    assert metrics.precision_at_k(ranked, 5) == 0.6
    assert metrics.recall_at_k(ranked, 2) == pytest.approx(2 / 3, abs=1e-3)
    assert metrics.recall_at_k(ranked, 5) == 1.0
    assert 0 < metrics.ndcg_at_k(ranked, 5) < 1
    assert metrics.ndcg_at_k([3, 2, 1, 0], 4) == 1.0


def test_mrr_and_accuracy():
    assert metrics.mean_reciprocal_rank([False, True]) == 0.5
    assert metrics.mean_reciprocal_rank([False, False]) == 0.0
    assert metrics.accuracy([True, True, False]) == 0.6667


def test_tfidf_baseline_ranks_relevant_text_higher():
    scorer = TfidfKeywordScorer().fit(
        [
            "python machine learning engineer llm rag fastapi",
            "graphic designer photoshop illustration branding",
            "python backend engineer fastapi postgresql",
        ]
    )
    relevant = scorer.score("python llm rag engineer", "python machine learning engineer llm rag fastapi")
    unrelated = scorer.score("python llm rag engineer", "graphic designer photoshop illustration branding")
    assert relevant > 0
    assert unrelated == 0


def test_keyword_memory_ranking_matches_shared_tokens():
    contents = [
        "I prefer remote-first roles with async teams.",
        "I never apply to gambling companies.",
        "My salary floor is PKR 250,000.",
    ]
    ranking = keyword_memory_ranking("remote-first async teams", contents, k=2)
    assert ranking[0] == 0


def test_predict_tools_covers_planner_paths():
    assert predict_tools("Find AI Engineer jobs")[0] == ["search_jobs"]
    assert predict_tools("Remember that I only want remote roles")[0] == ["remember"]
    assert predict_tools("Check my job sources")[0] == ["check_sources"]
    assert predict_tools("Submit my application for the top match")[0] == ["request_submission"]
    assert predict_tools("Mark as applied the top match")[0] == ["add_application"]
    assert predict_tools("Tailor my CV and draft a cover letter for the top match")[0] == [
        "tailor_cv",
        "draft_cover_letter",
    ]
    assert predict_tools("Show my applications and follow-ups")[0] == [
        "list_applications",
        "list_followups",
    ]


def test_planner_ignores_unrelated_text():
    tools, flags = predict_tools("Tell me a joke about databases")
    assert tools == []
    assert flags == set()


def test_challenge_set_has_supported_and_unsupported_claims():
    assert any(expected for _, expected in CHALLENGE_CLAIMS)
    assert any(not expected for _, expected in CHALLENGE_CLAIMS)


def test_quality_gates_reject_regressions():
    passing = {
        "tailoring": {"unsupported_claims": 0, "false_accepts": 0},
        "injection": {"detection_rate": 1.0, "false_positive_rate": 0.0},
        "tool_selection": {"accuracy": 1.0},
        "memory": {"recall_at_3": 1.0},
        "ranking": {"delta_ndcg_at_k": 0.01, "delta_precision_at_k": 0.0},
        "compaction": {"messages_kept": 20, "history_messages": 40},
    }
    assert quality_gates(passing)["passed"] is True

    failing = {**passing, "tailoring": {"unsupported_claims": 2, "false_accepts": 1}}
    result = quality_gates(failing)
    assert result["passed"] is False
    assert result["checks"]["zero_hallucinated_cv_claims"] is False
    assert result["checks"]["verifier_rejects_false_claims"] is False
