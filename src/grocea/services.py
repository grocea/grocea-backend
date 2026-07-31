from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from grocea.errors import DomainError
from grocea.models import Category, Ingredient, PantryStock, User
from grocea.normalization import clean_name, normalize_name
from grocea.schemas import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    IngredientCreate,
    IngredientPage,
    IngredientResponse,
    IngredientUpdate,
    MeasurementFamily,
    ProfileResponse,
    ProfileUpdate,
    Scope,
    ScopeFilter,
)


def _visible_to_user(model: type[Category] | type[Ingredient], user: User) -> ColumnElement[bool]:
    return or_(model.user_id.is_(None), model.user_id == user.id)


def _scope(user_id: UUID | None) -> Scope:
    return Scope.GLOBAL if user_id is None else Scope.CUSTOM


def profile_response(user: User) -> ProfileResponse:
    return ProfileResponse.model_validate(user)


def category_response(category: Category) -> CategoryResponse:
    return CategoryResponse(
        id=category.id,
        name=category.name,
        scope=_scope(category.user_id),
        archived_at=category.archived_at,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


def ingredient_response(ingredient: Ingredient, tracked: bool) -> IngredientResponse:
    return IngredientResponse(
        id=ingredient.id,
        name=ingredient.name,
        category_id=ingredient.category_id,
        measurement_family=MeasurementFamily(ingredient.measurement_family),
        scope=_scope(ingredient.user_id),
        tracked_in_pantry=tracked,
        archived_at=ingredient.archived_at,
        created_at=ingredient.created_at,
        updated_at=ingredient.updated_at,
    )


def _commit(session: Session, *, conflict_code: str, conflict_message: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DomainError(409, conflict_code, conflict_message) from exc


def update_profile(session: Session, user: User, payload: ProfileUpdate) -> ProfileResponse:
    if "display_name" in payload.model_fields_set:
        assert payload.display_name is not None
        user.display_name = clean_name(payload.display_name)
    if "preferred_servings" in payload.model_fields_set:
        user.preferred_servings = payload.preferred_servings
    session.flush()
    return profile_response(user)


def _category_query(user: User) -> Select[tuple[Category]]:
    return select(Category).where(_visible_to_user(Category, user))


def get_category_model(session: Session, user: User, category_id: UUID) -> Category:
    category = session.scalar(_category_query(user).where(Category.id == category_id))
    if category is None:
        raise DomainError(404, "CATEGORY_NOT_FOUND", "Category was not found.")
    return category


def _ensure_category_name_available(
    session: Session,
    user: User,
    name: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    statement = _category_query(user).where(Category.normalized_name == normalize_name(name))
    if exclude_id is not None:
        statement = statement.where(Category.id != exclude_id)
    duplicate = session.scalar(statement)
    if duplicate is None:
        return
    if duplicate.archived_at is not None and duplicate.user_id == user.id:
        raise DomainError(
            409,
            "CATEGORY_NAME_ARCHIVED",
            "An archived Category already uses this name.",
            {"category_id": str(duplicate.id)},
        )
    raise DomainError(
        409,
        "CATEGORY_NAME_EXISTS",
        "Category already exists.",
        {"category_id": str(duplicate.id)},
    )


def list_categories(
    session: Session,
    user: User,
    *,
    query: str | None,
    scope: ScopeFilter,
    include_archived: bool,
) -> list[CategoryResponse]:
    statement = _category_query(user)
    if not include_archived:
        statement = statement.where(Category.archived_at.is_(None))
    if query and query.strip():
        statement = statement.where(Category.normalized_name.contains(normalize_name(query)))
    if scope == ScopeFilter.GLOBAL:
        statement = statement.where(Category.user_id.is_(None))
    elif scope == ScopeFilter.CUSTOM:
        statement = statement.where(Category.user_id == user.id)
    statement = statement.order_by(Category.normalized_name, Category.id)
    return [category_response(category) for category in session.scalars(statement)]


def create_category(session: Session, user: User, payload: CategoryCreate) -> CategoryResponse:
    _ensure_category_name_available(session, user, payload.name)
    category = Category(
        id=payload.id or uuid4(),
        user_id=user.id,
        name=clean_name(payload.name),
        normalized_name=normalize_name(payload.name),
    )
    session.add(category)
    _commit(session, conflict_code="CATEGORY_NAME_EXISTS", conflict_message="Category already exists.")
    return category_response(category)


def update_category(
    session: Session,
    user: User,
    category_id: UUID,
    payload: CategoryUpdate,
) -> CategoryResponse:
    category = get_category_model(session, user, category_id)
    if category.user_id is None:
        raise DomainError(403, "GLOBAL_CATEGORY_READ_ONLY", "Global Categories are read-only.")
    if category.archived_at is not None:
        raise DomainError(409, "CATEGORY_ARCHIVED", "Restore Category before editing it.")
    _ensure_category_name_available(session, user, payload.name, exclude_id=category.id)
    category.name = clean_name(payload.name)
    category.normalized_name = normalize_name(payload.name)
    _commit(session, conflict_code="CATEGORY_NAME_EXISTS", conflict_message="Category already exists.")
    return category_response(category)


def archive_category(session: Session, user: User, category_id: UUID) -> CategoryResponse:
    category = get_category_model(session, user, category_id)
    if category.user_id is None:
        raise DomainError(403, "GLOBAL_CATEGORY_READ_ONLY", "Global Categories are read-only.")
    if category.archived_at is not None:
        return category_response(category)
    active_ingredient = session.scalar(
        select(Ingredient.id).where(Ingredient.category_id == category.id, Ingredient.archived_at.is_(None)).limit(1)
    )
    if active_ingredient is not None:
        raise DomainError(409, "CATEGORY_IN_USE", "Reassign active Ingredients before archiving Category.")
    category.archived_at = datetime.now(UTC)
    session.flush()
    return category_response(category)


def restore_category(session: Session, user: User, category_id: UUID) -> CategoryResponse:
    category = get_category_model(session, user, category_id)
    if category.user_id is None:
        raise DomainError(403, "GLOBAL_CATEGORY_READ_ONLY", "Global Categories are read-only.")
    if category.archived_at is None:
        return category_response(category)
    _ensure_category_name_available(session, user, category.name, exclude_id=category.id)
    category.archived_at = None
    _commit(session, conflict_code="CATEGORY_NAME_EXISTS", conflict_message="Category already exists.")
    return category_response(category)


def _ingredient_query(user: User) -> Select[tuple[Ingredient]]:
    return select(Ingredient).where(_visible_to_user(Ingredient, user))


def get_ingredient_model(session: Session, user: User, ingredient_id: UUID) -> Ingredient:
    ingredient = session.scalar(_ingredient_query(user).where(Ingredient.id == ingredient_id))
    if ingredient is None:
        raise DomainError(404, "INGREDIENT_NOT_FOUND", "Ingredient was not found.")
    return ingredient


def _is_tracked(session: Session, user: User, ingredient_id: UUID) -> bool:
    return bool(
        session.scalar(
            select(exists().where(PantryStock.user_id == user.id, PantryStock.ingredient_id == ingredient_id))
        )
    )


def _ensure_ingredient_name_available(
    session: Session,
    user: User,
    name: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    statement = _ingredient_query(user).where(Ingredient.normalized_name == normalize_name(name))
    if exclude_id is not None:
        statement = statement.where(Ingredient.id != exclude_id)
    duplicate = session.scalar(statement)
    if duplicate is None:
        return
    if duplicate.archived_at is not None and duplicate.user_id == user.id:
        raise DomainError(
            409,
            "INGREDIENT_NAME_ARCHIVED",
            "An archived Ingredient already uses this name.",
            {"ingredient_id": str(duplicate.id)},
        )
    raise DomainError(
        409,
        "INGREDIENT_NAME_EXISTS",
        "Ingredient already exists.",
        {"ingredient_id": str(duplicate.id)},
    )


def _active_category(session: Session, user: User, category_id: UUID) -> Category:
    category = get_category_model(session, user, category_id)
    if category.archived_at is not None:
        raise DomainError(409, "CATEGORY_ARCHIVED", "Choose an active Category.")
    return category


def list_ingredients(
    session: Session,
    user: User,
    *,
    query: str | None,
    category_id: UUID | None,
    scope: ScopeFilter,
    include_archived: bool,
    limit: int,
    offset: int,
) -> IngredientPage:
    filters: list[ColumnElement[bool]] = [_visible_to_user(Ingredient, user)]
    if not include_archived:
        filters.append(Ingredient.archived_at.is_(None))
    if query and query.strip():
        filters.append(Ingredient.normalized_name.contains(normalize_name(query)))
    if category_id is not None:
        filters.append(Ingredient.category_id == category_id)
    if scope == ScopeFilter.GLOBAL:
        filters.append(Ingredient.user_id.is_(None))
    elif scope == ScopeFilter.CUSTOM:
        filters.append(Ingredient.user_id == user.id)

    tracked = exists(
        select(PantryStock.id).where(
            PantryStock.user_id == user.id,
            PantryStock.ingredient_id == Ingredient.id,
        )
    ).label("tracked_in_pantry")
    statement = (
        select(Ingredient, tracked)
        .where(*filters)
        .order_by(Ingredient.normalized_name, Ingredient.id)
        .limit(limit)
        .offset(offset)
    )
    total = session.scalar(select(func.count()).select_from(Ingredient).where(*filters)) or 0
    items = [ingredient_response(ingredient, bool(is_tracked)) for ingredient, is_tracked in session.execute(statement)]
    return IngredientPage(items=items, total=total, limit=limit, offset=offset)


def get_ingredient(session: Session, user: User, ingredient_id: UUID) -> IngredientResponse:
    ingredient = get_ingredient_model(session, user, ingredient_id)
    return ingredient_response(ingredient, _is_tracked(session, user, ingredient.id))


def create_ingredient(session: Session, user: User, payload: IngredientCreate) -> IngredientResponse:
    _active_category(session, user, payload.category_id)
    _ensure_ingredient_name_available(session, user, payload.name)
    ingredient = Ingredient(
        id=payload.id or uuid4(),
        user_id=user.id,
        category_id=payload.category_id,
        name=clean_name(payload.name),
        normalized_name=normalize_name(payload.name),
        measurement_family=payload.measurement_family.value,
    )
    session.add(ingredient)
    if payload.track_in_pantry:
        session.add(
            PantryStock(
                id=uuid4(),
                user_id=user.id,
                ingredient_id=ingredient.id,
                quantity=Decimal("0.000"),
            )
        )
    _commit(session, conflict_code="INGREDIENT_NAME_EXISTS", conflict_message="Ingredient already exists.")
    return ingredient_response(ingredient, payload.track_in_pantry)


def update_ingredient(
    session: Session,
    user: User,
    ingredient_id: UUID,
    payload: IngredientUpdate,
) -> IngredientResponse:
    ingredient = get_ingredient_model(session, user, ingredient_id)
    if ingredient.user_id is None:
        raise DomainError(403, "GLOBAL_INGREDIENT_READ_ONLY", "Global Ingredients are read-only.")
    if ingredient.archived_at is not None:
        raise DomainError(409, "INGREDIENT_ARCHIVED", "Restore Ingredient before editing it.")
    if payload.name is not None:
        _ensure_ingredient_name_available(session, user, payload.name, exclude_id=ingredient.id)
        ingredient.name = clean_name(payload.name)
        ingredient.normalized_name = normalize_name(payload.name)
    if payload.category_id is not None:
        _active_category(session, user, payload.category_id)
        ingredient.category_id = payload.category_id
    if payload.measurement_family is not None and payload.measurement_family.value != ingredient.measurement_family:
        if _is_tracked(session, user, ingredient.id):
            raise DomainError(
                409,
                "MEASUREMENT_FAMILY_LOCKED",
                "Measurement family cannot change after Ingredient is tracked.",
            )
        ingredient.measurement_family = payload.measurement_family.value
    _commit(session, conflict_code="INGREDIENT_NAME_EXISTS", conflict_message="Ingredient already exists.")
    return ingredient_response(ingredient, _is_tracked(session, user, ingredient.id))


def archive_ingredient(session: Session, user: User, ingredient_id: UUID) -> IngredientResponse:
    ingredient = get_ingredient_model(session, user, ingredient_id)
    if ingredient.user_id is None:
        raise DomainError(403, "GLOBAL_INGREDIENT_READ_ONLY", "Global Ingredients are read-only.")
    if ingredient.archived_at is not None:
        return ingredient_response(ingredient, _is_tracked(session, user, ingredient.id))
    stock = session.scalar(
        select(PantryStock).where(PantryStock.user_id == user.id, PantryStock.ingredient_id == ingredient.id)
    )
    if stock is not None and stock.quantity != Decimal("0.000"):
        raise DomainError(409, "INGREDIENT_HAS_STOCK", "Set Pantry Stock to zero before archiving Ingredient.")
    ingredient.archived_at = datetime.now(UTC)
    session.flush()
    return ingredient_response(ingredient, stock is not None)


def restore_ingredient(session: Session, user: User, ingredient_id: UUID) -> IngredientResponse:
    ingredient = get_ingredient_model(session, user, ingredient_id)
    if ingredient.user_id is None:
        raise DomainError(403, "GLOBAL_INGREDIENT_READ_ONLY", "Global Ingredients are read-only.")
    if ingredient.archived_at is None:
        return ingredient_response(ingredient, _is_tracked(session, user, ingredient.id))
    category = get_category_model(session, user, ingredient.category_id)
    if category.archived_at is not None:
        raise DomainError(409, "CATEGORY_ARCHIVED", "Restore Ingredient's Category first.")
    _ensure_ingredient_name_available(session, user, ingredient.name, exclude_id=ingredient.id)
    ingredient.archived_at = None
    _commit(session, conflict_code="INGREDIENT_NAME_EXISTS", conflict_message="Ingredient already exists.")
    return ingredient_response(ingredient, _is_tracked(session, user, ingredient.id))
