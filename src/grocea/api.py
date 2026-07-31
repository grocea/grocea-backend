from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from grocea.constants import LOCAL_USER_ID
from grocea.dependencies import CurrentUser, DbSession, MutationRequest
from grocea.errors import DomainError
from grocea.full_services import (
    activity_response,
    adjust_stock,
    clear_basket,
    complete_grocery_list,
    cook_recipe,
    create_grocery_list_from_basket,
    create_grocery_list_item,
    create_recipe,
    delete_grocery_list,
    get_grocery_list_model,
    get_recipe_model,
    grocery_list_response,
    import_local_state,
    list_activity,
    list_grocery_lists,
    list_pantry_stocks,
    list_recipes,
    publish_recipe,
    recipe_response,
    remove_basket_item,
    remove_grocery_list_item,
    reuse_grocery_list_recipes,
    reverse_activity,
    state_response,
    update_grocery_list,
    update_grocery_list_item,
    update_recipe,
    upsert_basket_item,
)
from grocea.full_services import (
    delete_recipe as delete_recipe_service,
)
from grocea.models import ActivityEvent, ProcessedMutation, User
from grocea.schemas import (
    ActivityResponse,
    BasketItemUpsert,
    BasketResponse,
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    CookRecipeCreate,
    ErrorResponse,
    GroceryListComplete,
    GroceryListCreate,
    GroceryListItemCreate,
    GroceryListItemUpdate,
    GroceryListResponse,
    GroceryListUpdate,
    HealthResponse,
    IngredientCreate,
    IngredientPage,
    IngredientResponse,
    IngredientUpdate,
    LocalImportRequest,
    LocalImportResponse,
    PantryStockResponse,
    ProfileResponse,
    ProfileUpdate,
    RecipeCreate,
    RecipeResponse,
    RecipeUpdate,
    ReverseActivityCreate,
    ScopeFilter,
    StateResponse,
    StockOperationCreate,
)
from grocea.services import (
    archive_category,
    archive_ingredient,
    category_response,
    create_category,
    create_ingredient,
    get_category_model,
    get_ingredient,
    list_categories,
    list_ingredients,
    profile_response,
    restore_category,
    restore_ingredient,
    update_category,
    update_ingredient,
    update_profile,
)

router = APIRouter(
    prefix="/api",
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)


def apply_mutation[ResponseModel: BaseModel](
    session: DbSession,
    user: User,
    mutation: MutationRequest,
    response: Response,
    mutation_type: str,
    response_model: type[ResponseModel],
    operation: Callable[[], ResponseModel],
    *,
    imported: bool = False,
) -> ResponseModel:
    cached = session.scalar(
        select(ProcessedMutation).where(
            ProcessedMutation.device_id == mutation.device_id,
            ProcessedMutation.mutation_id == mutation.mutation_id,
        )
    )
    if cached is not None:
        if cached.mutation_type != mutation_type:
            raise DomainError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key was already used for another mutation.")
        response.headers["X-State-Revision"] = str(cached.revision)
        response.headers["X-Idempotent-Replay"] = "true"
        return response_model.model_validate(cached.response_body)
    result = operation()
    user.state_revision += 1
    if hasattr(result, "revision"):
        result.revision = user.state_revision
    session.flush()
    body = result.model_dump(mode="json")
    session.add(
        ProcessedMutation(
            user_id=user.id,
            device_id=mutation.device_id,
            mutation_id=mutation.mutation_id,
            mutation_type=mutation_type,
            response_body=body,
            revision=user.state_revision,
            imported=imported,
        )
    )
    session.commit()
    response.headers["X-State-Revision"] = str(user.state_revision)
    return result


@router.get("/health/live", response_model=HealthResponse, tags=["health"])
def live() -> HealthResponse:
    return HealthResponse()


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["health"],
)
def ready(session: DbSession) -> HealthResponse:
    try:
        session.execute(select(1))
        if session.get(User, LOCAL_USER_ID) is None:
            raise DomainError(503, "LOCAL_PROFILE_NOT_SEEDED", "Local Profile has not been seeded.")
    except DomainError:
        raise
    except SQLAlchemyError as exc:
        raise DomainError(503, "SERVICE_NOT_READY", "Database is not ready.") from exc
    return HealthResponse()


@router.get("/profile", response_model=ProfileResponse, tags=["profile"])
def read_profile(user: CurrentUser) -> ProfileResponse:
    return profile_response(user)


