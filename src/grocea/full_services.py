from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from grocea.errors import DomainError
from grocea.models import (
    ActivityEvent,
    BasketItem,
    Category,
    GroceryList,
    GroceryListItem,
    GroceryListItemSource,
    GroceryListRecipe,
    Ingredient,
    PantryStock,
    Recipe,
    RecipeIngredient,
    RecipeStep,
    StockChange,
    User,
)
from grocea.normalization import normalize_name
from grocea.schemas import (
    ActivityResponse,
    BasketItemResponse,
    BasketItemUpsert,
    BasketResponse,
    CookRecipeCreate,
    GroceryListComplete,
    GroceryListCreate,
    GroceryListItemCreate,
    GroceryListItemResponse,
    GroceryListItemSourceResponse,
    GroceryListItemUpdate,
    GroceryListRecipeResponse,
    GroceryListResponse,
    GroceryListStatus,
    GroceryListUpdate,
    ImportConflict,
    LocalImportRequest,
    LocalImportResponse,
    MeasurementFamily,
    PantryStockResponse,
    RecipeCreate,
    RecipeIngredientResponse,
    RecipeResponse,
    RecipeStatus,
    RecipeUpdate,
    ReverseActivityCreate,
    Scope,
    StateResponse,
    StockChangeResponse,
    StockOperation,
    StockOperationCreate,
    Unit,
)
from grocea.services import category_response, ingredient_response, profile_response

THREE_PLACES = Decimal("0.001")
UNIT_FACTORS = {
    Unit.MG: Decimal("0.001"),
    Unit.G: Decimal("1"),
    Unit.KG: Decimal("1000"),
    Unit.ML: Decimal("1"),
    Unit.L: Decimal("1000"),
    Unit.ITEM: Decimal("1"),
}
FAMILY_UNITS = {
    "mass": {Unit.MG, Unit.G, Unit.KG},
    "volume": {Unit.ML, Unit.L},
    "count": {Unit.ITEM},
}


def _scope(user_id: UUID | None) -> Scope:
    return Scope.GLOBAL if user_id is None else Scope.CUSTOM


def _parse_quantity(raw: str, unit: Unit) -> Decimal | None:
    try:
        value = Decimal(raw.strip()) * UNIT_FACTORS[unit]
    except (InvalidOperation, ValueError):
        return None
    value = value.quantize(THREE_PLACES, rounding=ROUND_HALF_UP)
    return value if value > 0 else None


def _imported_minor_quantity(item: dict[str, object], key: str) -> Decimal | None:
    value = item.get(key)
    if not isinstance(value, str):
        return None
    return (Decimal(value) / Decimal(1000)).quantize(THREE_PLACES, rounding=ROUND_HALF_UP)


def pantry_response(stock: PantryStock) -> PantryStockResponse:
    return PantryStockResponse.model_validate(stock)


def recipe_response(session: Session, recipe: Recipe) -> RecipeResponse:
    ingredients = session.scalars(
        select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id).order_by(RecipeIngredient.position)
    ).all()
    steps = session.scalars(
        select(RecipeStep).where(RecipeStep.recipe_id == recipe.id).order_by(RecipeStep.position)
    ).all()
    return RecipeResponse(
        id=recipe.id,
        status=RecipeStatus(recipe.status),
        scope=_scope(recipe.user_id),
        name=recipe.name,
        description=recipe.description,
        base_servings=recipe.base_servings,
        ingredients=[
            RecipeIngredientResponse(
                ingredient_id=item.ingredient_id,
                quantity=item.quantity,
                quantity_input=item.quantity_input,
                unit=Unit(item.unit),
            )
            for item in ingredients
        ],
        steps=[step.body for step in steps],
        created_at=recipe.created_at,
        updated_at=recipe.updated_at,
    )


def activity_response(session: Session, event: ActivityEvent) -> ActivityResponse:
    changes = session.scalars(
        select(StockChange).where(StockChange.event_id == event.id).order_by(StockChange.id)
    ).all()
    return ActivityResponse(
        id=event.id,
        type=event.event_type,  # type: ignore[arg-type]
        title=event.title,
        detail=event.detail,
        occurred_at=event.occurred_at,
        recipe_id=event.recipe_id,
        servings=event.servings,
        changes=[
            StockChangeResponse(
                ingredient_id=change.ingredient_id,
                before=change.before,
                delta=change.delta,
                after=change.after,
            )
            for change in changes
        ],
        reversed_at=event.reversed_at,
        reversal_of=event.reversal_of,
    )


def list_pantry_stocks(session: Session, user: User) -> list[PantryStockResponse]:
    rows = session.scalars(
        select(PantryStock).where(PantryStock.user_id == user.id).order_by(PantryStock.ingredient_id)
    )
    return [pantry_response(row) for row in rows]


def list_recipes(session: Session, user: User) -> list[RecipeResponse]:
    rows = session.scalars(
        select(Recipe)
        .where(or_(Recipe.user_id.is_(None), Recipe.user_id == user.id), Recipe.archived_at.is_(None))
        .order_by(Recipe.updated_at.desc(), Recipe.id)
    )
    return [recipe_response(session, row) for row in rows]


def list_activity(session: Session, user: User) -> list[ActivityResponse]:
    rows = session.scalars(
        select(ActivityEvent)
        .where(ActivityEvent.user_id == user.id)
        .order_by(ActivityEvent.occurred_at.desc(), ActivityEvent.id.desc())
    )
    return [activity_response(session, row) for row in rows]


def basket_response(session: Session, user: User) -> BasketResponse:
    rows = session.execute(
        select(BasketItem, Recipe)
        .join(Recipe, Recipe.id == BasketItem.recipe_id)
        .where(BasketItem.user_id == user.id)
        .order_by(BasketItem.position)
    ).all()
    return BasketResponse(
        items=[
            BasketItemResponse(
                recipe_id=recipe.id,
                recipe_name=recipe.name,
                servings=item.servings,
                base_servings=recipe.base_servings,
                valid=recipe.status == RecipeStatus.PUBLISHED and recipe.archived_at is None,
                error=(
                    None
                    if recipe.status == RecipeStatus.PUBLISHED and recipe.archived_at is None
                    else "Recipe is no longer published."
                ),
            )
            for item, recipe in rows
        ]
    )


def upsert_basket_item(
    session: Session,
    user: User,
    recipe_id: UUID,
    payload: BasketItemUpsert,
) -> BasketResponse:
    recipe = get_recipe_model(session, user, recipe_id)
    if recipe.status != RecipeStatus.PUBLISHED:
        raise DomainError(409, "RECIPE_NOT_PUBLISHED", "Only Published Recipes can be added to Basket.")
    item = session.scalar(select(BasketItem).where(BasketItem.user_id == user.id, BasketItem.recipe_id == recipe_id))
    if item is None:
        positions = session.scalars(select(BasketItem.position).where(BasketItem.user_id == user.id)).all()
        session.add(
            BasketItem(
                user_id=user.id,
                recipe_id=recipe_id,
                servings=payload.servings,
                position=max(positions, default=-1) + 1,
            )
        )
    else:
        item.servings = payload.servings
    session.flush()
    return basket_response(session, user)


def remove_basket_item(session: Session, user: User, recipe_id: UUID) -> BasketResponse:
    session.execute(delete(BasketItem).where(BasketItem.user_id == user.id, BasketItem.recipe_id == recipe_id))
    session.flush()
    return basket_response(session, user)


