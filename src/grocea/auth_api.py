from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request, Response, status
from sqlalchemy.exc import IntegrityError

from grocea.auth import (
    SESSION_COOKIE_NAME,
    IssuedSession,
    authenticate,
    change_password,
    cookie_secure,
    create_account,
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
        samesite="lax",
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