@router.patch("/profile", response_model=ProfileResponse, tags=["profile"])
def patch_profile(
    payload: ProfileUpdate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> ProfileResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "profile.update",
        ProfileResponse,
        lambda: update_profile(session, user, payload),
    )


@router.get("/categories", response_model=list[CategoryResponse], tags=["categories"])
def read_categories(
    session: DbSession,
    user: CurrentUser,
    query: str | None = Query(default=None, max_length=120),
    scope: ScopeFilter = ScopeFilter.ALL,
    include_archived: bool = False,
) -> list[CategoryResponse]:
    return list_categories(session, user, query=query, scope=scope, include_archived=include_archived)


@router.post(
    "/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}},
    tags=["categories"],
)
def post_category(
    payload: CategoryCreate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> CategoryResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "category.create",
        CategoryResponse,
        lambda: create_category(session, user, payload),
    )


@router.get("/categories/{category_id}", response_model=CategoryResponse, tags=["categories"])
def read_category(category_id: UUID, session: DbSession, user: CurrentUser) -> CategoryResponse:
    return category_response(get_category_model(session, user, category_id))


@router.patch("/categories/{category_id}", response_model=CategoryResponse, tags=["categories"])
def patch_category(
    category_id: UUID,
    payload: CategoryUpdate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> CategoryResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "category.update",
        CategoryResponse,
        lambda: update_category(session, user, category_id, payload),
    )


@router.delete("/categories/{category_id}", response_model=CategoryResponse, tags=["categories"])
def delete_category(
    category_id: UUID,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> CategoryResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "category.archive",
        CategoryResponse,
        lambda: archive_category(session, user, category_id),
    )


@router.post("/categories/{category_id}/restore", response_model=CategoryResponse, tags=["categories"])
def post_category_restore(
    category_id: UUID,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> CategoryResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "category.restore",
        CategoryResponse,
        lambda: restore_category(session, user, category_id),
    )


@router.get("/ingredients", response_model=IngredientPage, tags=["ingredients"])
def read_ingredients(
    session: DbSession,
    user: CurrentUser,
    query: str | None = Query(default=None, max_length=120),
    category_id: UUID | None = None,
    scope: ScopeFilter = ScopeFilter.ALL,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IngredientPage:
    return list_ingredients(
        session,
        user,
        query=query,
        category_id=category_id,
        scope=scope,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/ingredients",
    response_model=IngredientResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}},
    tags=["ingredients"],
)
def post_ingredient(
    payload: IngredientCreate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> IngredientResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "ingredient.create",
        IngredientResponse,
        lambda: create_ingredient(session, user, payload),
    )


@router.get("/ingredients/{ingredient_id}", response_model=IngredientResponse, tags=["ingredients"])
def read_ingredient(ingredient_id: UUID, session: DbSession, user: CurrentUser) -> IngredientResponse:
    return get_ingredient(session, user, ingredient_id)


@router.patch("/ingredients/{ingredient_id}", response_model=IngredientResponse, tags=["ingredients"])
def patch_ingredient(
    ingredient_id: UUID,
    payload: IngredientUpdate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> IngredientResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "ingredient.update",
        IngredientResponse,
        lambda: update_ingredient(session, user, ingredient_id, payload),
    )


@router.delete("/ingredients/{ingredient_id}", response_model=IngredientResponse, tags=["ingredients"])
def delete_ingredient(
    ingredient_id: UUID,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> IngredientResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "ingredient.archive",
        IngredientResponse,
        lambda: archive_ingredient(session, user, ingredient_id),
    )


@router.post("/ingredients/{ingredient_id}/restore", response_model=IngredientResponse, tags=["ingredients"])
def post_ingredient_restore(
    ingredient_id: UUID,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> IngredientResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "ingredient.restore",
        IngredientResponse,
        lambda: restore_ingredient(session, user, ingredient_id),
    )


@router.get("/state", response_model=StateResponse, tags=["sync"])
def read_state(session: DbSession, user: CurrentUser, response: Response) -> StateResponse:
    result = state_response(session, user)
    response.headers["X-State-Revision"] = str(result.revision)
    return result


@router.get("/pantry-stocks", response_model=list[PantryStockResponse], tags=["pantry"])
def read_pantry_stocks(session: DbSession, user: CurrentUser) -> list[PantryStockResponse]:
    return list_pantry_stocks(session, user)


