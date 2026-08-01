from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from grocea.auth import SESSION_COOKIE_NAME, resolve_session
from grocea.config import get_settings
from grocea.db import get_session
from grocea.errors import DomainError
from grocea.models import AuthSession, User

DbSession = Annotated[Session, Depends(get_session)]


@dataclass(slots=True, frozen=True)
class AuthContext:
    user: User
    session: AuthSession


def validate_origin(request: Request) -> None:
    origin = request.headers.get("Origin")
    if not origin:
        return
    configured = set(get_settings().cors_origin_list)
    if origin not in configured:
        raise DomainError(403, "ORIGIN_NOT_ALLOWED", "Request origin is not allowed.")


def get_current_auth(
    request: Request,
    response: Response,
    session: DbSession,
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> AuthContext:
    validate_origin(request)
    resolved = resolve_session(session, session_cookie)
    if resolved is None:
        response.delete_cookie(SESSION_COOKIE_NAME, path="/api")
        raise DomainError(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")
    user, auth_session = resolved
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        csrf = request.headers.get("X-CSRF-Token")
        if csrf is None or not hmac.compare_digest(csrf, auth_session.csrf_token):
            raise DomainError(403, "CSRF_INVALID", "CSRF token is missing or invalid.")
    return AuthContext(user=user, session=auth_session)


CurrentAuth = Annotated[AuthContext, Depends(get_current_auth)]


def get_current_user(auth: CurrentAuth) -> User:
    return auth.user


CurrentUser = Annotated[User, Depends(get_current_user)]


class MutationHeaders:
    def __init__(
        self,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        device_id: Annotated[str, Header(alias="X-Device-ID")],
    ) -> None:
        from uuid import UUID

        self.mutation_id = UUID(idempotency_key)
        self.device_id = UUID(device_id)


MutationRequest = Annotated[MutationHeaders, Depends()]
