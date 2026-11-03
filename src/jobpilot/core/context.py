"""Request-scoped identity.

The web app binds the signed-in account for the duration of a request. The CLI
and MCP servers bind the workspace user explicitly (or fall back to the local
workspace). Services read the current id from here, so every query can enforce
ownership without threading a parameter through every call site.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from collections.abc import Iterator

LOCAL_USER_ID = "local"

_current_user_id: ContextVar[str] = ContextVar("jobpilot_user_id", default=LOCAL_USER_ID)


def current_user_id() -> str:
    """Return the active user id, defaulting to the local CLI workspace."""
    return _current_user_id.get() or LOCAL_USER_ID


def set_current_user_id(user_id: str) -> Token[str]:
    return _current_user_id.set((user_id or LOCAL_USER_ID).strip())


def reset_current_user_id(token: Token[str]) -> None:
    _current_user_id.reset(token)


@contextmanager
def user_scope(user_id: str) -> Iterator[str]:
    """Temporarily run code as another user (CLI, tests, MCP startup)."""
    token = set_current_user_id(user_id)
    try:
        yield current_user_id()
    finally:
        reset_current_user_id(token)