def clear_basket(session: Session, user: User) -> BasketResponse:
    session.execute(delete(BasketItem).where(BasketItem.user_id == user.id))
    session.flush()
    return basket_response(session, user)


def grocery_list_response(session: Session, grocery_list: GroceryList) -> GroceryListResponse:
    recipes = session.scalars(
        select(GroceryListRecipe)
        .where(GroceryListRecipe.grocery_list_id == grocery_list.id)
        .order_by(GroceryListRecipe.position)
    ).all()
    items = session.scalars(
        select(GroceryListItem)
        .where(GroceryListItem.grocery_list_id == grocery_list.id)
        .order_by(GroceryListItem.category_name, GroceryListItem.checked, GroceryListItem.label, GroceryListItem.id)
    ).all()
    item_responses: list[GroceryListItemResponse] = []
    for item in items:
        sources = session.scalars(
            select(GroceryListItemSource)
            .where(GroceryListItemSource.grocery_list_item_id == item.id)
            .order_by(GroceryListItemSource.recipe_name, GroceryListItemSource.id)
        ).all()
        item_responses.append(
            GroceryListItemResponse(
                id=item.id,
                ingredient_id=item.ingredient_id,
                label=item.label,
                category_name=item.category_name,
                measurement_family=(
                    MeasurementFamily(item.measurement_family) if item.measurement_family is not None else None
                ),
                quantity=item.quantity,
                unit=item.unit,
                checked=item.checked,
                origin=item.origin,  # type: ignore[arg-type]
                edited=item.edited,
                original_required=item.original_required,
                original_pantry=item.original_pantry,
                original_quantity=item.original_quantity,
                sources=[
                    GroceryListItemSourceResponse(
                        recipe_id=source.recipe_snapshot_id,
                        recipe_name=source.recipe_name,
                        servings=source.servings,
                        quantity=source.quantity,
                        unit=Unit(source.unit),
                    )
                    for source in sources
                ],
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        )
    return GroceryListResponse(
        id=grocery_list.id,
        title=grocery_list.title,
        status=GroceryListStatus(grocery_list.status),
        recipes=[
            GroceryListRecipeResponse(
                recipe_id=recipe.recipe_snapshot_id,
                recipe_name=recipe.recipe_name,
                servings=recipe.servings,
                base_servings=recipe.base_servings,
            )
            for recipe in recipes
        ],
        items=item_responses,
        created_at=grocery_list.created_at,
        updated_at=grocery_list.updated_at,
        completed_at=grocery_list.completed_at,
    )


def list_grocery_lists(session: Session, user: User) -> list[GroceryListResponse]:
    rows = session.scalars(
        select(GroceryList)
        .where(GroceryList.user_id == user.id)
        .order_by(GroceryList.created_at.desc(), GroceryList.id.desc())
    ).all()
    return [grocery_list_response(session, row) for row in rows]


def create_grocery_list_from_basket(
    session: Session,
    user: User,
    payload: GroceryListCreate,
) -> GroceryListResponse:
    if session.get(GroceryList, payload.id) is not None:
        raise DomainError(409, "GROCERY_LIST_ID_EXISTS", "Grocery List ID already exists.")
    active = session.scalar(
        select(GroceryList).where(GroceryList.user_id == user.id, GroceryList.status == GroceryListStatus.ACTIVE)
    )
    if active is not None:
        raise DomainError(409, "ACTIVE_GROCERY_LIST_EXISTS", "Complete or delete the active Grocery List first.")
    basket_rows = session.execute(
        select(BasketItem, Recipe)
        .join(Recipe, Recipe.id == BasketItem.recipe_id)
        .where(BasketItem.user_id == user.id)
        .order_by(BasketItem.position)
    ).all()
    if not basket_rows:
        raise DomainError(409, "BASKET_EMPTY", "Add at least one Recipe to Basket first.")
    invalid = [
        recipe.name for _, recipe in basket_rows if recipe.status != RecipeStatus.PUBLISHED or recipe.archived_at
    ]
    if invalid:
        raise DomainError(
            409,
            "BASKET_RECIPE_INVALID",
            "Basket contains Recipes that are no longer published.",
            {"recipes": invalid},
        )

    if payload.recipe_basis:
        expected_recipes = {item.recipe_id: item for item in payload.recipe_basis}
        if set(expected_recipes) != {recipe.id for _, recipe in basket_rows}:
            raise DomainError(409, "GROCERY_CALCULATION_STALE", "Basket Recipes changed. Review fresh totals.")
        for _, recipe in basket_rows:
            basis = expected_recipes[recipe.id]
            requirements = session.scalars(
                select(RecipeIngredient)
                .where(RecipeIngredient.recipe_id == recipe.id)
                .order_by(RecipeIngredient.position)
            ).all()
            actual = {item.ingredient_id: item.quantity for item in requirements}
            expected = {
                item.ingredient_id: item.quantity.quantize(THREE_PLACES, rounding=ROUND_HALF_UP)
                for item in basis.ingredients
            }
            if basis.base_servings != recipe.base_servings or actual != expected:
                raise DomainError(409, "GROCERY_CALCULATION_STALE", "Recipe requirements changed. Review fresh totals.")

    first_name = basket_rows[0][1].name
    default_title = f"Groceries — {first_name}" + (f" + {len(basket_rows) - 1}" if len(basket_rows) > 1 else "")
    grocery_list = GroceryList(
        id=payload.id,
        user_id=user.id,
        title=payload.title or default_title,
        status=GroceryListStatus.ACTIVE,
    )
    session.add(grocery_list)
    session.flush()

    aggregates: dict[UUID, dict[str, object]] = {}
    for position, (basket_item, recipe) in enumerate(basket_rows):
        session.add(
            GroceryListRecipe(
                grocery_list_id=grocery_list.id,
                recipe_id=recipe.id,
                recipe_snapshot_id=recipe.id,
                recipe_name=recipe.name,
                position=position,
                servings=basket_item.servings,
                base_servings=recipe.base_servings,
            )
        )
        requirements = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id).order_by(RecipeIngredient.position)
        ).all()
        for requirement in requirements:
            if requirement.quantity is None:
                raise DomainError(409, "BASKET_RECIPE_INVALID", "Published Recipe has an invalid quantity.")
            contribution = (
                requirement.quantity * Decimal(basket_item.servings) / Decimal(recipe.base_servings)
            ).quantize(THREE_PLACES, rounding=ROUND_HALF_UP)
            aggregate = aggregates.setdefault(
                requirement.ingredient_id,
                {"required": Decimal("0.000"), "sources": []},
            )
            aggregate["required"] = aggregate["required"] + contribution  # type: ignore[operator]
            sources = aggregate["sources"]
            assert isinstance(sources, list)
            sources.append((recipe, basket_item.servings, contribution))

    pantry = {
        row.ingredient_id: row.quantity
        for row in session.scalars(select(PantryStock).where(PantryStock.user_id == user.id))
    }
    if payload.pantry_basis:
        expected_pantry = {
            item.ingredient_id: item.quantity.quantize(THREE_PLACES, rounding=ROUND_HALF_UP)
            for item in payload.pantry_basis
        }
        actual_pantry = {ingredient_id: pantry.get(ingredient_id, Decimal("0.000")) for ingredient_id in aggregates}
        if expected_pantry != actual_pantry:
            raise DomainError(409, "GROCERY_CALCULATION_STALE", "Pantry Stock changed. Review fresh totals.")
    generated_item_ids = {item.ingredient_id: item.id for item in payload.generated_item_ids}
    for ingredient_id, aggregate in aggregates.items():
        ingredient = session.get(Ingredient, ingredient_id)
        assert ingredient is not None
        category = session.get(Category, ingredient.category_id)
        assert category is not None
        required = aggregate["required"]
        assert isinstance(required, Decimal)
        pantry_quantity = pantry.get(ingredient_id, Decimal("0.000"))
        purchase = (required - pantry_quantity).quantize(THREE_PLACES, rounding=ROUND_HALF_UP)
        if purchase <= 0:
            continue
        canonical_unit = {"mass": "g", "volume": "ml", "count": "item"}[ingredient.measurement_family]
        grocery_item = GroceryListItem(
            id=generated_item_ids.get(ingredient.id, uuid4()),
            grocery_list_id=grocery_list.id,
            ingredient_id=ingredient.id,
            original_ingredient_id=ingredient.id,
            label=ingredient.name,
            category_name=category.name,
            measurement_family=ingredient.measurement_family,
            quantity=purchase,
            unit=canonical_unit,
            checked=False,
            origin="generated",
            edited=False,
            original_required=required,
            original_pantry=pantry_quantity,
            original_quantity=purchase,
        )
        session.add(grocery_item)
        session.flush()
        sources = aggregate["sources"]
        assert isinstance(sources, list)
        for recipe, servings, contribution in sources:
            session.add(
                GroceryListItemSource(
                    grocery_list_item_id=grocery_item.id,
                    recipe_snapshot_id=recipe.id,
                    recipe_name=recipe.name,
                    servings=servings,
                    quantity=contribution,
                    unit=canonical_unit,
                )
            )

    session.flush()
    has_items = session.scalar(
        select(GroceryListItem.id).where(GroceryListItem.grocery_list_id == grocery_list.id).limit(1)
    )
    if has_items is None:
        grocery_list.status = GroceryListStatus.COMPLETED
        grocery_list.completed_at = datetime.now(UTC)
    session.execute(delete(BasketItem).where(BasketItem.user_id == user.id))
    session.flush()
    return grocery_list_response(session, grocery_list)


