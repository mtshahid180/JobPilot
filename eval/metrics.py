"""Ranking, correlation and classification metrics used by the eval suite."""

from __future__ import annotations

import math
from collections.abc import Sequence


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def average_ranks(values: Sequence[float]) -> list[float]:
    """Ranks with ties receiving their average rank (1-based)."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        rank = (position + end) / 2 + 1
        for index in range(position, end + 1):
            ranks[order[index]] = rank
        position = end + 1
    return ranks


def pearson(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or len(a) < 2:
        return 0.0
    mean_a, mean_b = _mean(a), _mean(b)
    numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    denom_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    denom_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if denom_a == 0 or denom_b == 0:
        return 0.0
    return round(numerator / (denom_a * denom_b), 4)


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    """Rank correlation, robust to score scale."""
    return pearson(average_ranks(a), average_ranks(b))


def precision_at_k(ranked_labels: Sequence[int], k: int, threshold: int = 2) -> float:
    top = list(ranked_labels)[:k]
    if not top:
        return 0.0
    return round(sum(1 for label in top if label >= threshold) / len(top), 4)


def recall_at_k(ranked_labels: Sequence[int], k: int, threshold: int = 2) -> float:
    labels = list(ranked_labels)
    relevant = sum(1 for label in labels if label >= threshold)
    if relevant == 0:
        return 0.0
    found = sum(1 for label in labels[:k] if label >= threshold)
    return round(found / relevant, 4)


def ndcg_at_k(ranked_labels: Sequence[int], k: int) -> float:
    labels = list(ranked_labels)[:k]
    if not labels:
        return 0.0

    def dcg(values: Sequence[int]) -> float:
        return sum(
            (2**label - 1) / math.log2(position + 2)
            for position, label in enumerate(values)
        )

    ideal = sorted((int(label) for label in ranked_labels), reverse=True)[:k]
    best = dcg(ideal)
    return round(dcg(labels) / best, 4) if best else 0.0


def mean_reciprocal_rank(relevant: Sequence[bool]) -> float:
    for index, hit in enumerate(relevant, start=1):
        if hit:
            return round(1 / index, 4)
    return 0.0


def accuracy(correct: Sequence[bool]) -> float:
    return round(sum(1 for value in correct if value) / len(correct), 4) if correct else 0.0


def f1(true_positive: int, false_positive: int, false_negative: int) -> float:
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)
