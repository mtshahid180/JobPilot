"""Deterministic local embeddings."""

from __future__ import annotations

import math

import pytest

from jobpilot.pipelines.embedding import (
    DIMENSIONS,
    cosine,
    embed_text,
    embed_texts,
    from_blob,
    similarity,
    to_blob,
)


def test_embedding_shape_and_determinism():
    first = embed_text("Python FastAPI retrieval augmented generation")
    second = embed_text("Python FastAPI retrieval augmented generation")
    assert len(first) == DIMENSIONS
    assert first == second
    assert embed_text("") == [0.0] * DIMENSIONS


def test_embeddings_are_unit_length():
    vector = embed_text("machine learning engineer with python")
    norm = math.sqrt(sum(value * value for value in vector))
    assert math.isclose(norm, 1.0, rel_tol=1e-6)


def test_similar_text_scores_higher_than_unrelated_text():
    query = "python backend engineer with fastapi and postgres"
    related = "seeking a python engineer to build fastapi services on postgres"
    unrelated = "graphic designer skilled in photoshop and brand illustration"
    assert cosine(embed_text(query), embed_text(related)) > cosine(
        embed_text(query), embed_text(unrelated)
    )
    assert similarity(query, related) > similarity(query, unrelated)


def test_cosine_guards_against_mismatched_vectors():
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0, 0.0], [1.0]) == 0.0


def test_blob_roundtrip_and_batch_embedding():
    vector = embed_text("data pipelines and sql")
    restored = from_blob(to_blob(vector))
    assert restored == pytest.approx(vector, abs=1e-6)
    batch = embed_texts(["one", "two"])
    assert len(batch) == 2
    assert all(len(item) == DIMENSIONS for item in batch)
