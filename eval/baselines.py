"""Baselines for the evaluation suite.

These are deliberately simple, dependency-free models:

* :class:`TfidfKeywordScorer` — classic TF-IDF cosine retrieval used as the
  keyword-matching baseline for job ranking.
* :func:`keyword_memory_ranking` — the no-memory ablation for preference
  recall: lexical overlap instead of embedding similarity.
"""

from __future__ import annotations

import math
import re
from collections import Counter

TOKEN_RE = re.compile(r"[a-z][a-z0-9+#.\-]{1,}")
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "to",
    "was", "were", "will", "with", "you", "your", "we", "our", "this", "they",
    "their", "them", "not", "but", "can", "all", "any", "who", "what", "when",
    "where", "how", "job", "jobs", "role", "roles", "work", "team", "company",
}


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall((text or "").lower()) if token not in STOPWORDS]


class TfidfKeywordScorer:
    """TF-IDF cosine similarity over unigrams."""

    def __init__(self) -> None:
        self.idf: dict[str, float] = {}
        self._documents = 0

    def fit(self, documents: list[str]) -> "TfidfKeywordScorer":
        self._documents = len(documents)
        document_frequency: Counter[str] = Counter()
        for document in documents:
            document_frequency.update(set(tokenize(document)))
        self.idf = {
            token: math.log((1 + self._documents) / (1 + frequency)) + 1
            for token, frequency in document_frequency.items()
        }
        return self

    def _vector(self, text: str) -> dict[str, float]:
        counts = Counter(tokenize(text))
        vector = {
            token: count * self.idf.get(token, math.log(1 + self._documents) + 1)
            for token, count in counts.items()
        }
        norm = math.sqrt(sum(value * value for value in vector.values()))
        return {token: value / norm for token, value in vector.items()} if norm else {}

    def score(self, query: str, document: str) -> float:
        left, right = self._vector(query), self._vector(document)
        if not left or not right:
            return 0.0
        return round(sum(value * right.get(token, 0.0) for token, value in left.items()), 6)


def keyword_memory_ranking(query: str, contents: list[str], k: int = 3) -> list[int]:
    """Rank memories by Jaccard overlap — the no-embedding ablation."""
    query_tokens = set(tokenize(query))
    scored: list[tuple[float, int]] = []
    for index, content in enumerate(contents):
        tokens = set(tokenize(content))
        union = query_tokens | tokens
        score = len(query_tokens & tokens) / len(union) if union else 0.0
        scored.append((score, index))
    scored.sort(reverse=True)
    return [index for score, index in scored[:k] if score > 0]
