from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient


def _published_recipe(client: TestClient) -> dict[str, Any]:
    recipes = client.get("/api/recipes").json()
    return next(recipe for recipe in recipes if recipe["status"] == "published")


def test_basket_persists_one_adjustable_row_per_published_recipe(client: TestClient) -> None:
    recipe = _published_recipe(client)
    recipe_id = recipe["id"]

    added = client.put(f"/api/basket/recipes/{recipe_id}", json={"servings": 2})
    assert added.status_code == 200
    assert added.json()["items"] == [
        {
            "recipe_id": recipe_id,
            "recipe_name": recipe["name"],
            "servings": 2,
            "base_servings": recipe["base_servings"],
            "valid": True,
            "error": None,
        }
    ]

    updated = client.put(f"/api/basket/recipes/{recipe_id}", json={"servings": 5})
    assert updated.status_code == 200
    assert len(updated.json()["items"]) == 1
    assert updated.json()["items"][0]["servings"] == 5

    assert client.put(f"/api/basket/recipes/{recipe_id}", json={"servings": 13}).status_code == 422

    state = client.get("/api/state")
    assert state.status_code == 200
    assert state.json()["basket"] == updated.json()

    removed = client.delete(f"/api/basket/recipes/{recipe_id}")
    assert removed.status_code == 200
    assert removed.json() == {"items": []}


def _create_published_recipe(
    client: TestClient,
    *,
    name: str,
    ingredient_id: str,
    quantity: str,
    unit: str,
    base_servings: int,
) -> str:
    recipe_id = str(uuid4())
    created = client.post(
        "/api/recipes",
        json={
            "id": recipe_id,
            "name": name,
            "description": "",
            "base_servings": base_servings,
            "ingredients": [{"ingredient_id": ingredient_id, "quantity": quantity, "unit": unit}],
            "steps": ["Cook"],
        },
    )
    assert created.status_code == 201
    assert client.post(f"/api/recipes/{recipe_id}/publish", json={}).status_code == 200
    return recipe_id


