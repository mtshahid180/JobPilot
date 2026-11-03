"""Deterministic, dependency-free text embeddings.

A signed hashing vectoriser over word unigrams/bigrams and character trigrams.
It is not a replacement for a neural embedding model, but it is stable,
offline, fast and good enough for the retrieval benchmarks in this project. When
``EMBEDDING_PROVIDER=openai`` and a key is present, the gateway can be used
instead (see :func:`embed_texts`).
"""

from __future__ import annotations

import hashlib
import math
import re
import struct

DIMENSIONS = 384
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "to",
    "was", "were", "will", "with", "you", "your", "we", "our", "this", "they",
    "their", "them", "not", "but", "can", "all", "any", "who", "what", "when",
    "where", "how", "job", "role", "work", "team", "company",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % DIMENSIONS
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return index, sign


def embed_text(text: str) -> list[float]:
    """Embed text into a unit-length sparse-ish dense vector."""
    vector = [0.0] * DIMENSIONS
    tokens = _tokens(text or "")
    if not tokens:
        return vector

    features: list[tuple[str, float]] = []
    for token in tokens:
        features.append((f"w:{token}", 1.0))
    for left, right in zip(tokens, tokens[1:]):
        features.append((f"b:{left}_{right}", 0.65))
    joined = " ".join(tokens)
    for i in range(max(0, len(joined) - 2)):
        features.append((f"c:{joined[i:i + 3]}", 0.25))

    for feature, weight in features:
        index, sign = _bucket(feature)
        vector[index] += sign * weight

    norm = math.sqrt(sum(v * v for v in vector))
    if norm:
        vector = [v / norm for v in vector]
    return vector


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))


def similarity(a: str, b: str) -> float:
    """Cosine similarity rescaled to 0..1 for display."""
    return round((cosine(embed_text(a), embed_text(b)) + 1) / 2, 4)


def to_blob(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def from_blob(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob[: count * 4]))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch.

    Local mode is the default. OpenAI embeddings are opt-in and lazily imported
    so the project keeps working with zero keys.
    """
    from jobpilot.core.config import get_settings

    settings = get_settings()
    if settings.embedding_provider == "openai" and settings.llm_enabled:
        try:  # pragma: no cover - network path
            import httpx

            response = httpx.post(
                f"{settings.openai_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={"model": settings.embedding_model, "input": texts},
                timeout=settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            return [item["embedding"] for item in response.json()["data"]]
        except Exception:
            pass
    return [embed_text(text) for text in texts]
