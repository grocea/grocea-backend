from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from grocea.devlogs import InMemoryLogHandler, LogBuffer, api_log_buffer
from grocea.main import app, create_app


@pytest.fixture(autouse=True)
def clear_api_log_buffer() -> None:
    api_log_buffer.clear()


def test_log_buffer_evicts_old_entries_and_supports_incremental_cursors() -> None:
    buffer = LogBuffer(capacity=2)
    buffer.append(level="INFO", source="test", message="first")
    buffer.append(level="WARNING", source="test", message="second")
    buffer.append(level="ERROR", source="test", message="third")

    snapshot = buffer.snapshot()
    assert [entry.id for entry in snapshot.entries] == [2, 3]
    assert snapshot.latest_id == 3
    assert snapshot.capacity == 2

    incremental = buffer.snapshot(stream_id=snapshot.stream_id, after_id=2)
    assert [entry.id for entry in incremental.entries] == [3]
    assert incremental.reset_required is False

    expired = buffer.snapshot(stream_id=snapshot.stream_id, after_id=0)
    assert [entry.id for entry in expired.entries] == [2, 3]
    assert expired.reset_required is True

    changed_stream = buffer.snapshot(stream_id=uuid4(), after_id=3)
    assert [entry.id for entry in changed_stream.entries] == [2, 3]
    assert changed_stream.reset_required is True


def test_log_buffer_is_safe_under_concurrent_writes() -> None:
    buffer = LogBuffer(capacity=500)

    def append_entries(worker: int) -> None:
        for item in range(100):
            buffer.append(level="INFO", source="test", message=f"{worker}-{item}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append_entries, range(8)))

    entries = buffer.snapshot().entries
    ids = [entry.id for entry in entries]
    assert len(entries) == 500
    assert ids == list(range(301, 801))


def test_log_handler_records_exception_summary_request_id_and_traceback() -> None:
    buffer = LogBuffer(capacity=10)
    handler = InMemoryLogHandler(buffer)
    logger = logging.getLogger("grocea.test.capture")

    try:
        raise ValueError("broken ingredient")
    except ValueError:
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            1,
            "Developer failure",
            (),
            sys.exc_info(),
            extra={"request_id": "request-123"},
        )
    handler.emit(record)

    entry = buffer.snapshot().entries[0]
    assert entry.message == "Developer failure"
    assert entry.request_id == "request-123"
    assert entry.traceback is not None
    assert "ValueError: broken ingredient" in entry.traceback


def test_developer_pages_and_assets_are_served_without_entering_openapi() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        landing = client.get("/")
        logs = client.get("/logs")
        styles = client.get("/developer-assets/developer.css")
        script = client.get("/developer-assets/logs.js")

    assert landing.status_code == 200
    assert '<h1 id="landing-title">Grocea API</h1>' in landing.text
    assert 'href="/api/docs">OpenAPI Docs</a>' in landing.text
    assert 'href="/logs">API Logs</a>' in landing.text
    assert logs.status_code == 200
    assert '<h1 id="logs-title">API Logs</h1>' in logs.text
    assert "Minimum severity" in logs.text
    assert styles.status_code == 200
    assert "--green: #254f3a" in styles.text
    assert script.status_code == 200
    assert ".innerHTML" not in script.text
    assert "textContent" in script.text
    assert "/" not in app.openapi()["paths"]
    assert "/logs" not in app.openapi()["paths"]
    assert "/api/dev/logs" not in app.openapi()["paths"]


def test_web_assets_are_packaged_resources() -> None:
    package = files("grocea.web")
    for asset in ("index.html", "logs.html", "developer.css", "logs.js"):
        assert package.joinpath(asset).is_file()


def test_feed_records_product_request_without_sensitive_request_data() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/not-found?token=do-not-log",
            headers={"Authorization": "Bearer do-not-log", "Cookie": "session=do-not-log"},
        )
        feed = client.get("/api/dev/logs")

    assert response.status_code == 404
    assert feed.status_code == 200
    assert feed.headers["Cache-Control"] == "no-store"
    payload = feed.json()
    assert payload["capacity"] == 500
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["level"] == "WARNING"
    assert entry["http"]["path"] == "/api/not-found"
    assert entry["http"]["status_code"] == 404
    assert entry["request_id"] == response.headers["X-Request-ID"]
    serialized = feed.text
    assert "do-not-log" not in serialized
    assert "Authorization" not in serialized
    assert "Cookie" not in serialized


def test_feed_omits_developer_documentation_and_health_requests() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        for path in (
            "/",
            "/logs",
            "/developer-assets/developer.css",
            "/api/docs",
            "/api/openapi.json",
            "/api/health/live",
            "/api/dev/logs",
        ):
            assert client.get(path).status_code == 200
        feed = client.get("/api/dev/logs")

    assert feed.json()["entries"] == []


def test_feed_incremental_cursor_and_unhandled_error_traceback() -> None:
    application = create_app()

    @application.get("/api/test-crash", include_in_schema=False)
    def crash() -> None:
        raise RuntimeError("test crash")

    with TestClient(application, raise_server_exceptions=False) as client:
        initial = client.get("/api/dev/logs").json()
        failed = client.get("/api/test-crash")
        incremental = client.get(
            "/api/dev/logs",
            params={"stream_id": initial["stream_id"], "after_id": initial["latest_id"] or 0},
        ).json()

    assert failed.status_code == 500
    assert incremental["reset_required"] is False
    assert any(entry.get("http", {}).get("status_code") == 500 for entry in incremental["entries"])
    exception_entries = [entry for entry in incremental["entries"] if entry.get("traceback")]
    assert len(exception_entries) == 1
    assert exception_entries[0]["level"] == "ERROR"
    assert exception_entries[0]["request_id"] == failed.headers["X-Request-ID"]
    assert "RuntimeError: test crash" in exception_entries[0]["traceback"]