def get_grocery_list_model(session: Session, user: User, grocery_list_id: UUID) -> GroceryList:
    grocery_list = session.scalar(
        select(GroceryList).where(GroceryList.id == grocery_list_id, GroceryList.user_id == user.id)
    )
    if grocery_list is None:
        raise DomainError(404, "GROCERY_LIST_NOT_FOUND", "Grocery List was not found.")
    return grocery_list


def _require_active_grocery_list(session: Session, user: User, grocery_list_id: UUID) -> GroceryList:
    grocery_list = get_grocery_list_model(session, user, grocery_list_id)
    if grocery_list.status != GroceryListStatus.ACTIVE:
        raise DomainError(409, "GROCERY_LIST_COMPLETED", "Completed Grocery Lists are read-only.")
    return grocery_list


def _grocery_item_values(
    session: Session,
    user: User,
    *,
    ingredient_id: UUID | None,
    label: str,
    quantity: Decimal | None,
    unit: str | None,
) -> tuple[UUID | None, str, str, str | None, Decimal | None, str | None]:
    if ingredient_id is None:
        custom_quantity = quantity.quantize(THREE_PLACES, rounding=ROUND_HALF_UP) if quantity is not None else None
        return None, label.strip(), "Other", None, custom_quantity, unit.strip() if unit is not None else None
    ingredient = session.scalar(
        select(Ingredient).where(
            Ingredient.id == ingredient_id,
            or_(Ingredient.user_id.is_(None), Ingredient.user_id == user.id),
            Ingredient.archived_at.is_(None),
        )
    )
    if ingredient is None:
        raise DomainError(404, "INGREDIENT_NOT_FOUND", "Grocery List Ingredient was not found.")
    category = session.get(Category, ingredient.category_id)
    assert category is not None
    canonical_unit = {"mass": Unit.G, "volume": Unit.ML, "count": Unit.ITEM}[ingredient.measurement_family]
    normalized_quantity: Decimal | None = None
    if quantity is not None and unit is not None:
        try:
            parsed_unit = Unit(unit.strip())
        except ValueError as exc:
            raise DomainError(422, "UNIT_INVALID", "Catalog Ingredient requires a supported metric unit.") from exc
        if parsed_unit not in FAMILY_UNITS[ingredient.measurement_family]:
            raise DomainError(422, "UNIT_FAMILY_MISMATCH", "Grocery unit does not match Ingredient family.")
        normalized_quantity = (quantity * UNIT_FACTORS[parsed_unit]).quantize(THREE_PLACES, rounding=ROUND_HALF_UP)
    return (
        ingredient.id,
        ingredient.name,
        category.name,
        ingredient.measurement_family,
        normalized_quantity,
        canonical_unit.value if normalized_quantity is not None else None,
    )


def create_grocery_list_item(
    session: Session,
    user: User,
    grocery_list_id: UUID,
    payload: GroceryListItemCreate,
) -> GroceryListResponse:
    grocery_list = _require_active_grocery_list(session, user, grocery_list_id)
    if session.get(GroceryListItem, payload.id) is not None:
        raise DomainError(409, "GROCERY_LIST_ITEM_ID_EXISTS", "Grocery List Item ID already exists.")
    ingredient_id, label, category, family, quantity, unit = _grocery_item_values(
        session,
        user,
        ingredient_id=payload.ingredient_id,
        label=payload.label,
        quantity=payload.quantity,
        unit=payload.unit,
    )
    session.add(
        GroceryListItem(
            id=payload.id,
            grocery_list_id=grocery_list.id,
            ingredient_id=ingredient_id,
            label=label,
            category_name=category,
            measurement_family=family,
            quantity=quantity,
            unit=unit,
            checked=False,
            origin="manual",
            edited=False,
        )
    )
    session.flush()
    return grocery_list_response(session, grocery_list)


def update_grocery_list_item(
    session: Session,
    user: User,
    grocery_list_id: UUID,
    grocery_item_id: UUID,
    payload: GroceryListItemUpdate,
) -> GroceryListResponse:
    grocery_list = _require_active_grocery_list(session, user, grocery_list_id)
    item = session.scalar(
        select(GroceryListItem).where(
            GroceryListItem.id == grocery_item_id,
            GroceryListItem.grocery_list_id == grocery_list.id,
        )
    )
    if item is None:
        raise DomainError(404, "GROCERY_LIST_ITEM_NOT_FOUND", "Grocery List Item was not found.")
    ingredient_id, label, category, family, quantity, unit = _grocery_item_values(
        session,
        user,
        ingredient_id=payload.ingredient_id,
        label=payload.label,
        quantity=payload.quantity,
        unit=payload.unit,
    )
    changed = (
        item.ingredient_id != ingredient_id or item.label != label or item.quantity != quantity or item.unit != unit
    )
    item.ingredient_id = ingredient_id
    item.label = label
    item.category_name = category
    item.measurement_family = family
    item.quantity = quantity
    item.unit = unit
    item.checked = payload.checked
    item.edited = item.edited or changed
    session.flush()
    return grocery_list_response(session, grocery_list)


