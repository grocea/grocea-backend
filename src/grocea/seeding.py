from __future__ import annotations

from decimal import Decimal
from importlib.resources import files
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from grocea.constants import LOCAL_USER_ID
from grocea.db import engine
from grocea.models import Category, Ingredient, Recipe, RecipeIngredient, RecipeStep, User
from grocea.normalization import normalize_name


class SeedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeedCategory(SeedModel):
    id: UUID
    key: str
    name: str


class SeedIngredient(SeedModel):
    id: UUID
    name: str
    category: str
    measurement_family: Literal["mass", "volume", "count"]


class SeedRecipeIngredient(SeedModel):
    ingredient_id: UUID
    quantity: Decimal
    quantity_input: str
    unit: Literal["mg", "g", "kg", "ml", "L", "item"]


class SeedRecipe(SeedModel):
    id: UUID
    name: str
    description: str
    base_servings: int
    ingredients: list[SeedRecipeIngredient]
    steps: list[str]


def _load_yaml(name: str) -> object:
    resource = files("grocea.seed_data").joinpath(name)
    return yaml.safe_load(resource.read_text(encoding="utf-8"))


def load_categories() -> list[SeedCategory]:
    raw = _load_yaml("categories.yaml")
    if not isinstance(raw, dict) or "categories" not in raw:
        raise ValueError("categories.yaml must contain a categories list")
    return TypeAdapter(list[SeedCategory]).validate_python(raw["categories"])


def load_ingredients() -> list[SeedIngredient]:
    raw = _load_yaml("ingredients.yaml")
    if not isinstance(raw, dict) or "ingredients" not in raw:
        raise ValueError("ingredients.yaml must contain an ingredients list")
    ingredients = TypeAdapter(list[SeedIngredient]).validate_python(raw["ingredients"])
    if len(ingredients) != 150:
        raise ValueError(f"Expected exactly 150 seed Ingredients, found {len(ingredients)}")
    return ingredients


def load_recipes() -> list[SeedRecipe]:
    raw = _load_yaml("recipes.yaml")
    if not isinstance(raw, dict) or "recipes" not in raw:
        raise ValueError("recipes.yaml must contain a recipes list")
    return TypeAdapter(list[SeedRecipe]).validate_python(raw["recipes"])


def apply_seed(session: Session) -> None:
    user = session.get(User, LOCAL_USER_ID)
    if user is None:
        session.add(
            User(
                id=LOCAL_USER_ID,
                display_name="Grocie Crumbsworth",
                preferred_servings=2,
                measurement_system="metric",
            )
        )
        session.flush()

    category_ids: dict[str, UUID] = {}
    for category_seed in load_categories():
        normalized = normalize_name(category_seed.name)
        custom_collision = session.scalar(
            select(Category.id).where(Category.user_id.is_not(None), Category.normalized_name == normalized).limit(1)
        )
        if custom_collision is not None:
            raise ValueError(f"Global Category '{category_seed.name}' conflicts with a Custom Category")
        category = session.get(Category, category_seed.id)
        if category is None:
            category = Category(
                id=category_seed.id,
                user_id=None,
                name=category_seed.name.strip(),
                normalized_name=normalized,
            )
            session.add(category)
        else:
            if category.user_id is not None:
                raise ValueError(f"Seed Category UUID {category_seed.id} belongs to a custom record")
            category.name = category_seed.name.strip()
            category.normalized_name = normalized
        category_ids[category_seed.key] = category_seed.id
    session.flush()

    for ingredient_seed in load_ingredients():
        category_id = category_ids.get(ingredient_seed.category)
        if category_id is None:
            raise ValueError(
                f"Unknown Category key '{ingredient_seed.category}' for Ingredient '{ingredient_seed.name}'"
            )
        normalized = normalize_name(ingredient_seed.name)
        custom_collision = session.scalar(
            select(Ingredient.id)
            .where(Ingredient.user_id.is_not(None), Ingredient.normalized_name == normalized)
            .limit(1)
        )
        if custom_collision is not None:
            raise ValueError(f"Global Ingredient '{ingredient_seed.name}' conflicts with a Custom Ingredient")
        ingredient = session.get(Ingredient, ingredient_seed.id)
        if ingredient is None:
            session.add(
                Ingredient(
                    id=ingredient_seed.id,
                    user_id=None,
                    category_id=category_id,
                    name=ingredient_seed.name.strip(),
                    normalized_name=normalized,
                    measurement_family=ingredient_seed.measurement_family,
                )
            )
            continue
        if ingredient.user_id is not None:
            raise ValueError(f"Seed Ingredient UUID {ingredient_seed.id} belongs to a custom record")
        if ingredient.measurement_family != ingredient_seed.measurement_family:
            raise ValueError(f"Seed cannot change Measurement Family for '{ingredient_seed.name}'")
        ingredient.name = ingredient_seed.name.strip()
        ingredient.normalized_name = normalized
        ingredient.category_id = category_id
    session.flush()

    for recipe_seed in load_recipes():
        recipe = session.get(Recipe, recipe_seed.id)
        if recipe is None:
            recipe = Recipe(
                id=recipe_seed.id,
                user_id=None,
                status="published",
                name=recipe_seed.name,
                description=recipe_seed.description,
                base_servings=recipe_seed.base_servings,
            )
            session.add(recipe)
            session.flush()
            for position, item in enumerate(recipe_seed.ingredients):
                session.add(
                    RecipeIngredient(
                        recipe_id=recipe.id,
                        ingredient_id=item.ingredient_id,
                        position=position,
                        quantity=item.quantity,
                        quantity_input=item.quantity_input,
                        unit=item.unit,
                    )
                )
            for position, body in enumerate(recipe_seed.steps):
                session.add(RecipeStep(recipe_id=recipe.id, position=position, body=body))
            continue
        if recipe.user_id is not None:
            raise ValueError(f"Seed Recipe UUID {recipe.id} belongs to a custom record")
        recipe.name = recipe_seed.name
        recipe.description = recipe_seed.description
        recipe.base_servings = recipe_seed.base_servings


def seed_database() -> None:
    with Session(engine) as session:
        try:
            apply_seed(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
