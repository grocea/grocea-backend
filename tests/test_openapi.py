from __future__ import annotations

import json
from pathlib import Path

from grocea.main import app

ROOT = Path(__file__).resolve().parents[1]


def test_committed_openapi_matches_application() -> None:
    committed = json.loads((ROOT / "openapi" / "openapi.json").read_text(encoding="utf-8"))
    assert committed == app.openapi()


def test_openapi_exposes_full_pwa_paths() -> None:
    paths = set(app.openapi()["paths"])
    assert paths == {
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/session",
        "/api/auth/logout",
        "/api/auth/password",
        "/api/health/live",
        "/api/health/ready",
        "/api/profile",
        "/api/categories",
        "/api/categories/{category_id}",
        "/api/categories/{category_id}/restore",
        "/api/ingredients",
        "/api/ingredients/{ingredient_id}",
        "/api/ingredients/{ingredient_id}/restore",
        "/api/state",
        "/api/pantry-stocks",
        "/api/pantry-stocks/{ingredient_id}/operations",
        "/api/basket",
        "/api/basket/recipes/{recipe_id}",
        "/api/grocery-lists",
        "/api/grocery-lists/from-basket",
        "/api/grocery-lists/{grocery_list_id}",
        "/api/grocery-lists/{grocery_list_id}/items",
        "/api/grocery-lists/{grocery_list_id}/items/{grocery_item_id}",
        "/api/grocery-lists/{grocery_list_id}/complete",
        "/api/grocery-lists/{grocery_list_id}/reuse-recipes",
        "/api/recipes",
        "/api/recipes/{recipe_id}",
        "/api/recipes/{recipe_id}/publish",
        "/api/recipes/{recipe_id}/cook",
        "/api/activity",
        "/api/activity/{event_id}",
        "/api/activity/{event_id}/reverse",
        "/api/imports/local-state",
    }