@router.post(
    "/pantry-stocks/{ingredient_id}/operations",
    response_model=ActivityResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["pantry"],
)
def post_stock_operation(
    ingredient_id: UUID,
    payload: StockOperationCreate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> ActivityResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "stock.operation",
        ActivityResponse,
        lambda: adjust_stock(session, user, ingredient_id, payload),
    )


@router.put("/basket/recipes/{recipe_id}", response_model=BasketResponse, tags=["basket"])
def put_basket_recipe(
    recipe_id: UUID,
    payload: BasketItemUpsert,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> BasketResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "basket.recipe.upsert",
        BasketResponse,
        lambda: upsert_basket_item(session, user, recipe_id, payload),
    )


@router.delete("/basket/recipes/{recipe_id}", response_model=BasketResponse, tags=["basket"])
def delete_basket_recipe(
    recipe_id: UUID,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> BasketResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "basket.recipe.remove",
        BasketResponse,
        lambda: remove_basket_item(session, user, recipe_id),
    )


@router.delete("/basket", response_model=BasketResponse, tags=["basket"])
def delete_basket(
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> BasketResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "basket.clear",
        BasketResponse,
        lambda: clear_basket(session, user),
    )


@router.post(
    "/grocery-lists/from-basket",
    response_model=GroceryListResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["grocery-lists"],
)
def post_grocery_list_from_basket(
    payload: GroceryListCreate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> GroceryListResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "grocery-list.create",
        GroceryListResponse,
        lambda: create_grocery_list_from_basket(session, user, payload),
    )


@router.post(
    "/grocery-lists/{grocery_list_id}/items",
    response_model=GroceryListResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["grocery-lists"],
)
def post_grocery_list_item(
    grocery_list_id: UUID,
    payload: GroceryListItemCreate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> GroceryListResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "grocery-list.item.create",
        GroceryListResponse,
        lambda: create_grocery_list_item(session, user, grocery_list_id, payload),
    )


@router.put(
    "/grocery-lists/{grocery_list_id}/items/{grocery_item_id}",
    response_model=GroceryListResponse,
    tags=["grocery-lists"],
)
def put_grocery_list_item(
    grocery_list_id: UUID,
    grocery_item_id: UUID,
    payload: GroceryListItemUpdate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> GroceryListResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "grocery-list.item.update",
        GroceryListResponse,
        lambda: update_grocery_list_item(session, user, grocery_list_id, grocery_item_id, payload),
    )


@router.post(
    "/grocery-lists/{grocery_list_id}/complete",
    response_model=GroceryListResponse,
    tags=["grocery-lists"],
)
def post_grocery_list_complete(
    grocery_list_id: UUID,
    payload: GroceryListComplete,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> GroceryListResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "grocery-list.complete",
        GroceryListResponse,
        lambda: complete_grocery_list(session, user, grocery_list_id, payload),
    )


@router.get("/grocery-lists", response_model=list[GroceryListResponse], tags=["grocery-lists"])
def read_grocery_lists(session: DbSession, user: CurrentUser) -> list[GroceryListResponse]:
    return list_grocery_lists(session, user)


@router.get("/grocery-lists/{grocery_list_id}", response_model=GroceryListResponse, tags=["grocery-lists"])
def read_grocery_list(grocery_list_id: UUID, session: DbSession, user: CurrentUser) -> GroceryListResponse:
    return grocery_list_response(session, get_grocery_list_model(session, user, grocery_list_id))


@router.patch("/grocery-lists/{grocery_list_id}", response_model=GroceryListResponse, tags=["grocery-lists"])
def patch_grocery_list(
    grocery_list_id: UUID,
    payload: GroceryListUpdate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> GroceryListResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "grocery-list.update",
        GroceryListResponse,
        lambda: update_grocery_list(session, user, grocery_list_id, payload),
    )


@router.delete(
    "/grocery-lists/{grocery_list_id}/items/{grocery_item_id}",
    response_model=GroceryListResponse,
    tags=["grocery-lists"],
)
def delete_grocery_list_item(
    grocery_list_id: UUID,
    grocery_item_id: UUID,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> GroceryListResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "grocery-list.item.delete",
        GroceryListResponse,
        lambda: remove_grocery_list_item(session, user, grocery_list_id, grocery_item_id),
    )


