"""Rolling conversation summaries for context compaction."""

from __future__ import annotations

import re
from collections.abc import Iterable

MAX_SUMMARY_CHARS = 1200


def summarize_messages(messages: Iterable[dict], previous: str = "") -> str:
    """Extractive summary: goals, outcomes and open threads.

    Deterministic and free. When an LLM is configured the harness can pass a
    richer summary through the same interface.
    """
    user_turns = [
        m.get("content", "").strip()
        for m in messages
        if m.get("role") == "user" and m.get("content", "").strip()
    ]
    assistant_turns = [
        m.get("content", "").strip()
        for m in messages
        if m.get("role") == "assistant" and m.get("content", "").strip()
    ]
    if not user_turns and not assistant_turns:
        return previous

    goals: list[str] = []
    for turn in user_turns[-6:]:
        cleaned = _compress(turn, 190)
        if cleaned and cleaned not in goals:
            goals.append(cleaned)

    outcomes: list[str] = []
    for turn in assistant_turns[-4:]:
        cleaned = _compress(turn, 160)
        if cleaned and cleaned not in outcomes:
            outcomes.append(cleaned)

    parts: list[str] = []
    if previous:
        parts.append(previous.strip())
    parts.append("Recent user goals: " + " | ".join(goals[-4:]))
    if outcomes:
        parts.append("Latest outcomes: " + " | ".join(outcomes[-3:]))
    summary = "\n".join(parts)
    return summary[-MAX_SUMMARY_CHARS:]


def _compress(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(please\s+|can you\s+|could you\s+)", "", text, flags=re.I)
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text