def complete_grocery_list(
    session: Session,
    user: User,
    grocery_list_id: UUID,
    payload: GroceryListComplete,
) -> GroceryListResponse:
    grocery_list = _require_active_grocery_list(session, user, grocery_list_id)
    if len(set(payload.pantry_item_ids)) != len(payload.pantry_item_ids):
        raise DomainError(422, "DUPLICATE_PANTRY_ITEM", "Pantry update Items must be unique.")
    selected = session.scalars(
        select(GroceryListItem).where(
            GroceryListItem.grocery_list_id == grocery_list.id,
            GroceryListItem.id.in_(payload.pantry_item_ids),
        )
    ).all()
    if len(selected) != len(payload.pantry_item_ids):
        raise DomainError(422, "PANTRY_ITEM_INVALID", "Selected pantry update Item was not found in this list.")
    if any(not item.checked or item.ingredient_id is None or item.quantity is None for item in selected):
        raise DomainError(
            422,
            "PANTRY_ITEM_INELIGIBLE",
            "Pantry updates require checked catalog Items with quantities.",
        )
    pantry_additions: dict[UUID, Decimal] = {}
    for item in selected:
        assert item.ingredient_id is not None and item.quantity is not None
        pantry_additions[item.ingredient_id] = (
            pantry_additions.get(item.ingredient_id, Decimal("0.000")) + item.quantity
        )

    event: ActivityEvent | None = None
    if selected:
        if session.get(ActivityEvent, payload.event_id) is not None:
            raise DomainError(409, "ACTIVITY_ID_EXISTS", "Activity Event ID already exists.")
        event = ActivityEvent(
            id=payload.event_id,
            user_id=user.id,
            event_type="manual",
            title="Groceries added to pantry",
            detail=f"{len(selected)} purchased item{'s' if len(selected) != 1 else ''}",
        )
        session.add(event)
        session.flush()
    for ingredient_id, quantity in pantry_additions.items():
        assert event is not None
        stock = session.scalar(
            select(PantryStock)
            .where(PantryStock.user_id == user.id, PantryStock.ingredient_id == ingredient_id)
            .with_for_update()
        )
        if stock is None:
            stock = PantryStock(user_id=user.id, ingredient_id=ingredient_id, quantity=Decimal("0.000"))
            session.add(stock)
            session.flush()
        before = stock.quantity
        stock.quantity = (before + quantity).quantize(THREE_PLACES, rounding=ROUND_HALF_UP)
        session.add(
            StockChange(
                event_id=event.id,
                ingredient_id=ingredient_id,
                before=before,
                delta=quantity,
                after=stock.quantity,
            )
        )
    grocery_list.status = GroceryListStatus.COMPLETED
    grocery_list.completed_at = datetime.now(UTC)
    session.flush()
    return grocery_list_response(session, grocery_list)


def update_grocery_list(
    session: Session,
    user: User,
    grocery_list_id: UUID,
    payload: GroceryListUpdate,
) -> GroceryListResponse:
    grocery_list = _require_active_grocery_list(session, user, grocery_list_id)
    grocery_list.title = payload.title
    session.flush()
    return grocery_list_response(session, grocery_list)


def remove_grocery_list_item(
    session: Session,
    user: User,
    grocery_list_id: UUID,
    grocery_item_id: UUID,
) -> GroceryListResponse:
    grocery_list = _require_active_grocery_list(session, user, grocery_list_id)
    item = session.scalar(
        select(GroceryListItem).where(
            GroceryListItem.id == grocery_item_id,
            GroceryListItem.grocery_list_id == grocery_list.id,
        )
    )
    if item is None:
        raise DomainError(404, "GROCERY_LIST_ITEM_NOT_FOUND", "Grocery List Item was not found.")
    session.delete(item)
    session.flush()
    return grocery_list_response(session, grocery_list)


def _restore_grocery_list_recipes_to_basket(
    session: Session,
    user: User,
    grocery_list: GroceryList,
) -> None:
    sources = session.scalars(
        select(GroceryListRecipe)
        .where(GroceryListRecipe.grocery_list_id == grocery_list.id)
        .order_by(GroceryListRecipe.position)
    ).all()
    recipes: dict[UUID, Recipe] = {}
    invalid: list[str] = []
    for source in sources:
        recipe = session.scalar(
            select(Recipe).where(
                Recipe.id == source.recipe_snapshot_id,
                or_(Recipe.user_id.is_(None), Recipe.user_id == user.id),
                Recipe.status == RecipeStatus.PUBLISHED,
                Recipe.archived_at.is_(None),
            )
        )
        if recipe is None:
            invalid.append(source.recipe_name)
        else:
            recipes[source.recipe_snapshot_id] = recipe
    if invalid:
        raise DomainError(
            409,
            "GROCERY_LIST_RECIPE_INVALID",
            "Source Recipes are no longer available.",
            {"recipes": invalid},
        )
    existing_rows = session.scalars(select(BasketItem).where(BasketItem.user_id == user.id)).all()
    existing = {item.recipe_id for item in existing_rows}
    next_position = max((item.position for item in existing_rows), default=-1) + 1
    for source in sources:
        if source.recipe_snapshot_id not in existing:
            session.add(
                BasketItem(
                    user_id=user.id,
                    recipe_id=recipes[source.recipe_snapshot_id].id,
                    servings=source.servings,
                    position=next_position,
                )
            )
            existing.add(source.recipe_snapshot_id)
            next_position += 1
    session.flush()


def reuse_grocery_list_recipes(
    session: Session,
    user: User,
    grocery_list_id: UUID,
) -> BasketResponse:
    grocery_list = get_grocery_list_model(session, user, grocery_list_id)
    if grocery_list.status != GroceryListStatus.COMPLETED:
        raise DomainError(409, "GROCERY_LIST_ACTIVE", "Only completed Grocery Lists can be reused.")
    _restore_grocery_list_recipes_to_basket(session, user, grocery_list)
    return basket_response(session, user)


def delete_grocery_list(
    session: Session,
    user: User,
    grocery_list_id: UUID,
    *,
    restore_recipes: bool,
) -> BasketResponse:
    grocery_list = get_grocery_list_model(session, user, grocery_list_id)
    if restore_recipes:
        _restore_grocery_list_recipes_to_basket(session, user, grocery_list)
    session.delete(grocery_list)
    session.flush()
    return basket_response(session, user)


def state_response(session: Session, user: User) -> StateResponse:
    categories = session.scalars(
        select(Category)
        .where(or_(Category.user_id.is_(None), Category.user_id == user.id), Category.archived_at.is_(None))
        .order_by(Category.normalized_name)
    )
    ingredients = session.scalars(
        select(Ingredient)
        .where(or_(Ingredient.user_id.is_(None), Ingredient.user_id == user.id), Ingredient.archived_at.is_(None))
        .order_by(Ingredient.normalized_name)
    )
    stocks = {
        stock.ingredient_id for stock in session.scalars(select(PantryStock).where(PantryStock.user_id == user.id))
    }
    return StateResponse(
        revision=user.state_revision,
        profile=profile_response(user),
        categories=[category_response(category) for category in categories],
        ingredients=[ingredient_response(ingredient, ingredient.id in stocks) for ingredient in ingredients],
        pantry_stocks=list_pantry_stocks(session, user),
        recipes=list_recipes(session, user),
        activity=list_activity(session, user),
        basket=basket_response(session, user),
        grocery_lists=list_grocery_lists(session, user),
    )


