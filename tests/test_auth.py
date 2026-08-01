from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from grocea.auth import SESSION_COOKIE_NAME, create_account
from grocea.models import AuthSession, ProcessedMutation, User

PASSWORD = "correct horse battery staple"


def test_anonymous_product_access_requires_authentication(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/api/profile")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_registration_normalizes_email_and_sets_host_cookie(anonymous_client: TestClient, db_session: Session) -> None:
    response = anonymous_client.post(
        "/api/auth/register",
        json={"email": "  Person@Example.COM ", "password": PASSWORD, "display_name": " Person "},
    )
    assert response.status_code == 201
    assert response.json()["account"]["email"] == "Person@Example.COM"
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE_NAME}=" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/api" in cookie
    user = db_session.scalar(select(User).where(User.normalized_email == "person@example.com"))
    assert user is not None
    assert user.password_hash and user.password_hash.startswith("$argon2id$")


def test_duplicate_email_and_invalid_credentials_are_stable(anonymous_client: TestClient) -> None:
    first = anonymous_client.post(
        "/api/auth/register",
        json={"email": "duplicate@example.com", "password": PASSWORD, "display_name": "One"},
    )
    assert first.status_code == 201
    duplicate = anonymous_client.post(
        "/api/auth/register",
        json={"email": " DUPLICATE@example.com ", "password": PASSWORD, "display_name": "Two"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "EMAIL_ALREADY_REGISTERED"
    unknown = anonymous_client.post("/api/auth/login", json={"email": "missing@example.com", "password": PASSWORD})
    wrong = anonymous_client.post(
        "/api/auth/login", json={"email": "duplicate@example.com", "password": "wrong password"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["code"] == wrong.json()["code"] == "INVALID_CREDENTIALS"


def test_csrf_logout_and_password_rotation(client: TestClient, db_session: Session) -> None:
    blocked = client.patch("/api/profile", json={"display_name": "Nope"}, headers={"X-CSRF-Token": "bad"})
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "CSRF_INVALID"

    session_response = client.get("/api/auth/session")
    assert session_response.status_code == 200
    old_csrf = session_response.json()["csrf_token"]
    changed = client.patch(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "a different correct battery phrase"},
        headers={"X-CSRF-Token": old_csrf},
    )
    assert changed.status_code == 200
    assert changed.json()["csrf_token"] != old_csrf
    assert db_session.scalar(select(AuthSession).where(AuthSession.csrf_token == old_csrf)) is None

    client.headers["X-CSRF-Token"] = changed.json()["csrf_token"]
    logout = client.post("/api/auth/logout")
    assert logout.status_code == 204
    assert client.get("/api/profile").status_code == 401


def test_processed_mutation_replay_is_scoped_to_user(anonymous_client: TestClient, db_session: Session) -> None:
    account_a, session_a = create_account(
        db_session,
        email=f"a-{uuid4()}@example.com",
        password=PASSWORD,
        display_name="A",
    )
    account_b, session_b = create_account(
        db_session,
        email=f"b-{uuid4()}@example.com",
        password=PASSWORD,
        display_name="B",
    )
    db_session.commit()
    client_a = TestClient(anonymous_client.app)
    client_b = TestClient(anonymous_client.app)
    for test_client, issued in ((client_a, session_a), (client_b, session_b)):
        test_client.cookies.set(SESSION_COOKIE_NAME, issued.token)
        test_client.headers.update({"X-CSRF-Token": issued.csrf_token, "X-Device-ID": str(uuid4())})
    mutation_id = str(uuid4())
    headers_a = {"Idempotency-Key": mutation_id}
    headers_b = {"Idempotency-Key": mutation_id}
    try:
        first = client_a.patch("/api/profile", json={"display_name": "A updated"}, headers=headers_a)
        second = client_b.patch("/api/profile", json={"display_name": "B updated"}, headers=headers_b)
    finally:
        client_a.close()
        client_b.close()
    assert first.status_code == second.status_code == 200
    assert first.json()["display_name"] == "A updated"
    assert second.json()["display_name"] == "B updated"
    rows = db_session.scalars(select(ProcessedMutation).where(ProcessedMutation.mutation_id == mutation_id)).all()
    assert {row.user_id for row in rows} == {account_a.id, account_b.id}
