from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from starlette.middleware.trustedhost import TrustedHostMiddleware

from grocea.api import router
from grocea.auth_api import router as auth_router
from grocea.config import get_settings
from grocea.developer import router as developer_router
from grocea.devlogs import api_log_buffer, install_application_log_capture, should_capture_request
from grocea.errors import install_exception_handlers

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    is_local = settings.app_env in {"local", "test"}
    default_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
    if not is_local and (
        not settings.trusted_host_list
        or set(settings.trusted_host_list) == default_hosts
        or not settings.cors_origin_list
        or set(settings.cors_origin_list) == {"http://localhost:5173", "http://127.0.0.1:5173"}
    ):
        raise RuntimeError("TRUSTED_HOSTS and CORS_ORIGINS must be configured outside local/test environments")
    if not is_local and ("*" in settings.cors_origin_list or "*" in settings.trusted_host_list):
        raise RuntimeError("Wildcard CORS origins and hosts are forbidden with credentialed sessions")
    logging.basicConfig(level=settings.log_level.upper())
    install_application_log_capture(api_log_buffer)
    application = FastAPI(
        title="Grocea API",
        version="0.1.0",
        docs_url="/api/docs" if is_local else None,
        openapi_url="/api/openapi.json" if is_local else None,
        redoc_url=None,
    )
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "Idempotency-Key", "X-Device-ID", "X-CSRF-Token"],
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

    if is_local:
        application.include_router(developer_router)
    application.include_router(auth_router)
    application.include_router(router)

    def custom_openapi() -> dict[str, Any]:
        if application.openapi_schema:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            description="Grocea authenticated API",
            routes=application.routes,
        )
        components = schema.setdefault("components", {})
        schemes = components.setdefault("securitySchemes", {})
        schemes["GroceaSessionCookie"] = {"type": "apiKey", "in": "cookie", "name": "grocea_session"}
        public_paths = {
            "/api/health/live",
            "/api/health/ready",
            "/api/auth/register",
            "/api/auth/login",
        }
        for path, operations in schema.get("paths", {}).items():
            if path in public_paths:
                continue
            for method, operation in operations.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                operation.setdefault("security", [{"GroceaSessionCookie": []}])
                if method in {"post", "put", "patch", "delete"}:
                    parameters = operation.setdefault("parameters", [])
                    if not any(
                        parameter.get("in") == "header" and parameter.get("name") == "X-CSRF-Token"
                        for parameter in parameters
                    ):
                        parameters.append(
                            {
                                "name": "X-CSRF-Token",
                                "in": "header",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        )
        application.openapi_schema = schema
        return schema

    application.openapi = custom_openapi  # type: ignore[method-assign]
    return application


app = create_app()