def get_recipe_model(session: Session, user: User, recipe_id: UUID, *, draft_only: bool = False) -> Recipe:
    recipe = session.scalar(
        select(Recipe).where(
            Recipe.id == recipe_id,
            or_(Recipe.user_id.is_(None), Recipe.user_id == user.id),
            Recipe.archived_at.is_(None),
        )
    )
    if recipe is None:
        raise DomainError(404, "RECIPE_NOT_FOUND", "Recipe was not found.")
    if draft_only and (recipe.user_id != user.id or recipe.status != RecipeStatus.DRAFT):
        raise DomainError(409, "RECIPE_NOT_EDITABLE", "Only custom Draft Recipes can be edited.")
    return recipe


def _replace_recipe_parts(session: Session, user: User, recipe: Recipe, payload: RecipeCreate | RecipeUpdate) -> None:
    if len({item.ingredient_id for item in payload.ingredients}) != len(payload.ingredients):
        raise DomainError(422, "DUPLICATE_RECIPE_INGREDIENT", "Recipe Ingredients must be unique.")
    session.execute(delete(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id))
    session.execute(delete(RecipeStep).where(RecipeStep.recipe_id == recipe.id))
    for position, item in enumerate(payload.ingredients):
        ingredient = session.scalar(
            select(Ingredient).where(
                Ingredient.id == item.ingredient_id,
                or_(Ingredient.user_id.is_(None), Ingredient.user_id == user.id),
                Ingredient.archived_at.is_(None),
            )
        )
        if ingredient is None:
            raise DomainError(404, "INGREDIENT_NOT_FOUND", "Recipe Ingredient was not found.")
        if item.unit not in FAMILY_UNITS[ingredient.measurement_family]:
            raise DomainError(422, "UNIT_FAMILY_MISMATCH", "Recipe unit does not match Ingredient family.")
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                ingredient_id=item.ingredient_id,
                position=position,
                quantity=_parse_quantity(item.quantity, item.unit),
                quantity_input=item.quantity,
                unit=item.unit.value,
            )
        )
    for position, body in enumerate(payload.steps):
        session.add(RecipeStep(recipe_id=recipe.id, position=position, body=body))


def create_recipe(session: Session, user: User, payload: RecipeCreate) -> RecipeResponse:
    if session.get(Recipe, payload.id) is not None:
        raise DomainError(409, "RECIPE_ID_EXISTS", "Recipe ID already exists.")
    recipe = Recipe(
        id=payload.id,
        user_id=user.id,
        status=RecipeStatus.DRAFT,
        name=payload.name,
        description=payload.description,
        base_servings=payload.base_servings,
    )
    session.add(recipe)
    session.flush()
    _replace_recipe_parts(session, user, recipe, payload)
    session.flush()
    return recipe_response(session, recipe)


def update_recipe(session: Session, user: User, recipe_id: UUID, payload: RecipeUpdate) -> RecipeResponse:
    recipe = get_recipe_model(session, user, recipe_id, draft_only=True)
    recipe.name = payload.name
    recipe.description = payload.description
    recipe.base_servings = payload.base_servings
    _replace_recipe_parts(session, user, recipe, payload)
    session.flush()
    return recipe_response(session, recipe)


def delete_recipe(session: Session, user: User, recipe_id: UUID) -> RecipeResponse:
    recipe = get_recipe_model(session, user, recipe_id, draft_only=True)
    response = recipe_response(session, recipe)
    session.delete(recipe)
    session.flush()
    return response


def publish_recipe(session: Session, user: User, recipe_id: UUID) -> RecipeResponse:
    recipe = get_recipe_model(session, user, recipe_id, draft_only=True)
    response = recipe_response(session, recipe)
    if not recipe.name.strip() or not response.ingredients or not any(step.strip() for step in response.steps):
        raise DomainError(422, "RECIPE_INCOMPLETE", "Recipe needs a name, Ingredient, and preparation step.")
    if any(item.quantity is None or item.quantity <= 0 for item in response.ingredients):
        raise DomainError(422, "RECIPE_QUANTITY_INVALID", "Every Recipe Ingredient needs a positive quantity.")
    recipe.name = recipe.name.strip()
    recipe.description = recipe.description.strip()
    recipe.status = RecipeStatus.PUBLISHED
    session.flush()
    return recipe_response(session, recipe)


def adjust_stock(
    session: Session,
    user: User,
    ingredient_id: UUID,
    payload: StockOperationCreate,
) -> ActivityResponse:
    ingredient = session.scalar(
        select(Ingredient).where(
            Ingredient.id == ingredient_id,
            or_(Ingredient.user_id.is_(None), Ingredient.user_id == user.id),
            Ingredient.archived_at.is_(None),
        )
    )
    if ingredient is None:
        raise DomainError(404, "INGREDIENT_NOT_FOUND", "Ingredient was not found.")
    stock = session.scalar(
        select(PantryStock)
        .where(PantryStock.user_id == user.id, PantryStock.ingredient_id == ingredient_id)
        .with_for_update()
    )
    if stock is None:
        stock = PantryStock(user_id=user.id, ingredient_id=ingredient_id, quantity=Decimal("0.000"))
        session.add(stock)
        session.flush()
    before = stock.quantity
    amount = payload.amount.quantize(THREE_PLACES)
    after = (
        amount
        if payload.operation == StockOperation.SET
        else (before + amount if payload.operation == StockOperation.ADD else before - amount)
    )
    delta = after - before
    stock.quantity = after
    verb = {"add": "Added", "set": "Set", "remove": "Removed"}[payload.operation.value]
    event = ActivityEvent(
        id=payload.event_id,
        user_id=user.id,
        event_type="manual",
        title=f"{verb} {ingredient.name}",
        detail=payload.reason.strip() or "Manual adjustment",
    )
    session.add(event)
    session.flush()
    session.add(StockChange(event_id=event.id, ingredient_id=ingredient_id, before=before, delta=delta, after=after))
    session.flush()
    return activity_response(session, event)


def cook_recipe(session: Session, user: User, recipe_id: UUID, payload: CookRecipeCreate) -> ActivityResponse:
    recipe = get_recipe_model(session, user, recipe_id)
    if recipe.status != RecipeStatus.PUBLISHED:
        raise DomainError(409, "RECIPE_NOT_PUBLISHED", "Only Published Recipes can be cooked.")
    ingredients = session.scalars(
        select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id).order_by(RecipeIngredient.position)
    ).all()
    event = ActivityEvent(
        id=payload.event_id,
        user_id=user.id,
        event_type="cooking",
        title=f"Cooked {recipe.name}",
        detail=f"{payload.servings} serving{'s' if payload.servings != 1 else ''} · {len(ingredients)} stock changes",
        recipe_id=recipe.id,
        servings=payload.servings,
    )
    session.add(event)
    session.flush()
    for item in ingredients:
        assert item.quantity is not None
        stock = session.scalar(
            select(PantryStock)
            .where(PantryStock.user_id == user.id, PantryStock.ingredient_id == item.ingredient_id)
            .with_for_update()
        )
        if stock is None:
            stock = PantryStock(user_id=user.id, ingredient_id=item.ingredient_id, quantity=Decimal("0.000"))
            session.add(stock)
            session.flush()
        before = stock.quantity
        needed = (item.quantity * Decimal(payload.servings) / Decimal(recipe.base_servings)).quantize(
            THREE_PLACES, rounding=ROUND_HALF_UP
        )
        stock.quantity = before - needed
        session.add(
            StockChange(
                event_id=event.id,
                ingredient_id=item.ingredient_id,
                before=before,
                delta=-needed,
                after=stock.quantity,
            )
        )
    session.flush()
    return activity_response(session, event)


