from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from grocea.errors import DomainError
from grocea.models import (
    ActivityEvent,
    Category,
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
    CookRecipeCreate,
    ImportConflict,
    LocalImportRequest,
    LocalImportResponse,
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

    return LocalImportResponse(
        revision=user.state_revision,
        id_map=id_map,
        conflicts=[ImportConflict.model_validate(conflict) for conflict in conflicts],
    )
