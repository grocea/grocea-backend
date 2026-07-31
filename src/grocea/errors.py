from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DomainError(Exception):
    status_code: int
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


def get_request_id(request: Request) -> UUID:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, UUID) else uuid4()


def error_response(
    request: Request, status_code: int, code: str, message: str, details: dict[str, object]
) -> JSONResponse:
    request_id = get_request_id(request)
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "details": details, "request_id": str(request_id)},
        headers={"X-Request-ID": str(request_id)},
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields: list[dict[str, Any]] = []
        for item in exc.errors():
            fields.append(
                {
                    "field": ".".join(str(part) for part in item["loc"] if part not in {"body", "query", "path"}),
                    "message": item["msg"],
                    "type": item["type"],
                }
            )
        return error_response(
            request,
            422,
            "VALIDATION_ERROR",
            "Request validation failed.",
            {"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        message = str(exc.detail) if exc.detail else "HTTP request failed."
        return error_response(request, exc.status_code, code, message, {})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled request error",
            exc_info=exc,
            extra={"request_id": str(get_request_id(request))},
        )
        return error_response(request, 500, "INTERNAL_ERROR", "An unexpected error occurred.", {})