def reverse_activity(
    session: Session,
    user: User,
    original_id: UUID,
    payload: ReverseActivityCreate,
) -> ActivityResponse:
    original = session.scalar(
        select(ActivityEvent).where(ActivityEvent.id == original_id, ActivityEvent.user_id == user.id).with_for_update()
    )
    if original is None:
        raise DomainError(404, "ACTIVITY_NOT_FOUND", "Activity Event was not found.")
    if original.event_type != "cooking":
        raise DomainError(409, "ACTIVITY_NOT_REVERSIBLE", "Only Cooking Events can be reversed.")
    if original.reversed_at is not None:
        raise DomainError(409, "ACTIVITY_ALREADY_REVERSED", "Cooking Event was already reversed.")
    occurred_at = datetime.now(UTC)
    reversal = ActivityEvent(
        id=payload.event_id,
        user_id=user.id,
        event_type="reversal",
        title="Cooking undone",
        detail=f"{original.title.removeprefix('Cooked ')} · Stock restored",
        occurred_at=occurred_at,
        reversal_of=original.id,
    )
    session.add(reversal)
    session.flush()
    changes = session.scalars(select(StockChange).where(StockChange.event_id == original.id)).all()
    for change in changes:
        stock = session.scalar(
            select(PantryStock)
            .where(PantryStock.user_id == user.id, PantryStock.ingredient_id == change.ingredient_id)
            .with_for_update()
        )
        assert stock is not None
        before = stock.quantity
        delta = -change.delta
        stock.quantity = before + delta
        session.add(
            StockChange(
                event_id=reversal.id,
                ingredient_id=change.ingredient_id,
                before=before,
                delta=delta,
                after=stock.quantity,
            )
        )
    original.reversed_at = occurred_at
    session.flush()
    return activity_response(session, reversal)


