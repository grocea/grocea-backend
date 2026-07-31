from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from grocea.api import router
from grocea.config import get_settings
from grocea.developer import router as developer_router
from grocea.devlogs import api_log_buffer, install_application_log_capture, should_capture_request
from grocea.errors import install_exception_handlers

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    install_application_log_capture(api_log_buffer)
    application = FastAPI(
        title="Grocea API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "Idempotency-Key", "X-Device-ID"],
        expose_headers=["X-Request-ID", "X-State-Revision", "X-Idempotent-Replay"],
    )
    install_exception_handlers(application)

    @application.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid4()
        request.state.request_id = request_id
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            if should_capture_request(request.url.path):
                api_log_buffer.append_request(
                    method=request.method,
                    path=request.url.path,
                    status_code=500,
                    duration_ms=(time.monotonic() - started) * 1000,
                    request_id=str(request_id),
                )
            raise
        response.headers["X-Request-ID"] = str(request_id)
        duration_ms = (time.monotonic() - started) * 1000
        if should_capture_request(request.url.path):
            api_log_buffer.append_request(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
                request_id=str(request_id),
            )
        logger.info(
            "%s %s %s %.1fms request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response

    application.include_router(developer_router)
    application.include_router(router)
    return application


app = create_app()
