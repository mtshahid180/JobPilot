"""Memory write/read policy.

The model proposes; the harness decides. Profile-affecting facts need explicit
confirmation, near-duplicates merge, and contradictions are flagged instead of
silently overwriting the user's stated preferences.
"""

from __future__ import annotations

import re

from jobpilot.core.models import Memory, MemoryProposal

PROFILE_PATTERNS = [
    r"\bmy (name|email|phone|degree|university|address|visa|salary)\b",
    r"\b(i (have|hold|earned|graduated|worked at|studied))\b",
    r"\b(years? of experience|experience at|worked as)\b",
    r"\b(i (am|'m) (a|an) )\b",
]

PREFERENCE_PATTERNS = [
    r"\b(i|we) (prefer|like|want|need|avoid|hate|dislike|only)\b",
    r"\b(remote|hybrid|onsite|on-site)\b",
    r"\b(salary|compensation|pay)\b",
    r"\b(location|city|country|relocat)",
    r"\b(target|looking for|interested in)\b",
    r"\b(no|not)\b.*\b(jobs?|roles?|companies|technologies|stack)\b",
]

RULE_PATTERNS = [
    r"\b(always|never|must|do not|don't)\b",
    r"\b(only apply|only show|never apply)\b",
]

EPISODE_PATTERNS = [
    r"\b(applied|rejected|interview|offer|follow[- ]?up|declined)\b",
    r"\b(i (sent|submitted|heard back|got))\b",
]


def classify(content: str) -> tuple[str, bool, str]:
    """Return (kind, requires_confirmation, reason)."""
    text = (content or "").lower()
    if any(re.search(pattern, text) for pattern in PROFILE_PATTERNS):
        return "semantic", True, "changes profile facts; needs your confirmation"
    if any(re.search(pattern, text) for pattern in EPISODE_PATTERNS):
        return "episodic", False, "records something that happened"
    if any(re.search(pattern, text) for pattern in RULE_PATTERNS):
        return "rule", False, "standing rule for future searches"
    if any(re.search(pattern, text) for pattern in PREFERENCE_PATTERNS):
        return "preference", False, "job-search preference"
    return "semantic", False, "durable note"


def propose(content: str, provenance: str = "") -> MemoryProposal:
    content = re.sub(r"\s+", " ", content).strip()
    kind, needs_confirm, reason = classify(content)
    if kind == "semantic" and not needs_confirm:
        kind = "preference" if _looks_like_preference(content) else "semantic"
    return MemoryProposal(
        content=content,
        kind=kind,  # type: ignore[arg-type]
        reason=reason,
        requires_confirmation=needs_confirm,
        tags=_tags(content),
    )


def _looks_like_preference(content: str) -> bool:
    lowered = content.lower()
    return any(
        word in lowered
        for word in ("prefer", "remote", "onsite", "salary", "location", "looking for", "target")
    )


def _tags(content: str) -> list[str]:
    tags: list[str] = []
    lowered = content.lower()
    for tag, words in {
        "remote": ("remote", "wfh"),
        "location": ("location", "city", "relocat", "islamabad", "lahore", "karachi"),
        "salary": ("salary", "compensation", "pay"),
        "role": ("role", "engineer", "developer", "scientist", "analyst"),
        "stack": ("python", "react", "sql", "llm", "ml", "ai"),
        "constraint": ("avoid", "never", "only", "no "),
    }.items():
        if any(word in lowered for word in words):
            tags.append(tag)
    return tags


def detect_conflict(content: str, existing: list[Memory]) -> Memory | None:
    """Return the memory this new statement contradicts, if any."""
    new = content.lower()
    new_remote = _remote_stance(new)
    new_salary = _salary_number(new)
    for memory in existing:
        old = memory.content.lower()
        old_remote = _remote_stance(old)
        if new_remote and old_remote and new_remote != old_remote:
            return memory
        old_salary = _salary_number(old)
        if new_salary and old_salary and abs(new_salary - old_salary) / max(old_salary, 1) > 0.25:
            return memory
    return None


def _remote_stance(text: str) -> str:
    remote_only = bool(re.search(r"\b(remote only|only remote|fully remote|no onsite|no on-site)\b", text))
    onsite_only = bool(re.search(r"\b(onsite only|on-site only|only onsite|no remote|office only)\b", text))
    if remote_only:
        return "remote"
    if onsite_only:
        return "onsite"
    return ""


def _salary_number(text: str) -> int | None:
    if not any(word in text for word in ("salary", "compensation", "pay", "floor")):
        return None
    match = re.search(r"(\d[\d,]{2,})", text)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def importance_for(kind: str) -> float:
    return {
        "rule": 0.95,
        "preference": 0.8,
        "semantic": 0.7,
        "episodic": 0.5,
    }.get(kind, 0.5)