def import_local_state(session: Session, user: User, payload: LocalImportRequest) -> LocalImportResponse:
    # Import accepts legacy string IDs. Conservative mapping avoids overwriting server-owned data.
    raw_categories = payload.state.get("categories", [])
    raw_ingredients = payload.state.get("ingredients", [])
    raw_balances = payload.state.get("balances", {})
    raw_recipes = payload.state.get("recipes", [])
    raw_activity = payload.state.get("activity", [])
    raw_basket = payload.state.get("basket", [])
    raw_grocery_lists = payload.state.get("groceryLists", [])
    id_map: dict[str, UUID] = {}
    conflicts: list[dict[str, str]] = []
    if isinstance(raw_categories, list):
        existing_categories = list(
            session.scalars(select(Category).where(or_(Category.user_id.is_(None), Category.user_id == user.id)))
        )
        category_aliases = {
            "dairy & chilled": "dairy",
            "pantry staples": "pantry",
            "protein": "meat & seafood",
            "herbs & spices": "pantry",
        }
        category_by_name = {item.normalized_name: item for item in existing_categories}
        for raw in raw_categories:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not isinstance(raw.get("name"), str):
                continue
            local_id, name = raw["id"], raw["name"].strip()
            normalized = normalize_name(name)
            match = category_by_name.get(normalized) or category_by_name.get(category_aliases.get(normalized, ""))
            if match is not None:
                id_map[local_id] = match.id
                continue
            if raw.get("scope") == "global":
                conflicts.append(
                    {"kind": "category", "local_id": local_id, "message": "Global Category has no server match."}
                )
                continue
            category = Category(user_id=user.id, name=name, normalized_name=normalized)
            session.add(category)
            session.flush()
            id_map[local_id] = category.id
            category_by_name[normalized] = category
    if isinstance(raw_ingredients, list):
        existing_ingredients = list(
            session.scalars(select(Ingredient).where(or_(Ingredient.user_id.is_(None), Ingredient.user_id == user.id)))
        )
        aliases = {
            "bananas": "banana",
            "carrots": "carrot",
            "eggs": "egg",
            "tomatoes": "tomato",
            "plain flour": "wheat flour",
            "chickpeas": "canned chickpeas",
        }
        ingredient_by_name = {item.normalized_name: item for item in existing_ingredients}
        for raw in raw_ingredients:
            if not isinstance(raw, dict) or not all(
                isinstance(raw.get(key), str) for key in ("id", "name", "categoryId", "family")
            ):
                continue
            local_id, name = raw["id"], raw["name"].strip()
            normalized = normalize_name(name)
            ingredient_match = ingredient_by_name.get(normalized) or ingredient_by_name.get(aliases.get(normalized, ""))
            if ingredient_match is not None:
                if ingredient_match.measurement_family != raw["family"]:
                    conflicts.append(
                        {
                            "kind": "ingredient",
                            "local_id": local_id,
                            "message": "Measurement Family conflicts with server Ingredient.",
                        }
                    )
                else:
                    id_map[local_id] = ingredient_match.id
                continue
            category_id = id_map.get(raw["categoryId"])
            if raw.get("scope") == "global" or category_id is None:
                conflicts.append(
                    {"kind": "ingredient", "local_id": local_id, "message": "Ingredient cannot be mapped safely."}
                )
                continue
            ingredient = Ingredient(
                user_id=user.id,
                category_id=category_id,
                name=name,
                normalized_name=normalized,
                measurement_family=raw["family"],
            )
            session.add(ingredient)
            session.flush()
            id_map[local_id] = ingredient.id
            ingredient_by_name[normalized] = ingredient

    raw_profile = payload.state.get("profile")
    if isinstance(raw_profile, dict):
        display_name = raw_profile.get("displayName")
        servings = raw_profile.get("preferredServings")
        if isinstance(display_name, str) and isinstance(servings, int):
            if user.display_name == "Grocie Crumbsworth" and user.preferred_servings == 2:
                user.display_name = display_name.strip() or user.display_name
                user.preferred_servings = servings
            elif user.display_name != display_name or user.preferred_servings != servings:
                conflicts.append(
                    {
                        "kind": "profile",
                        "local_id": str(user.id),
                        "message": "Backend Profile already contains different preferences.",
                    }
                )

    if isinstance(raw_balances, dict):
        for local_id, raw_quantity in raw_balances.items():
            if not isinstance(local_id, str) or not isinstance(raw_quantity, str):
                continue
            ingredient_id = id_map.get(local_id)
            if ingredient_id is None:
                continue
            try:
                balance_quantity = (Decimal(raw_quantity) / Decimal(1000)).quantize(THREE_PLACES)
            except InvalidOperation:
                conflicts.append(
                    {"kind": "balance", "local_id": local_id, "message": "Pantry balance is not a valid quantity."}
                )
                continue
            stock = session.scalar(
                select(PantryStock).where(
                    PantryStock.user_id == user.id,
                    PantryStock.ingredient_id == ingredient_id,
                )
            )
            if stock is None:
                session.add(PantryStock(user_id=user.id, ingredient_id=ingredient_id, quantity=balance_quantity))
            elif stock.quantity == Decimal("0.000") or stock.quantity == balance_quantity:
                stock.quantity = balance_quantity
            else:
                conflicts.append(
                    {
                        "kind": "balance",
                        "local_id": local_id,
                        "message": "Backend Pantry Stock already has a different nonzero balance.",
                    }
                )
    session.flush()

    if isinstance(raw_recipes, list):
        existing_recipes = list(
            session.scalars(select(Recipe).where(or_(Recipe.user_id.is_(None), Recipe.user_id == user.id)))
        )
        recipe_by_name = {normalize_name(item.name): item for item in existing_recipes}
        for raw in raw_recipes:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not isinstance(raw.get("name"), str):
                continue
            local_id = raw["id"]
            name = raw["name"].strip()
            existing = recipe_by_name.get(normalize_name(name))
            if existing is not None:
                if raw.get("scope") == "global":
                    id_map[local_id] = existing.id
                    continue
                conflicts.append(
                    {"kind": "recipe", "local_id": local_id, "message": "Backend Recipe already uses this name."}
                )
                continue
            ingredients_raw = raw.get("ingredients")
            steps_raw = raw.get("steps")
            if not isinstance(ingredients_raw, list) or not isinstance(steps_raw, list):
                continue
            mapped_items: list[tuple[UUID, Decimal | None, str, str]] = []
            recipe_conflict = False
            for item in ingredients_raw:
                if not isinstance(item, dict) or not isinstance(item.get("ingredientId"), str):
                    recipe_conflict = True
                    break
                ingredient_id = id_map.get(item["ingredientId"])
                unit = item.get("unit")
                raw_quantity = item.get("quantity")
                if ingredient_id is None or unit not in {"mg", "g", "kg", "ml", "L", "item"}:
                    recipe_conflict = True
                    break
                quantity_input = raw_quantity if isinstance(raw_quantity, str) else ""
                recipe_quantity: Decimal | None = None
                if raw.get("status") == "published" and isinstance(raw_quantity, str):
                    try:
                        recipe_quantity = (Decimal(raw_quantity) / Decimal(1000)).quantize(THREE_PLACES)
                        quantity_input = str((recipe_quantity / UNIT_FACTORS[Unit(unit)]).normalize())
                    except InvalidOperation:
                        recipe_conflict = True
                        break
                elif isinstance(raw_quantity, str):
                    recipe_quantity = _parse_quantity(raw_quantity, Unit(unit))
                mapped_items.append((ingredient_id, recipe_quantity, quantity_input, unit))
            if recipe_conflict:
                conflicts.append(
                    {"kind": "recipe", "local_id": local_id, "message": "Recipe contains an unmapped Ingredient."}
                )
                continue
            recipe = Recipe(
                user_id=user.id,
                status="published" if raw.get("status") == "published" else "draft",
                name=name,
                description=raw.get("description") if isinstance(raw.get("description"), str) else "",
                base_servings=raw.get("baseServings") if isinstance(raw.get("baseServings"), int) else 1,
            )
            session.add(recipe)
            session.flush()
            for position, (ingredient_id, stored_quantity, quantity_input, unit) in enumerate(mapped_items):
                session.add(
                    RecipeIngredient(
                        recipe_id=recipe.id,
                        ingredient_id=ingredient_id,
                        position=position,
                        quantity=stored_quantity,
                        quantity_input=quantity_input,
                        unit=unit,
                    )
                )
            for position, body in enumerate(steps_raw):
                if isinstance(body, str):
                    session.add(RecipeStep(recipe_id=recipe.id, position=position, body=body))
            id_map[local_id] = recipe.id
            recipe_by_name[normalize_name(name)] = recipe
    session.flush()

    event_models: dict[str, ActivityEvent] = {}
    if isinstance(raw_activity, list):
        for raw in raw_activity:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            local_id = raw["id"]
            changes_raw = raw.get("changes")
            if not isinstance(changes_raw, list):
                continue
            mapped_changes: list[tuple[UUID, Decimal, Decimal, Decimal]] = []
            try:
                for change in changes_raw:
                    if not isinstance(change, dict) or not isinstance(change.get("ingredientId"), str):
                        raise ValueError
                    ingredient_id = id_map.get(change["ingredientId"])
                    if ingredient_id is None:
                        raise ValueError
                    before = (Decimal(str(change["before"])) / Decimal(1000)).quantize(THREE_PLACES)
                    delta = (Decimal(str(change["delta"])) / Decimal(1000)).quantize(THREE_PLACES)
                    after = (Decimal(str(change["after"])) / Decimal(1000)).quantize(THREE_PLACES)
                    mapped_changes.append((ingredient_id, before, delta, after))
            except (InvalidOperation, KeyError, ValueError):
                conflicts.append(
                    {"kind": "activity", "local_id": local_id, "message": "Activity contains an unmapped Stock Change."}
                )
                continue
            try:
                event_id = UUID(local_id)
            except ValueError:
                event_id = uuid4()
            event = ActivityEvent(
                id=event_id,
                user_id=user.id,
                event_type=raw.get("type") if raw.get("type") in {"cooking", "manual", "reversal"} else "manual",
                title=raw.get("title") if isinstance(raw.get("title"), str) else "Imported activity",
                detail=raw.get("detail") if isinstance(raw.get("detail"), str) else "Imported from this device",
                occurred_at=datetime.fromisoformat(raw["occurredAt"])
                if isinstance(raw.get("occurredAt"), str)
                else datetime.now(UTC),
                recipe_id=id_map.get(raw["recipeId"]) if isinstance(raw.get("recipeId"), str) else None,
                servings=raw.get("servings") if isinstance(raw.get("servings"), int) else None,
                reversed_at=datetime.fromisoformat(raw["reversedAt"])
                if isinstance(raw.get("reversedAt"), str)
                else None,
            )
            session.add(event)
            session.flush()
            for ingredient_id, before, delta, after in mapped_changes:
                session.add(
                    StockChange(
                        event_id=event.id,
                        ingredient_id=ingredient_id,
                        before=before,
                        delta=delta,
                        after=after,
                    )
                )
            event_models[local_id] = event
            id_map[local_id] = event.id
        for raw in raw_activity:
            if isinstance(raw, dict) and isinstance(raw.get("id"), str) and isinstance(raw.get("reversalOf"), str):
                current_event = event_models.get(raw["id"])
                original = event_models.get(raw["reversalOf"])
                if current_event is not None and original is not None:
                    current_event.reversal_of = original.id

    if isinstance(raw_basket, list):
        existing_basket = session.scalars(select(BasketItem).where(BasketItem.user_id == user.id)).all()
        if existing_basket and raw_basket:
            conflicts.append(
                {"kind": "basket", "local_id": str(user.id), "message": "Backend Basket already contains Recipes."}
            )
        elif not existing_basket:
            for position, raw in enumerate(raw_basket):
                if not isinstance(raw, dict) or not isinstance(raw.get("recipeId"), str):
                    continue
                recipe_id = id_map.get(raw["recipeId"])
                servings = raw.get("servings")
                imported_recipe = session.get(Recipe, recipe_id) if recipe_id is not None else None
                if (
                    imported_recipe is None
                    or imported_recipe.status != RecipeStatus.PUBLISHED
                    or not isinstance(servings, int)
                    or not 1 <= servings <= 12
                ):
                    conflicts.append(
                        {
                            "kind": "basket",
                            "local_id": raw["recipeId"],
                            "message": "Basket Recipe cannot be mapped safely.",
                        }
                    )
                    continue
                session.add(
                    BasketItem(
                        user_id=user.id,
                        recipe_id=imported_recipe.id,
                        servings=servings,
                        position=position,
                    )
                )
    session.flush()

    if isinstance(raw_grocery_lists, list):
        existing_lists = session.scalars(select(GroceryList).where(GroceryList.user_id == user.id)).all()
        if existing_lists and raw_grocery_lists:
            conflicts.append(
                {
                    "kind": "grocery-list",
                    "local_id": str(user.id),
                    "message": "Backend already contains Grocery Lists.",
                }
            )
        elif not existing_lists:
            imported_active = False
            for raw in raw_grocery_lists:
                if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                    continue
                local_list_id = raw["id"]
                try:
                    grocery_list_id = UUID(local_list_id)
                except ValueError:
                    grocery_list_id = uuid4()
                status = raw.get("status")
                title = raw.get("title")
                if status not in {"active", "completed"} or not isinstance(title, str) or not title.strip():
                    conflicts.append(
                        {
                            "kind": "grocery-list",
                            "local_id": local_list_id,
                            "message": "Grocery List metadata is invalid.",
                        }
                    )
                    continue
                if status == "active" and imported_active:
                    conflicts.append(
                        {
                            "kind": "grocery-list",
                            "local_id": local_list_id,
                            "message": "Only one active Grocery List can be imported.",
                        }
                    )
                    continue
                created_at = (
                    datetime.fromisoformat(raw["createdAt"])
                    if isinstance(raw.get("createdAt"), str)
                    else datetime.now(UTC)
                )
                updated_at = (
                    datetime.fromisoformat(raw["updatedAt"]) if isinstance(raw.get("updatedAt"), str) else created_at
                )
                completed_at = (
                    datetime.fromisoformat(raw["completedAt"]) if isinstance(raw.get("completedAt"), str) else None
                )
                grocery_list = GroceryList(
                    id=grocery_list_id,
                    user_id=user.id,
                    title=title.strip(),
                    status=status,
                    created_at=created_at,
                    updated_at=updated_at,
                    completed_at=completed_at,
                )
                session.add(grocery_list)
                session.flush()
                id_map[local_list_id] = grocery_list.id
                imported_active = imported_active or status == "active"

                raw_sources = raw.get("recipes")
                if isinstance(raw_sources, list):
                    for position, source in enumerate(raw_sources):
                        if not isinstance(source, dict) or not isinstance(source.get("recipeId"), str):
                            continue
                        mapped_recipe_id = id_map.get(source["recipeId"])
                        servings = source.get("servings")
                        base_servings = source.get("baseServings")
                        name = source.get("recipeName")
                        if (
                            mapped_recipe_id is None
                            or not isinstance(servings, int)
                            or not 1 <= servings <= 12
                            or not isinstance(base_servings, int)
                            or not isinstance(name, str)
                        ):
                            conflicts.append(
                                {
                                    "kind": "grocery-list-recipe",
                                    "local_id": source["recipeId"],
                                    "message": "Grocery List source Recipe cannot be mapped safely.",
                                }
                            )
                            continue
                        session.add(
                            GroceryListRecipe(
                                grocery_list_id=grocery_list.id,
                                recipe_id=mapped_recipe_id,
                                recipe_snapshot_id=mapped_recipe_id,
                                recipe_name=name,
                                position=position,
                                servings=servings,
                                base_servings=base_servings,
                            )
                        )

                raw_items = raw.get("items")
                if isinstance(raw_items, list):
                    for raw_item in raw_items:
                        if not isinstance(raw_item, dict) or not isinstance(raw_item.get("id"), str):
                            continue
                        local_item_id = raw_item["id"]
                        try:
                            grocery_item_id = UUID(local_item_id)
                        except ValueError:
                            grocery_item_id = uuid4()
                        local_ingredient_id = raw_item.get("ingredientId")
                        ingredient_id = (
                            id_map.get(local_ingredient_id) if isinstance(local_ingredient_id, str) else None
                        )

                        try:
                            quantity = _imported_minor_quantity(raw_item, "quantity")
                            original_required = _imported_minor_quantity(raw_item, "originalRequired")
                            original_pantry = _imported_minor_quantity(raw_item, "originalPantry")
                            original_quantity = _imported_minor_quantity(raw_item, "originalQuantity")
                        except InvalidOperation:
                            conflicts.append(
                                {
                                    "kind": "grocery-list-item",
                                    "local_id": local_item_id,
                                    "message": "Grocery List Item quantity is invalid.",
                                }
                            )
                            continue
                        label = raw_item.get("label")
                        if not isinstance(label, str) or not label.strip():
                            continue
                        item = GroceryListItem(
                            id=grocery_item_id,
                            grocery_list_id=grocery_list.id,
                            ingredient_id=ingredient_id,
                            original_ingredient_id=ingredient_id if raw_item.get("origin") == "generated" else None,
                            label=label.strip(),
                            category_name=(
                                raw_item["categoryName"] if isinstance(raw_item.get("categoryName"), str) else "Other"
                            ),
                            measurement_family=(
                                raw_item["family"] if raw_item.get("family") in {"mass", "volume", "count"} else None
                            ),
                            quantity=quantity,
                            unit=raw_item["unit"] if isinstance(raw_item.get("unit"), str) else None,
                            checked=raw_item.get("checked") is True,
                            origin=raw_item["origin"]
                            if raw_item.get("origin") in {"generated", "manual"}
                            else "manual",
                            edited=raw_item.get("edited") is True,
                            original_required=original_required,
                            original_pantry=original_pantry,
                            original_quantity=original_quantity,
                            created_at=(
                                datetime.fromisoformat(raw_item["createdAt"])
                                if isinstance(raw_item.get("createdAt"), str)
                                else created_at
                            ),
                            updated_at=(
                                datetime.fromisoformat(raw_item["updatedAt"])
                                if isinstance(raw_item.get("updatedAt"), str)
                                else updated_at
                            ),
                        )
                        session.add(item)
                        session.flush()
                        id_map[local_item_id] = item.id
                        raw_item_sources = raw_item.get("sources")
                        if isinstance(raw_item_sources, list):
                            for source in raw_item_sources:
                                if not isinstance(source, dict) or not isinstance(source.get("recipeId"), str):
                                    continue
                                mapped_recipe_id = id_map.get(source["recipeId"])
                                source_quantity = source.get("quantity")
                                source_name = source.get("recipeName")
                                source_servings = source.get("servings")
                                source_unit = source.get("unit")
                                if (
                                    mapped_recipe_id is None
                                    or not isinstance(source_quantity, str)
                                    or not isinstance(source_name, str)
                                    or not isinstance(source_servings, int)
                                    or source_unit not in {"g", "ml", "item"}
                                ):
                                    continue
                                try:
                                    stored_source_quantity = (Decimal(source_quantity) / Decimal(1000)).quantize(
                                        THREE_PLACES, rounding=ROUND_HALF_UP
                                    )
                                except InvalidOperation:
                                    continue
                                session.add(
                                    GroceryListItemSource(
                                        grocery_list_item_id=item.id,
                                        recipe_snapshot_id=mapped_recipe_id,
                                        recipe_name=source_name,
                                        servings=source_servings,
                                        quantity=stored_source_quantity,
                                        unit=source_unit,
                                    )
                                )
    session.flush()

    return LocalImportResponse(
        revision=user.state_revision,
        id_map=id_map,
        conflicts=[ImportConflict.model_validate(conflict) for conflict in conflicts],
    )
