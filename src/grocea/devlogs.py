from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID, uuid4

from grocea.config import get_settings


@dataclass(frozen=True, slots=True)
class HttpRequestLog:
    method: str
    path: str
    status_code: int
    duration_ms: float

    def to_payload(self) -> dict[str, object]:
        return {
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class LogEntry:
    id: int
    timestamp: datetime
    level: str
    source: str
    message: str
    request_id: str | None = None
    http: HttpRequestLog | None = None
    traceback: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "level": self.level,
            "source": self.source,
            "message": self.message,
        }
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        if self.http is not None:
            payload["http"] = self.http.to_payload()
        if self.traceback is not None:
            payload["traceback"] = self.traceback
        return payload


@dataclass(frozen=True, slots=True)
class LogSnapshot:
    stream_id: UUID
    latest_id: int | None
    reset_required: bool
    capacity: int
    entries: tuple[LogEntry, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "stream_id": str(self.stream_id),
            "latest_id": self.latest_id,
            "reset_required": self.reset_required,
            "capacity": self.capacity,
            "entries": [entry.to_payload() for entry in self.entries],
        }


class LogBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("Log capacity must be at least one entry")
        self._capacity = capacity
        self._entries: deque[LogEntry] = deque(maxlen=capacity)
        self._stream_id = uuid4()
        self._next_id = 1
        self._lock = Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def stream_id(self) -> UUID:
        return self._stream_id

    def append(
        self,
        *,
        level: str,
        source: str,
        message: str,
        request_id: str | None = None,
        http: HttpRequestLog | None = None,
        traceback: str | None = None,
    ) -> LogEntry:
        with self._lock:
            entry = LogEntry(
                id=self._next_id,
                timestamp=datetime.now(UTC),
                level=level.upper(),
                source=source,
                message=message,
                request_id=request_id,
                http=http,
                traceback=traceback,
            )
            self._next_id += 1
            self._entries.append(entry)
            return entry

    def append_request(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        request_id: str,
    ) -> LogEntry:
        rounded_duration = round(duration_ms, 1)
        return self.append(
            level=request_level(status_code),
            source="grocea.request",
            message=f"{method} {path} {status_code} {rounded_duration:.1f}ms",
            request_id=request_id,
            http=HttpRequestLog(
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=rounded_duration,
            ),
        )

    def snapshot(self, *, stream_id: UUID | None = None, after_id: int | None = None) -> LogSnapshot:
        with self._lock:
            entries = tuple(self._entries)
            latest_id = entries[-1].id if entries else None
            if stream_id is None or after_id is None:
                cursor_is_invalid = False
                selected = entries
            else:
                cursor_is_invalid = (
                    stream_id != self._stream_id
                    or (latest_id is None and after_id != 0)
                    or (latest_id is not None and after_id > latest_id)
                    or (bool(entries) and after_id < entries[0].id - 1)
                )
                selected = entries if cursor_is_invalid else tuple(entry for entry in entries if entry.id > after_id)
            return LogSnapshot(
                stream_id=self._stream_id,
                latest_id=latest_id,
                reset_required=bool(cursor_is_invalid),
                capacity=self._capacity,
                entries=selected,
            )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._stream_id = uuid4()
            self._next_id = 1


def request_level(status_code: int) -> str:
    if status_code >= 500:
        return "ERROR"
    if status_code >= 400:
        return "WARNING"
    return "INFO"


def should_capture_request(path: str) -> bool:
    if not path.startswith("/api/"):
        return False
    excluded_paths = {"/api/openapi.json"}
    excluded_prefixes = ("/api/dev/", "/api/docs", "/api/health/")
    return path not in excluded_paths and not path.startswith(excluded_prefixes)


class InMemoryLogHandler(logging.Handler):
    def __init__(self, buffer: LogBuffer) -> None:
        super().__init__(level=logging.WARNING)
        self.buffer = buffer
        self._exception_formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            traceback_text = None
            if record.exc_info is not None:
                traceback_text = self._exception_formatter.formatException(record.exc_info)
            request_id = getattr(record, "request_id", None)
            self.buffer.append(
                level=record.levelname,
                source=record.name,
                message=record.getMessage(),
                request_id=str(request_id) if request_id is not None else None,
                traceback=traceback_text,
            )
        except Exception:  # pragma: no cover - logging must never break request handling
            self.handleError(record)


def install_application_log_capture(buffer: LogBuffer) -> None:
    grocea_logger = logging.getLogger("grocea")
    for logger_name, registered_logger in logging.root.manager.loggerDict.items():
        if logger_name.startswith("grocea.") and isinstance(registered_logger, logging.Logger):
            registered_logger.disabled = False
    grocea_logger.disabled = False
    for handler in grocea_logger.handlers:
        if isinstance(handler, InMemoryLogHandler):
            handler.buffer = buffer
            return
    grocea_logger.addHandler(InMemoryLogHandler(buffer))


api_log_buffer = LogBuffer(get_settings().api_log_capacity)
