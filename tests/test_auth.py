"""Signup, login, logout and API protection."""

from __future__ import annotations

from jobpilot.core.db import get_db


def _set_csrf(client, token: str = "csrf-test") -> str:
    with client.session_transaction() as sess:
        sess["csrf_token"] = token
    return token


def _signup(client, email: str, password: str = "correct-horse-1", name: str = "Test Person"):
    token = _set_csrf(client)
    return client.post(
        "/signup",
        data={
            "csrf_token": token,
            "name": name,
            "email": email,
            "password": password,
            "confirm": password,
        },
    )


def test_anonymous_requests_are_redirected_but_health_is_public(anon_client):
    page = anon_client.get("/")
    assert page.status_code == 302
    assert "/login" in page.headers["Location"]

    api = anon_client.get("/api/dashboard")
    assert api.status_code == 401
    assert api.get_json()["ok"] is False

    health = anon_client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json()["ok"] is True
    assert "budget" not in health.get_json()
    assert health.get_json()["database"] == {"ok": True}
    assert "path" not in health.get_json()["database"]


def test_login_and_signup_pages_render(anon_client):
    for path in ("/login", "/signup"):
        response = anon_client.get(path)
        assert response.status_code == 200
        assert b"JobPilot" in response.data


def test_signup_creates_account_and_logs_in(anon_client):
    response = _signup(anon_client, "new@example.com", name="New Person")
    assert response.status_code == 302
    assert "/setup" in response.headers["Location"]

    profile = anon_client.get("/api/profile")
    assert profile.status_code == 200
    assert profile.get_json()["profile"]["full_name"] in ("", "Your Name")

    health = anon_client.get("/api/health").get_json()
    assert health["user"]["email"] == "new@example.com"
    assert health["budget"]["max_total"] > 0


def test_signup_validation_errors(anon_client):
    token = _set_csrf(anon_client)
    mismatch = anon_client.post(
        "/signup",
        data={
            "csrf_token": token,
            "email": "x@example.com",
            "password": "long-enough-1",
            "confirm": "different-pass-1",
        },
    )
    assert mismatch.status_code == 400
    assert b"do not match" in mismatch.data

    _signup(anon_client, "dup@example.com")
    from jobpilot.ui.app import create_app

    other = create_app({"TESTING": True, "SECRET_KEY": "test-secret"}).test_client()
    token = _set_csrf(other)
    duplicate = other.post(
        "/signup",
        data={
            "csrf_token": token,
            "email": "DUP@example.com",
            "password": "long-enough-2",
            "confirm": "long-enough-2",
        },
    )
    assert duplicate.status_code == 400
    assert b"already exists" in duplicate.data


def test_authenticated_user_can_change_password(anon_client):
    _signup(anon_client, "change@example.com", "correct-horse-1")
    with anon_client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    wrong = anon_client.post(
        "/account/password",
        data={
            "csrf_token": csrf,
            "current_password": "not-the-password",
            "new_password": "correct-horse-2",
        },
    )
    assert wrong.status_code == 302
    assert "password_error" in wrong.headers["Location"]

    changed = anon_client.post(
        "/account/password",
        data={
            "csrf_token": csrf,
            "current_password": "correct-horse-1",
            "new_password": "correct-horse-2",
        },
    )
    assert changed.status_code == 302
    assert "password=updated" in changed.headers["Location"]

    anon_client.post("/logout", data={"csrf_token": csrf})
    token = _set_csrf(anon_client)
    old = anon_client.post(
        "/login",
        data={"csrf_token": token, "email": "change@example.com", "password": "correct-horse-1"},
    )
    assert old.status_code == 401
    new = anon_client.post(
        "/login",
        data={"csrf_token": token, "email": "change@example.com", "password": "correct-horse-2"},
    )
    assert new.status_code == 302


def test_logout_and_login_roundtrip(anon_client):
    _signup(anon_client, "round@example.com", "correct-horse-1")

    with anon_client.session_transaction() as sess:
        csrf = sess["csrf_token"]
        assert sess["user_id"]

    logout = anon_client.post("/logout", data={"csrf_token": csrf})
    assert logout.status_code == 302
    assert "/login" in logout.headers["Location"]
    assert anon_client.get("/api/profile").status_code == 401

    token = _set_csrf(anon_client)
    wrong = anon_client.post(
        "/login",
        data={"csrf_token": token, "email": "round@example.com", "password": "nope"},
    )
    assert wrong.status_code == 401

    right = anon_client.post(
        "/login",
        data={
            "csrf_token": token,
            "email": "round@example.com",
            "password": "correct-horse-1",
        },
    )
    assert right.status_code == 302
    assert anon_client.get("/api/profile").status_code == 200


def test_form_posts_require_a_csrf_token(anon_client):
    response = anon_client.post(
        "/login", data={"email": "nobody@example.com", "password": "whatever-1"}
    )
    assert response.status_code == 400
    assert b"session expired" in response.data.lower()


def test_two_accounts_have_isolated_workspaces(anon_client):
    from jobpilot.ui.app import create_app

    first = anon_client
    _signup(first, "alice@example.com", name="Alice")
    first.put(
        "/api/profile",
        json={"full_name": "Alice Example", "skills": ["Python"], "target_roles": ["AI Engineer"]},
    )

    second = create_app({"TESTING": True, "SECRET_KEY": "test-secret"}).test_client()
    _signup(second, "bob@example.com", name="Bob")

    alice = first.get("/api/profile").get_json()["profile"]
    bob = second.get("/api/profile").get_json()["profile"]
    assert alice["full_name"] == "Alice Example"
    assert bob["full_name"] != "Alice Example"
    assert bob["skills"] == []

    db = get_db()
    rows = db.query("SELECT id, email FROM users ORDER BY email")
    assert [row["email"] for row in rows] == ["alice@example.com", "bob@example.com"]
    assert rows[0]["id"] != rows[1]["id"]
