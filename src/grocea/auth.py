from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from email_validator import EmailNotValidError
from email_validator import validate_email as validate_email_address
from pwdlib import PasswordHash
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from grocea.config import get_settings
from grocea.constants import LOCAL_USER_ID
from grocea.errors import DomainError
from grocea.models import AuthSession, User

SESSION_COOKIE_NAME = "grocea_session"
SESSION_TTL = timedelta(days=30)
password_hash = PasswordHash.recommended()

# Valid Argon2id hash. Used only to keep unknown-email login timing close to known-email login.
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$MIIRqgvgQbgj220jfp0MPA$YfwJSVjtjSU0zzV/P3S9nnQ/USre2wvJMjfCIjrTQbg"
)


@dataclass(slots=True, frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    expires_at: datetime


def normalize_email(email: str) -> str:
    candidate = email.strip()
    try:
        validate_email_address(candidate, check_deliverability=False)
    except EmailNotValidError as error:
        raise DomainError(422, "EMAIL_INVALID", "Enter a valid email address.") from error
    return candidate.casefold()


def validate_password(password: str) -> None:
    if not 15 <= len(password) <= 128:
        raise DomainError(422, "PASSWORD_INVALID", "Password must be between 15 and 128 characters.")


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def _now() -> datetime:
    return datetime.now(UTC)


def _verify_password(password: str, stored_hash: str) -> tuple[bool, str | None]:
    try:
        valid, replacement = password_hash.verify_and_update(password, stored_hash)
    except Exception:
        return False, None
    return valid, replacement


def _new_session(session: Session, user: User) -> IssuedSession:
    now = _now()
    session.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = now + SESSION_TTL
    session.add(
        AuthSession(
            user_id=user.id,
            token_hash=_token_hash(token),
            csrf_token=csrf_token,
            expires_at=expires_at,
        )
    )
    return IssuedSession(token=token, csrf_token=csrf_token, expires_at=expires_at)


def create_account(
    session: Session,
    *,
    email: str,
    password: str,
    display_name: str,
    user_id: UUID | None = None,
) -> tuple[User, IssuedSession]:
    validate_password(password)
    canonical = normalize_email(email)
    legacy = session.get(User, LOCAL_USER_ID)
    if legacy is not None and legacy.password_hash is None:
        raise DomainError(
            409,
            "LOCAL_PROFILE_CLAIM_REQUIRED",
            "Claim the legacy Local Profile before registering accounts.",
        )
    user = User(
        email=email.strip(),
        normalized_email=canonical,
        password_hash=password_hash.hash(password),
        display_name=display_name.strip(),
        preferred_servings=2,
        measurement_system="metric",
    )
    if user_id is not None:
        user.id = user_id
    session.add(user)
    session.flush()
    return user, _new_session(session, user)


def authenticate(session: Session, *, email: str, password: str) -> tuple[User, IssuedSession]:
    canonical = normalize_email(email)
    user = session.scalar(select(User).where(User.normalized_email == canonical))
    stored_hash = user.password_hash if user is not None and user.password_hash is not None else DUMMY_PASSWORD_HASH
    valid, replacement = _verify_password(password, stored_hash)
    if not valid or user is None:
        raise DomainError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    if replacement is not None:
        user.password_hash = replacement
    return user, _new_session(session, user)


def resolve_session(session: Session, token: str | None) -> tuple[User, AuthSession] | None:
    if not token:
        return None
    auth_session = session.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if auth_session is None or auth_session.expires_at <= _now():
        return None
    user = session.get(User, auth_session.user_id)
    if user is None or user.password_hash is None:
        return None
    return user, auth_session


def change_password(session: Session, user: User, *, current_password: str, new_password: str) -> IssuedSession:
    validate_password(new_password)
    if user.password_hash is None:
        raise DomainError(401, "INVALID_CREDENTIALS", "Current password is incorrect.")
    valid, _replacement = _verify_password(current_password, user.password_hash)
    if not valid:
        raise DomainError(401, "INVALID_CREDENTIALS", "Current password is incorrect.")
    user.password_hash = password_hash.hash(new_password)
    session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    return _new_session(session, user)


def revoke_session(session: Session, auth_session: AuthSession) -> None:
    session.delete(auth_session)


def claim_legacy_profile(session: Session, *, legacy_user_id: UUID, email: str, password: str) -> User:
    validate_password(password)
    user = session.get(User, legacy_user_id)
    if user is None:
        raise DomainError(404, "LOCAL_PROFILE_NOT_FOUND", "Legacy Local Profile was not found.")
    if user.password_hash is not None or user.normalized_email is not None:
        raise DomainError(409, "LOCAL_PROFILE_ALREADY_CLAIMED", "Legacy Local Profile is already claimed.")
    canonical = normalize_email(email)
    existing = session.scalar(select(User).where(User.normalized_email == canonical, User.id != legacy_user_id))
    if existing is not None:
        raise DomainError(409, "EMAIL_ALREADY_REGISTERED", "An account with this email already exists.")
    user.email = email.strip()
    user.normalized_email = canonical
    user.password_hash = password_hash.hash(password)
    session.flush()
    return user


def session_max_age(expires_at: datetime | None = None) -> int:
    target = expires_at or (_now() + SESSION_TTL)
    return max(0, int((target - _now()).total_seconds()))


def cookie_secure() -> bool:
    return get_settings().app_env not in {"local", "test"}


def cookie_samesite() -> Literal["lax", "strict", "none"]:
    return get_settings().auth_cookie_samesite
