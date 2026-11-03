"""Account creation, authentication and workspace resolution."""

from __future__ import annotations

import pytest

from jobpilot.core.context import user_scope
from jobpilot.services.users import AccountError, UserService, resolve_workspace_user


def test_create_and_authenticate_user(db):
    users = UserService(db)
    user = users.create("Person@Example.com ", "correct-horse", "Ada Lovelace")

    assert user.email == "person@example.com"
    assert user.name == "Ada Lovelace"
    assert users.get(user.id).email == "person@example.com"
    assert users.count() == 1

    signed_in = users.authenticate("person@example.com", "correct-horse")
    assert signed_in is not None
    assert signed_in.id == user.id
    assert signed_in.last_login_at is not None

    assert users.authenticate("person@example.com", "wrong") is None
    assert users.authenticate("missing@example.com", "correct-horse") is None


def test_duplicate_email_is_rejected_case_insensitively(db):
    users = UserService(db)
    users.create("dup@example.com", "long-enough-1")
    with pytest.raises(AccountError, match="already exists"):
        users.create("DUP@example.com", "long-enough-2")


def test_weak_password_and_bad_email_are_rejected(db):
    users = UserService(db)
    with pytest.raises(AccountError, match="valid email"):
        users.create("not-an-email", "long-enough-1")
    with pytest.raises(AccountError, match="8 characters"):
        users.create("ok@example.com", "short")


def test_password_update_requires_the_current_password(db):
    users = UserService(db)
    user = users.create("change@example.com", "old-password-1")
    with pytest.raises(AccountError, match="incorrect"):
        users.update_password(user.id, "wrong-password", "new-password-1")

    users.update_password(user.id, "old-password-1", "new-password-1")
    assert users.authenticate("change@example.com", "old-password-1") is None
    assert users.authenticate("change@example.com", "new-password-1") is not None


def test_new_account_starts_with_a_private_blank_profile(db):
    from jobpilot.services.profile import ProfileService

    user = UserService(db).create("fresh@example.com", "long-enough-1", "Fresh User")
    with user_scope(user.id):
        profile = ProfileService(db).get()
        assert profile.full_name in ("", "Your Name")
        assert profile.skills == []


def test_resolve_workspace_user_from_environment(db, monkeypatch):
    users = UserService(db)
    user = users.create("cli@example.com", "long-enough-1")

    monkeypatch.delenv("JOBPILOT_USER_ID", raising=False)
    monkeypatch.delenv("JOBPILOT_USER_EMAIL", raising=False)
    assert resolve_workspace_user() == "local"

    monkeypatch.setenv("JOBPILOT_USER_EMAIL", "cli@example.com")
    assert resolve_workspace_user() == user.id

    monkeypatch.setenv("JOBPILOT_USER_ID", "explicit-workspace")
    assert resolve_workspace_user() == "explicit-workspace"

    monkeypatch.delenv("JOBPILOT_USER_ID")
    monkeypatch.setenv("JOBPILOT_USER_EMAIL", "nobody@example.com")
    with pytest.raises(RuntimeError, match="does not match"):
        resolve_workspace_user()