@router.post("/grocery-lists/{grocery_list_id}/reuse-recipes", response_model=BasketResponse, tags=["grocery-lists"])
def post_grocery_list_reuse(
    grocery_list_id: UUID,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> BasketResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "grocery-list.reuse",
        BasketResponse,
        lambda: reuse_grocery_list_recipes(session, user, grocery_list_id),
    )


@router.delete("/grocery-lists/{grocery_list_id}", response_model=BasketResponse, tags=["grocery-lists"])
def delete_grocery_list_route(
    grocery_list_id: UUID,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
    restore_recipes: bool = False,
) -> BasketResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "grocery-list.delete",
        BasketResponse,
        lambda: delete_grocery_list(session, user, grocery_list_id, restore_recipes=restore_recipes),
    )


@router.get("/recipes", response_model=list[RecipeResponse], tags=["recipes"])
def read_recipes(session: DbSession, user: CurrentUser) -> list[RecipeResponse]:
    return list_recipes(session, user)


@router.post("/recipes", response_model=RecipeResponse, status_code=status.HTTP_201_CREATED, tags=["recipes"])
def post_recipe(
    payload: RecipeCreate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> RecipeResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "recipe.create",
        RecipeResponse,
        lambda: create_recipe(session, user, payload),
    )


@router.get("/recipes/{recipe_id}", response_model=RecipeResponse, tags=["recipes"])
def read_recipe(recipe_id: UUID, session: DbSession, user: CurrentUser) -> RecipeResponse:
    return recipe_response(session, get_recipe_model(session, user, recipe_id))


@router.patch("/recipes/{recipe_id}", response_model=RecipeResponse, tags=["recipes"])
def patch_recipe(
    recipe_id: UUID,
    payload: RecipeUpdate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> RecipeResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "recipe.update",
        RecipeResponse,
        lambda: update_recipe(session, user, recipe_id, payload),
    )


@router.delete("/recipes/{recipe_id}", response_model=RecipeResponse, tags=["recipes"])
def delete_recipe(
    recipe_id: UUID,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> RecipeResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "recipe.delete",
        RecipeResponse,
        lambda: delete_recipe_service(session, user, recipe_id),
    )


@router.post("/recipes/{recipe_id}/publish", response_model=RecipeResponse, tags=["recipes"])
def post_recipe_publish(
    recipe_id: UUID,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> RecipeResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "recipe.publish",
        RecipeResponse,
        lambda: publish_recipe(session, user, recipe_id),
    )


@router.post("/recipes/{recipe_id}/cook", response_model=ActivityResponse, tags=["recipes"])
def post_recipe_cook(
    recipe_id: UUID,
    payload: CookRecipeCreate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> ActivityResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "recipe.cook",
        ActivityResponse,
        lambda: cook_recipe(session, user, recipe_id, payload),
    )


@router.get("/activity", response_model=list[ActivityResponse], tags=["activity"])
def read_activity(session: DbSession, user: CurrentUser) -> list[ActivityResponse]:
    return list_activity(session, user)


@router.get("/activity/{event_id}", response_model=ActivityResponse, tags=["activity"])
def read_activity_event(event_id: UUID, session: DbSession, user: CurrentUser) -> ActivityResponse:
    event = session.scalar(select(ActivityEvent).where(ActivityEvent.id == event_id, ActivityEvent.user_id == user.id))
    if event is None:
        raise DomainError(404, "ACTIVITY_NOT_FOUND", "Activity Event was not found.")
    return activity_response(session, event)


@router.post("/activity/{event_id}/reverse", response_model=ActivityResponse, tags=["activity"])
def post_activity_reverse(
    event_id: UUID,
    payload: ReverseActivityCreate,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> ActivityResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "activity.reverse",
        ActivityResponse,
        lambda: reverse_activity(session, user, event_id, payload),
    )


@router.post("/imports/local-state", response_model=LocalImportResponse, tags=["sync"])
def post_local_import(
    payload: LocalImportRequest,
    session: DbSession,
    user: CurrentUser,
    mutation: MutationRequest,
    response: Response,
) -> LocalImportResponse:
    return apply_mutation(
        session,
        user,
        mutation,
        response,
        "state.import",
        LocalImportResponse,
        lambda: import_local_state(session, user, payload),
        imported=True,
    )
