from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient


def _ingredient_id(client: TestClient, name: str) -> str:
    response = client.get("/api/ingredients", params={"query": name, "limit": 100})
    return str(next(item["id"] for item in response.json()["items"] if item["name"] == name))


def test_recipe_stock_cook_reverse_and_idempotency(client: TestClient) -> None:
    rice_id = _ingredient_id(client, "Basmati rice")
    stock_event_id = str(uuid4())
    stock_mutation_id = str(uuid4())
    stock_payload = {"event_id": stock_event_id, "operation": "set", "amount": "1000.000", "reason": "Test"}
    headers = {"Idempotency-Key": stock_mutation_id}

    first = client.post(f"/api/pantry-stocks/{rice_id}/operations", json=stock_payload, headers=headers)
    replay = client.post(f"/api/pantry-stocks/{rice_id}/operations", json=stock_payload, headers=headers)
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.headers["X-Idempotent-Replay"] == "true"
    assert replay.json() == first.json()

    recipe_id = str(uuid4())
    created = client.post(
        "/api/recipes",
        json={
            "id": recipe_id,
            "name": "Offline rice",
            "description": "",
            "base_servings": 2,
            "ingredients": [{"ingredient_id": rice_id, "quantity": "300", "unit": "g"}],
            "steps": ["Cook it"],
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "draft"
    published = client.post(f"/api/recipes/{recipe_id}/publish", json={})
    assert published.status_code == 200
    assert published.json()["ingredients"][0]["quantity"] == "300.000"

    cooking_id = str(uuid4())
    cooked = client.post(
        f"/api/recipes/{recipe_id}/cook",
        json={"event_id": cooking_id, "servings": 10},
    )
    assert cooked.status_code == 200
    assert cooked.json()["changes"][0] == {
        "ingredient_id": rice_id,
        "before": "1000.000",
        "delta": "-1500.000",
        "after": "-500.000",
    }

    reversal_id = str(uuid4())
    reversed_event = client.post(
        f"/api/activity/{cooking_id}/reverse",
        json={"event_id": reversal_id},
    )
    assert reversed_event.status_code == 200
    assert reversed_event.json()["changes"][0]["after"] == "1000.000"
    assert client.post(f"/api/activity/{cooking_id}/reverse", json={"event_id": str(uuid4())}).status_code == 409

    state = client.get("/api/state")
    assert state.status_code == 200
    assert state.json()["revision"] >= 5
    assert (
        next(stock["quantity"] for stock in state.json()["pantry_stocks"] if stock["ingredient_id"] == rice_id)
        == "1000.000"
    )


def test_import_maps_legacy_ids_and_is_idempotent(client: TestClient) -> None:
    import_id = str(uuid4())
    grocery_list_id = str(uuid4())
    grocery_item_id = str(uuid4())
    payload = {
        "import_id": import_id,
        "state": {
            "profile": {"displayName": "Imported", "preferredServings": 4},
            "categories": [{"id": "pantry", "name": "Pantry staples", "scope": "global"}],
            "ingredients": [
                {
                    "id": "rice",
                    "name": "Basmati rice",
                    "categoryId": "pantry",
                    "family": "mass",
                    "scope": "global",
                }
            ],
            "balances": {"rice": "2400000"},
            "recipes": [
                {
                    "id": "legacy-recipe",
                    "status": "published",
                    "scope": "custom",
                    "name": "Imported rice bowl",
                    "description": "",
                    "baseServings": 2,
                    "ingredients": [{"ingredientId": "rice", "quantity": "300000", "unit": "g"}],
                    "steps": ["Cook"],
                }
            ],
            "activity": [
                {
                    "id": "legacy-event",
                    "type": "manual",
                    "title": "Added rice",
                    "detail": "Imported",
                    "occurredAt": "2026-07-17T09:15:00+08:00",
                    "changes": [{"ingredientId": "rice", "before": "0", "delta": "2400000", "after": "2400000"}],
                }
            ],
            "basket": [
                {
                    "recipeId": "legacy-recipe",
                    "recipeName": "Imported rice bowl",
                    "servings": 3,
                    "baseServings": 2,
                    "valid": True,
                }
            ],
            "groceryLists": [
                {
                    "id": grocery_list_id,
                    "title": "Imported groceries",
                    "status": "completed",
                    "recipes": [
                        {
                            "recipeId": "legacy-recipe",
                            "recipeName": "Imported rice bowl",
                            "servings": 2,
                            "baseServings": 2,
                        }
                    ],
                    "items": [
                        {
                            "id": grocery_item_id,
                            "ingredientId": "rice",
                            "label": "Basmati rice",
                            "categoryName": "Pantry",
                            "family": "mass",
                            "quantity": "300000",
                            "unit": "g",
                            "checked": True,
                            "origin": "generated",
                            "edited": False,
                            "originalRequired": "300000",
                            "originalPantry": "0",
                            "originalQuantity": "300000",
                            "sources": [
                                {
                                    "recipeId": "legacy-recipe",
                                    "recipeName": "Imported rice bowl",
                                    "servings": 2,
                                    "quantity": "300000",
                                    "unit": "g",
                                }
                            ],
                            "createdAt": "2026-07-31T10:00:00+08:00",
                            "updatedAt": "2026-07-31T10:00:00+08:00",
                        }
                    ],
                    "createdAt": "2026-07-31T10:00:00+08:00",
                    "updatedAt": "2026-07-31T11:00:00+08:00",
                    "completedAt": "2026-07-31T11:00:00+08:00",
                }
            ],
        },
    }
    headers = {"Idempotency-Key": import_id}
    first = client.post("/api/imports/local-state", json=payload, headers=headers)
    replay = client.post("/api/imports/local-state", json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json()["conflicts"] == []
    assert first.json()["id_map"]["rice"] == _ingredient_id(client, "Basmati rice")
    assert replay.json() == first.json()
    assert replay.headers["X-Idempotent-Replay"] == "true"
    state = client.get("/api/state").json()
    rice_id = first.json()["id_map"]["rice"]
    assert (
        next(stock["quantity"] for stock in state["pantry_stocks"] if stock["ingredient_id"] == rice_id) == "2400.000"
    )
    assert any(recipe["name"] == "Imported rice bowl" for recipe in state["recipes"])
    assert any(event["title"] == "Added rice" for event in state["activity"])
    imported_recipe_id = first.json()["id_map"]["legacy-recipe"]
    assert state["basket"]["items"][0] == {
        "recipe_id": imported_recipe_id,
        "recipe_name": "Imported rice bowl",
        "servings": 3,
        "base_servings": 2,
        "valid": True,
        "error": None,
    }
    assert state["grocery_lists"][0]["id"] == grocery_list_id
    assert state["grocery_lists"][0]["items"][0]["ingredient_id"] == rice_id