def test_confirm_basket_aggregates_requirements_subtracts_signed_pantry_and_freezes_sources(
    client: TestClient,
) -> None:
    rice_id = client.get("/api/ingredients", params={"query": "Basmati rice", "limit": 100}).json()["items"][0]["id"]
    first_id = _create_published_recipe(
        client,
        name="Rice one",
        ingredient_id=rice_id,
        quantity="300",
        unit="g",
        base_servings=2,
    )
    second_id = _create_published_recipe(
        client,
        name="Rice two",
        ingredient_id=rice_id,
        quantity="0.5",
        unit="kg",
        base_servings=4,
    )
    stock = client.post(
        f"/api/pantry-stocks/{rice_id}/operations",
        json={"event_id": str(uuid4()), "operation": "set", "amount": "-100", "reason": "Existing deficit"},
    )
    assert stock.status_code == 201
    assert client.put(f"/api/basket/recipes/{first_id}", json={"servings": 2}).status_code == 200
    assert client.put(f"/api/basket/recipes/{second_id}", json={"servings": 2}).status_code == 200

    list_id = str(uuid4())
    grocery_item_id = str(uuid4())
    created = client.post(
        "/api/grocery-lists/from-basket",
        json={"id": list_id, "generated_item_ids": [{"ingredient_id": rice_id, "id": grocery_item_id}]},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["id"] == list_id
    assert body["status"] == "active"
    assert body["title"] == "Groceries — Rice one + 1"
    assert [(recipe["recipe_name"], recipe["servings"]) for recipe in body["recipes"]] == [
        ("Rice one", 2),
        ("Rice two", 2),
    ]
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == grocery_item_id
    assert item["ingredient_id"] == rice_id
    assert item["label"] == "Basmati rice"
    assert item["quantity"] == "650.000"
    assert item["unit"] == "g"
    assert item["original_required"] == "550.000"
    assert item["original_pantry"] == "-100.000"
    assert item["original_quantity"] == "650.000"
    assert [(source["recipe_name"], source["quantity"]) for source in item["sources"]] == [
        ("Rice one", "300.000"),
        ("Rice two", "250.000"),
    ]
    state = client.get("/api/state").json()
    assert state["basket"] == {"items": []}
    assert state["grocery_lists"][0] == body


def test_active_list_supports_manual_items_and_selected_checked_items_update_pantry_on_completion(
    client: TestClient,
) -> None:
    rice_id = client.get("/api/ingredients", params={"query": "Basmati rice", "limit": 100}).json()["items"][0]["id"]
    recipe_id = _create_published_recipe(
        client,
        name="Shopping rice",
        ingredient_id=rice_id,
        quantity="300",
        unit="g",
        base_servings=2,
    )
    assert client.put(f"/api/basket/recipes/{recipe_id}", json={"servings": 2}).status_code == 200
    grocery_list = client.post("/api/grocery-lists/from-basket", json={"id": str(uuid4())}).json()
    generated = grocery_list["items"][0]

    changed = client.put(
        f"/api/grocery-lists/{grocery_list['id']}/items/{generated['id']}",
        json={
            "ingredient_id": rice_id,
            "label": "Basmati rice",
            "quantity": "350",
            "unit": "g",
            "checked": True,
        },
    )
    assert changed.status_code == 200
    edited = changed.json()["items"][0]
    assert edited["quantity"] == "350.000"
    assert edited["checked"] is True
    assert edited["edited"] is True
    assert edited["original_quantity"] == "300.000"

    custom_id = str(uuid4())
    custom = client.post(
        f"/api/grocery-lists/{grocery_list['id']}/items",
        json={
            "id": custom_id,
            "ingredient_id": None,
            "label": "Dish soap",
            "quantity": "2",
            "unit": "bottles",
        },
    )
    assert custom.status_code == 201
    custom_item = next(item for item in custom.json()["items"] if item["id"] == custom_id)
    assert custom_item["origin"] == "manual"
    assert custom_item["quantity"] == "2.000"
    assert custom_item["unit"] == "bottles"

    completed = client.post(
        f"/api/grocery-lists/{grocery_list['id']}/complete",
        json={"event_id": str(uuid4()), "pantry_item_ids": [generated["id"]]},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    state = client.get("/api/state").json()
    assert next(stock["quantity"] for stock in state["pantry_stocks"] if stock["ingredient_id"] == rice_id) == "350.000"
    assert state["activity"][0]["title"] == "Groceries added to pantry"


def test_completion_combines_duplicate_catalog_items_into_one_pantry_change(client: TestClient) -> None:
    rice_id = client.get("/api/ingredients", params={"query": "Basmati rice", "limit": 100}).json()["items"][0]["id"]
    recipe_id = _create_published_recipe(
        client,
        name="Duplicate shopping rice",
        ingredient_id=rice_id,
        quantity="300",
        unit="g",
        base_servings=2,
    )
    client.put(f"/api/basket/recipes/{recipe_id}", json={"servings": 2})
    grocery_list = client.post("/api/grocery-lists/from-basket", json={"id": str(uuid4())}).json()
    generated = grocery_list["items"][0]
    client.put(
        f"/api/grocery-lists/{grocery_list['id']}/items/{generated['id']}",
        json={"ingredient_id": rice_id, "label": "Basmati rice", "quantity": "300", "unit": "g", "checked": True},
    )
    manual_id = str(uuid4())
    client.post(
        f"/api/grocery-lists/{grocery_list['id']}/items",
        json={"id": manual_id, "ingredient_id": rice_id, "label": "Basmati rice", "quantity": "100", "unit": "g"},
    )
    client.put(
        f"/api/grocery-lists/{grocery_list['id']}/items/{manual_id}",
        json={"ingredient_id": rice_id, "label": "Basmati rice", "quantity": "100", "unit": "g", "checked": True},
    )

    completed = client.post(
        f"/api/grocery-lists/{grocery_list['id']}/complete",
        json={"event_id": str(uuid4()), "pantry_item_ids": [generated["id"], manual_id]},
    )

    assert completed.status_code == 200
    state = client.get("/api/state").json()
    assert next(stock["quantity"] for stock in state["pantry_stocks"] if stock["ingredient_id"] == rice_id) == "400.000"
    assert state["activity"][0]["changes"] == [
        {"ingredient_id": rice_id, "before": "0.000", "delta": "400.000", "after": "400.000"}
    ]


def test_completion_aggregates_duplicate_catalog_rows_into_one_pantry_change(client: TestClient) -> None:
    rice_id = client.get("/api/ingredients", params={"query": "Basmati rice", "limit": 100}).json()["items"][0]["id"]
    recipe_id = _create_published_recipe(
        client,
        name="Duplicate rice",
        ingredient_id=rice_id,
        quantity="300",
        unit="g",
        base_servings=2,
    )
    client.put(f"/api/basket/recipes/{recipe_id}", json={"servings": 2})
    grocery_list = client.post("/api/grocery-lists/from-basket", json={"id": str(uuid4())}).json()
    generated = grocery_list["items"][0]
    client.put(
        f"/api/grocery-lists/{grocery_list['id']}/items/{generated['id']}",
        json={"ingredient_id": rice_id, "label": "Basmati rice", "quantity": "300", "unit": "g", "checked": True},
    )
    manual_id = str(uuid4())
    client.post(
        f"/api/grocery-lists/{grocery_list['id']}/items",
        json={"id": manual_id, "ingredient_id": rice_id, "label": "Basmati rice", "quantity": "100", "unit": "g"},
    )
    client.put(
        f"/api/grocery-lists/{grocery_list['id']}/items/{manual_id}",
        json={"ingredient_id": rice_id, "label": "Basmati rice", "quantity": "100", "unit": "g", "checked": True},
    )

    completed = client.post(
        f"/api/grocery-lists/{grocery_list['id']}/complete",
        json={"event_id": str(uuid4()), "pantry_item_ids": [generated["id"], manual_id]},
    )

    assert completed.status_code == 200
    state = client.get("/api/state").json()
    assert next(stock["quantity"] for stock in state["pantry_stocks"] if stock["ingredient_id"] == rice_id) == "400.000"
    assert state["activity"][0]["changes"] == [
        {
            "ingredient_id": rice_id,
            "before": "0.000",
            "delta": "400.000",
            "after": "400.000",
        }
    ]


def test_grocery_list_lifecycle_enforces_one_active_and_reuses_frozen_recipes(client: TestClient) -> None:
    rice_id = client.get("/api/ingredients", params={"query": "Basmati rice", "limit": 100}).json()["items"][0]["id"]
    recipe_id = _create_published_recipe(
        client,
        name="Reusable rice",
        ingredient_id=rice_id,
        quantity="300",
        unit="g",
        base_servings=2,
    )
    client.put(f"/api/basket/recipes/{recipe_id}", json={"servings": 2})
    grocery_list = client.post("/api/grocery-lists/from-basket", json={"id": str(uuid4())}).json()

    renamed = client.patch(
        f"/api/grocery-lists/{grocery_list['id']}",
        json={"title": "Weekend groceries"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Weekend groceries"

    client.put(f"/api/basket/recipes/{recipe_id}", json={"servings": 5})
    blocked = client.post("/api/grocery-lists/from-basket", json={"id": str(uuid4())})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "ACTIVE_GROCERY_LIST_EXISTS"

    completed = client.post(
        f"/api/grocery-lists/{grocery_list['id']}/complete",
        json={"event_id": str(uuid4()), "pantry_item_ids": []},
    )
    assert completed.status_code == 200
    reused = client.post(f"/api/grocery-lists/{grocery_list['id']}/reuse-recipes", json={})
    assert reused.status_code == 200
    assert reused.json()["items"][0]["servings"] == 5

    next_list = client.post("/api/grocery-lists/from-basket", json={"id": str(uuid4())}).json()
    restored = client.delete(f"/api/grocery-lists/{next_list['id']}", params={"restore_recipes": True})
    assert restored.status_code == 200
    assert restored.json()["items"][0]["recipe_id"] == recipe_id
    assert restored.json()["items"][0]["servings"] == 5


def test_confirm_basket_with_fully_covered_requirements_creates_completed_history(client: TestClient) -> None:
    rice_id = client.get("/api/ingredients", params={"query": "Basmati rice", "limit": 100}).json()["items"][0]["id"]
    recipe_id = _create_published_recipe(
        client,
        name="Covered rice",
        ingredient_id=rice_id,
        quantity="300",
        unit="g",
        base_servings=2,
    )
    client.post(
        f"/api/pantry-stocks/{rice_id}/operations",
        json={"event_id": str(uuid4()), "operation": "set", "amount": "1000", "reason": "Well stocked"},
    )
    client.put(f"/api/basket/recipes/{recipe_id}", json={"servings": 2})

    created = client.post("/api/grocery-lists/from-basket", json={"id": str(uuid4())})
    assert created.status_code == 201
    assert created.json()["status"] == "completed"
    assert created.json()["items"] == []
    assert created.json()["completed_at"] is not None
    assert client.get("/api/state").json()["basket"] == {"items": []}


def test_confirmation_rejects_stale_offline_calculation_and_preserves_basket(client: TestClient) -> None:
    recipe = _published_recipe(client)
    recipe_id = recipe["id"]
    ingredient_ids = [item["ingredient_id"] for item in recipe["ingredients"]]
    client.put(f"/api/basket/recipes/{recipe_id}", json={"servings": recipe["base_servings"]})
    client.post(
        f"/api/pantry-stocks/{ingredient_ids[0]}/operations",
        json={"event_id": str(uuid4()), "operation": "set", "amount": "1", "reason": "Changed elsewhere"},
    )

    response = client.post(
        "/api/grocery-lists/from-basket",
        json={
            "id": str(uuid4()),
            "recipe_basis": [
                {
                    "recipe_id": recipe_id,
                    "base_servings": recipe["base_servings"],
                    "ingredients": [
                        {"ingredient_id": item["ingredient_id"], "quantity": item["quantity"]}
                        for item in recipe["ingredients"]
                    ],
                }
            ],
            "pantry_basis": [{"ingredient_id": ingredient_id, "quantity": "0.000"} for ingredient_id in ingredient_ids],
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "GROCERY_CALCULATION_STALE"
    assert client.get("/api/state").json()["basket"]["items"][0]["recipe_id"] == recipe_id
