from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from httpx import Request
from sqlalchemy import Engine, make_url, text
from sqlalchemy.orm import Session

from grocea.auth import SESSION_COOKIE_NAME, create_account
from grocea.config import get_settings
from grocea.constants import LOCAL_USER_ID
from grocea.db import build_engine, get_session
from grocea.main import app
from grocea.seeding import apply_seed

ROOT = Path(__file__).resolve().parents[1]


def _assert_safe_test_url(database_url: str) -> None:
    url = make_url(database_url)
    if url.database != "grocea_test" or url.host not in {None, "", "localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Tests require a loopback PostgreSQL database named grocea_test")


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    database_url = get_settings().test_database_url
    _assert_safe_test_url(database_url)
    engine = build_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    with Session(engine) as session:
        apply_seed(session)
        session.commit()

    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Iterator[Session]:
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _test_client(db_session: Session) -> TestClient:
    def override_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    test_client = TestClient(app, raise_server_exceptions=False)
    test_client.headers.update({"X-Device-ID": "c33b8b8e-2c58-4490-a51f-fe055fc64df9"})

    def add_mutation_id(request: Request) -> None:
        if "Idempotency-Key" not in request.headers:
            request.headers["Idempotency-Key"] = str(uuid4())

    test_client.event_hooks["request"].append(add_mutation_id)
    return test_client


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    _user, issued = create_account(
        db_session,
        email="test@example.com",
        password="correct horse battery staple",
        display_name="Grocie Crumbsworth",
        user_id=LOCAL_USER_ID,
    )
    db_session.commit()
    test_client = _test_client(db_session)
    test_client.cookies.set(SESSION_COOKIE_NAME, issued.token, path="/api")
    test_client.headers["X-CSRF-Token"] = issued.csrf_token
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client(db_session: Session) -> Iterator[TestClient]:
    test_client = _test_client(db_session)
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.clear()
