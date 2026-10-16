"""Account controls. All owned writes serialize on the User row."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from grocea.auth import _verify_password, normalize_email, password_hash, validate_password
from grocea.config import Settings, get_settings
from grocea.db import Base, SessionFactory
from grocea.errors import DomainError
from grocea.models import (
    ActivityEvent,
    AuthSession,
    BasketItem,
    Category,
    GroceryList,
    GroceryListItem,
    GroceryListItemSource,
    GroceryListRecipe,
    Ingredient,
    PantryStock,
    PasswordResetToken,
    ProcessedMutation,
    Recipe,
    RecipeIngredient,
    RecipeStep,
    RecoveryThrottle,
    StockChange,
    User,
)
from grocea.schemas import AccountExportResponse

logger = logging.getLogger(__name__)
RECOVERY_MESSAGE = (
    "If an account matches, you may receive a reset link. Check your inbox and try again later if needed."
)


def recovery_settings() -> Settings:
    settings = get_settings()
    try:
        origin = urlsplit(settings.public_app_origin)
        if not origin.hostname or not re.fullmatch(r"[A-Za-z0-9.:-]+", origin.hostname) or origin.port == 0:
            raise ValueError
    except ValueError as error:
        raise DomainError(503, "RECOVERY_UNAVAILABLE", "Password recovery is temporarily unavailable.") from error
    local = settings.app_env in {"local", "test"}
    valid_origin = origin.scheme == "https" or (
        local and origin.scheme == "http" and origin.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    plain_local = local and settings.smtp_host in {"localhost", "127.0.0.1", "::1"}
    if (
        not valid_origin
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or origin.path not in {"", "/"}
        or not settings.smtp_host
        or not settings.smtp_from
        or not settings.recovery_throttle_secret.get_secret_value()
        or bool(settings.smtp_username) != bool(settings.smtp_password.get_secret_value())
        or (settings.smtp_security == "none" and not plain_local)
    ):
        raise DomainError(503, "RECOVERY_UNAVAILABLE", "Password recovery is temporarily unavailable.")
    return settings


def throttle(session: Session, action: str, subject: str, limit: int, seconds: int) -> bool:
    settings = get_settings()
    key = settings.recovery_throttle_secret.get_secret_value()
    if not key:
        raise DomainError(503, "RECOVERY_UNAVAILABLE", "Password recovery is temporarily unavailable.")
    now = datetime.now(UTC)
    start = datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, UTC)
    digest = hmac.digest(key.encode(), subject.encode(), "sha256")
    expired = (
        select(RecoveryThrottle.action, RecoveryThrottle.subject_hash, RecoveryThrottle.window_start)
        .where(RecoveryThrottle.expires_at < now)
        .limit(100)
    )
    from sqlalchemy import tuple_

    session.execute(
        delete(RecoveryThrottle).where(
            tuple_(RecoveryThrottle.action, RecoveryThrottle.subject_hash, RecoveryThrottle.window_start).in_(expired)
        )
    )
    statement = insert(RecoveryThrottle).values(
        action=action,
        subject_hash=digest,
        window_start=start,
        count=1,
        expires_at=start + timedelta(seconds=seconds * 2),
    )
    upsert = statement.on_conflict_do_update(
        index_elements=[RecoveryThrottle.action, RecoveryThrottle.subject_hash, RecoveryThrottle.window_start],
        set_={"count": RecoveryThrottle.count + 1},
    ).returning(RecoveryThrottle.count)
    count = session.scalar(upsert)
    session.commit()  # Persist rejected attempts too; independent of account writes.
    return count is not None and count <= limit


def send_mail(recipient: str, subject: str, text: str) -> bool:
    """Real SMTP attempt; accepted by SMTP does not imply inbox delivery."""
    try:
        settings = recovery_settings()
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(text)
        context = ssl.create_default_context()
        transport = (
            smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds, context=context
            )
            if settings.smtp_security == "ssl"
            else smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds)
        )
        with transport as smtp:
            smtp.ehlo()
            if settings.smtp_security == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            refused = smtp.send_message(message)
            if refused:
                logger.warning("Recovery SMTP recipient refused")
                return False
        return True
    except (OSError, smtplib.SMTPException, DomainError, ValueError):
        # SMTP exceptions can include recipient addresses/message data. Never format them.
        logger.warning("Recovery SMTP delivery attempt failed")
        return False


def deliver_reset(user_id: UUID, token: str) -> None:
    with SessionFactory() as session:
        record = session.scalar(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == hashlib.sha256(token.encode()).digest())
        )
        user = session.get(User, user_id)
        if record is None or user is None or user.email is None or record.expires_at <= datetime.now(UTC):
            return
        recipient = user.email
    origin = get_settings().public_app_origin.rstrip("/")
    send_mail(
        recipient,
        "Reset your Grocea password",
        (
            f"Open this link to choose a new password:\n{origin}/reset-password#token={token}\n\n"
            "This link expires soon and works once. If you did not request it, ignore this email."
        ),
    )


def issue_reset(session: Session, email: str) -> tuple[UUID, str] | None:
    canonical = normalize_email(email)
    user = session.scalar(
        select(User)
        .where(User.normalized_email == canonical)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or user.password_hash is None:
        session.rollback()
        return None
    now = datetime.now(UTC)
    session.execute(
        delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.expires_at <= now)
    )
    token = secrets.token_urlsafe(32)
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode()).digest(),
            expires_at=now + timedelta(minutes=get_settings().reset_token_minutes),
        )
    )
    session.commit()
    return user.id, token


def reset_password(session: Session, token: str, new_password: str) -> str:
    invalid = DomainError(400, "PASSWORD_RESET_INVALID", "This reset link is invalid or expired. Request a new link.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise invalid
    digest = hashlib.sha256(token.encode("ascii")).digest()
    user_id = session.scalar(select(PasswordResetToken.user_id).where(PasswordResetToken.token_hash == digest))
    if user_id is None:
        raise invalid
    user = session.scalar(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )
    record = session.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == digest)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or record is None or record.expires_at <= datetime.now(UTC) or user.email is None:
        raise invalid
    validate_password(new_password)
    user.password_hash = password_hash.hash(new_password)
    session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    session.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
    recipient = user.email
    session.commit()
    return recipient


def notify_password_changed(recipient: str) -> None:
    send_mail(
        recipient,
        "Your Grocea password changed",
        (
            "Your Grocea password has changed. All previous sessions and reset links are now invalid. "
            "Sign in normally with your new password. If this was not you, request password recovery immediately."
        ),
    )


# Explicit column allowlists. Archives and historical snapshots are retained.
EXPORT_FIELDS: list[tuple[type[Base], str]] = [
    (User, "id email display_name preferred_servings measurement_system created_at updated_at"),
    (Category, "id user_id name archived_at created_at updated_at"),
    (Ingredient, "id user_id category_id name measurement_family archived_at created_at updated_at"),
    (PantryStock, "id user_id ingredient_id quantity low_stock_threshold refill_quantity created_at updated_at"),
    (Recipe, "id user_id status name description base_servings archived_at created_at updated_at"),
    (RecipeIngredient, "id recipe_id ingredient_id position quantity quantity_input unit"),
    (RecipeStep, "id recipe_id position body"),
    (ActivityEvent, "id user_id event_type title detail occurred_at recipe_id servings reversal_of reversed_at"),
    (StockChange, "id event_id ingredient_id before delta after"),
    (BasketItem, "id user_id recipe_id servings position created_at updated_at"),
    (GroceryList, "id user_id title status completed_at created_at updated_at"),
    (GroceryListRecipe, "id grocery_list_id recipe_id recipe_snapshot_id recipe_name position servings base_servings"),
    (
        GroceryListItem,
        (
            "id grocery_list_id ingredient_id original_ingredient_id label category_name measurement_family quantity "
            "unit checked origin edited original_required original_pantry original_quantity created_at updated_at"
        ),
    ),
    (GroceryListItemSource, "id grocery_list_item_id recipe_snapshot_id recipe_name servings quantity unit"),
]


def export_account(session: Session, user: User) -> AccountExportResponse:
    recipe_ids = select(Recipe.id).where(or_(Recipe.user_id == user.id, Recipe.user_id.is_(None)))
    list_ids = select(GroceryList.id).where(GroceryList.user_id == user.id)
    item_ids = select(GroceryListItem.id).where(GroceryListItem.grocery_list_id.in_(list_ids))
    event_ids = select(ActivityEvent.id).where(ActivityEvent.user_id == user.id)
    filters = {
        User: User.id == user.id,
        Category: or_(Category.user_id == user.id, Category.user_id.is_(None)),
        Ingredient: or_(Ingredient.user_id == user.id, Ingredient.user_id.is_(None)),
        Recipe: or_(Recipe.user_id == user.id, Recipe.user_id.is_(None)),
        RecipeIngredient: RecipeIngredient.recipe_id.in_(recipe_ids),
        RecipeStep: RecipeStep.recipe_id.in_(recipe_ids),
        PantryStock: PantryStock.user_id == user.id,
        ActivityEvent: ActivityEvent.user_id == user.id,
        StockChange: StockChange.event_id.in_(event_ids),
        BasketItem: BasketItem.user_id == user.id,
        GroceryList: GroceryList.user_id == user.id,
        GroceryListRecipe: GroceryListRecipe.grocery_list_id.in_(list_ids),
        GroceryListItem: GroceryListItem.grocery_list_id.in_(list_ids),
        GroceryListItemSource: GroceryListItemSource.grocery_list_item_id.in_(item_ids),
    }
    data: dict[str, list[dict[str, object]]] = {}
    for model, names in EXPORT_FIELDS:
        rows = session.execute(
            select(*(getattr(model, field) for field in names.split())).where(filters[model]).order_by("id")
        ).mappings()
        data[str(model.__tablename__)] = [
            {
                str(key): f"{value:.3f}"
                if isinstance(value, Decimal)
                else value.isoformat()
                if isinstance(value, datetime)
                else str(value)
                if isinstance(value, UUID)
                else value
                for key, value in row.items()
            }
            for row in rows
        ]
    result = AccountExportResponse(exported_at=datetime.now(UTC), state_revision=user.state_revision, data=data)
    session.rollback()  # release snapshot/account lock before sending attachment
    return result


def delete_account(session: Session, user: User, current_password: str) -> None:
    valid, _ = _verify_password(current_password, user.password_hash or "")
    if not valid:
        raise DomainError(401, "INVALID_CREDENTIALS", "Current password is incorrect.")
    recipes = select(Recipe.id).where(Recipe.user_id == user.id)
    ingredients = select(Ingredient.id).where(Ingredient.user_id == user.id)
    categories = select(Category.id).where(Category.user_id == user.id)
    events = select(ActivityEvent.id).where(ActivityEvent.user_id == user.id)
    lists = select(GroceryList.id).where(GroceryList.user_id == user.id)
    # Same-owner references are enforced by services, not all database FKs. Do not
    # let SET NULL silently change another account's history in inconsistent data.
    foreign_checks = [
        select(Ingredient.id).where(
            Ingredient.category_id.in_(categories), or_(Ingredient.user_id != user.id, Ingredient.user_id.is_(None))
        ),
        select(RecipeIngredient.id).where(
            RecipeIngredient.ingredient_id.in_(ingredients), RecipeIngredient.recipe_id.not_in(recipes)
        ),
        select(PantryStock.id).where(PantryStock.ingredient_id.in_(ingredients), PantryStock.user_id != user.id),
        select(StockChange.id).where(StockChange.ingredient_id.in_(ingredients), StockChange.event_id.not_in(events)),
        select(ActivityEvent.id).where(
            ActivityEvent.user_id != user.id,
            or_(ActivityEvent.recipe_id.in_(recipes), ActivityEvent.reversal_of.in_(events)),
        ),
        select(BasketItem.id).where(BasketItem.recipe_id.in_(recipes), BasketItem.user_id != user.id),
        select(GroceryListRecipe.id).where(
            GroceryListRecipe.recipe_id.in_(recipes), GroceryListRecipe.grocery_list_id.not_in(lists)
        ),
        select(GroceryListItem.id).where(
            GroceryListItem.ingredient_id.in_(ingredients), GroceryListItem.grocery_list_id.not_in(lists)
        ),
    ]
    if any(session.scalar(query.limit(1)) is not None for query in foreign_checks):
        raise DomainError(
            409, "ACCOUNT_DEPENDENCY_CONFLICT", "Account data needs repair before deletion. No data was deleted."
        )
    for model in (PasswordResetToken, AuthSession, ProcessedMutation, GroceryList, BasketItem):
        session.execute(delete(model).where(model.user_id == user.id))
    session.execute(delete(StockChange).where(StockChange.event_id.in_(events)))
    # Legacy imports can contain reversal cycles. Only the doomed account's rows
    # are changed, in the same irreversible teardown transaction.
    session.execute(update(ActivityEvent).where(ActivityEvent.user_id == user.id).values(reversal_of=None))
    session.execute(delete(ActivityEvent).where(ActivityEvent.user_id == user.id))
    for owned_model in (PantryStock, Recipe, Ingredient, Category):
        session.execute(delete(owned_model).where(owned_model.user_id == user.id))
    session.execute(delete(User).where(User.id == user.id))
    session.commit()
