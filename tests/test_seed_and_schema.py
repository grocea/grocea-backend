from __future__ import annotations

from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from grocea.models import AuthSession, Category, Ingredient, PantryStock, Recipe, User
from grocea.seeding import apply_seed, load_categories, load_ingredients


def test_seed_manifests_have_required_counts_and_unique_ids() -> None:
    categories = load_categories()
    ingredients = load_ingredients()

    assert len(categories) == 8
    assert len(ingredients) == 150
    assert len({item.id for item in categories}) == 8
    assert len({item.id for item in ingredients}) == 150
    assert {item.measurement_family for item in ingredients} == {"mass", "volume", "count"}


def test_migration_created_full_pwa_tables(test_engine: Engine) -> None:
    tables = set(inspect(test_engine).get_table_names())
    assert tables == {
        "activity_events",
        "alembic_version",
        "categories",
        "ingredients",
        "pantry_stocks",
        "processed_mutations",
        "recipe_ingredients",
        "recipe_steps",
        "recipes",
        "stock_changes",
        "basket_items",
        "grocery_lists",
        "grocery_list_recipes",
        "grocery_list_items",
        "grocery_list_item_sources",
        "users",
        "auth_sessions",
    }


def test_seed_is_idempotent_and_does_not_create_an_account(db_session: Session) -> None:
    apply_seed(db_session)
    apply_seed(db_session)
    db_session.flush()

    assert db_session.scalars(select(User)).all() == []
    assert db_session.scalars(select(AuthSession)).all() == []
    assert len(db_session.scalars(select(Category).where(Category.user_id.is_(None))).all()) == 8
    assert len(db_session.scalars(select(Ingredient).where(Ingredient.user_id.is_(None))).all()) == 150
    assert len(db_session.scalars(select(Recipe).where(Recipe.user_id.is_(None))).all()) >= 1
    assert len(db_session.scalars(select(PantryStock)).all()) == 0
