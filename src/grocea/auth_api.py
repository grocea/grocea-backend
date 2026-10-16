from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Request, Response, status
from sqlalchemy.exc import IntegrityError

from grocea.account import (
    RECOVERY_MESSAGE,
    deliver_reset,
    issue_reset,
    notify_password_changed,
    recovery_settings,
    reset_password,
    throttle,
)
from grocea.auth import (
    SESSION_COOKIE_NAME,
    IssuedSession,
    authenticate,
    change_password,
    cookie_samesite,
    cookie_secure,
    create_account,
    normalize_email,
    revoke_session,
    session_max_age,
)
from grocea.dependencies import CurrentAuth, DbSession, validate_origin
from grocea.errors import DomainError
from grocea.models import User
from grocea.schemas import (
    AuthAccountResponse,
    AuthLoginRequest,
    AuthPasswordChangeRequest,
    AuthRegisterRequest,
    AuthSessionResponse,
    ErrorResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RecoveryResponse,
)

router = APIRouter(
    prefix="/api/auth",
    tags=["auth"],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)


def _set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=session_max_age(expires_at),
        httponly=True,
        secure=cookie_secure(),
        samesite=cookie_samesite(),
        path="/api",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/api")


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _session_response(user: User, issued: IssuedSession) -> AuthSessionResponse:
    if user.email is None:
        raise DomainError(503, "ACCOUNT_NOT_CONFIGURED", "Account credentials are not configured.")
    return AuthSessionResponse(
        account=AuthAccountResponse(id=user.id, email=user.email),
        csrf_token=issued.csrf_token,
        expires_at=issued.expires_at,
    )


@router.post("/register", response_model=AuthSessionResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: AuthRegisterRequest,
    request: Request,
    response: Response,
    session: DbSession,
) -> AuthSessionResponse:
    validate_origin(request)
    try:
        user, issued = create_account(
            session,
            email=str(payload.email),
            password=payload.password,
            display_name=payload.display_name,
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if "normalized_email" in str(error).lower() or "uq_users_normalized_email" in str(error).lower():
            raise DomainError(409, "EMAIL_ALREADY_REGISTERED", "An account with this email already exists.") from error
        raise
    _set_session_cookie(response, issued.token, issued.expires_at)
    _no_store(response)
    return _session_response(user, issued)


@router.post("/login", response_model=AuthSessionResponse)
def login(payload: AuthLoginRequest, request: Request, response: Response, session: DbSession) -> AuthSessionResponse:
    validate_origin(request)
    user, issued = authenticate(session, email=str(payload.email), password=payload.password)
    session.commit()
    _set_session_cookie(response, issued.token, issued.expires_at)
    _no_store(response)
    return _session_response(user, issued)


@router.get("/session", response_model=AuthSessionResponse)
def read_session(auth: CurrentAuth, response: Response) -> AuthSessionResponse:
    _no_store(response)
    issued = IssuedSession(token="", csrf_token=auth.session.csrf_token, expires_at=auth.session.expires_at)
    return _session_response(auth.user, issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(auth: CurrentAuth, response: Response, session: DbSession) -> Response:
    revoke_session(session, auth.session)
    session.commit()
    _clear_session_cookie(response)
    _no_store(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.patch("/password", response_model=AuthSessionResponse)
def update_password(
    payload: AuthPasswordChangeRequest,
    auth: CurrentAuth,
    response: Response,
    session: DbSession,
) -> AuthSessionResponse:
    issued = change_password(
        session,
        auth.user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    session.commit()
    _set_session_cookie(response, issued.token, issued.expires_at)
    _no_store(response)
    return _session_response(auth.user, issued)


@router.post("/password-reset/request", response_model=RecoveryResponse, status_code=202)
def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    response: Response,
    session: DbSession,
    background_tasks: BackgroundTasks,
) -> RecoveryResponse:
    validate_origin(request)
    started = time.monotonic()
    settings = recovery_settings()  # same failure for every address
    canonical = normalize_email(str(payload.email))
    peer = request.client.host if request.client else "unknown"
    ip_allowed = throttle(session, "request-ip", peer, 30, 3600)
    address_allowed = throttle(session, "request-email", canonical, settings.reset_requests_per_hour, 3600)
    cooldown_allowed = throttle(session, "request-cooldown", canonical, 1, 60)
    if ip_allowed and address_allowed and cooldown_allowed:
        issued = issue_reset(session, canonical)
        if issued:
            background_tasks.add_task(deliver_reset, *issued)
    _no_store(response)
    time.sleep(max(0, 0.15 - (time.monotonic() - started)))
    return RecoveryResponse(message=RECOVERY_MESSAGE)


@router.post("/password-reset/confirm", status_code=204)
def confirm_password_reset(
    payload: PasswordResetConfirm,
    request: Request,
    response: Response,
    session: DbSession,
    background_tasks: BackgroundTasks,
) -> Response:
    validate_origin(request)
    peer = request.client.host if request.client else "unknown"
    if not throttle(session, "confirm-ip", peer, 60, 3600):
        raise DomainError(429, "RECOVERY_RATE_LIMITED", "Too many attempts. Try again later.")
    recipient = reset_password(session, payload.token, payload.new_password)
    background_tasks.add_task(notify_password_changed, recipient)
    _clear_session_cookie(response)
    _no_store(response)
    response.status_code = 204
    return response
