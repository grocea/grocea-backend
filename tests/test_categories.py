from __future__ import annotations

from fastapi.testclient import TestClient


def _category(client: TestClient, name: str) -> dict[str, object]:
    response = client.get("/api/categories", params={"query": name})
    assert response.status_code == 200
    return next(item for item in response.json() if item["name"] == name)


def test_category_create_duplicate_archive_visibility_and_restore(client: TestClient) -> None:
    created = client.post("/api/categories", json={"name": "  Ferments  "})
    assert created.status_code == 201
    category_id = created.json()["id"]
    assert created.json()["name"] == "Ferments"
    assert created.json()["scope"] == "custom"

    duplicate = client.post("/api/categories", json={"name": "ferments"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "CATEGORY_NAME_EXISTS"

    archived = client.delete(f"/api/categories/{category_id}")
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert not any(item["id"] == category_id for item in client.get("/api/categories").json())
    assert any(
        item["id"] == category_id for item in client.get("/api/categories", params={"include_archived": True}).json()
    )

    reserved = client.post("/api/categories", json={"name": "FERMENTS"})
    assert reserved.status_code == 409
    assert reserved.json()["code"] == "CATEGORY_NAME_ARCHIVED"
    assert reserved.json()["details"]["category_id"] == category_id

    restored = client.post(f"/api/categories/{category_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["id"] == category_id
    assert restored.json()["archived_at"] is None


def test_category_archive_rejects_active_ingredient_and_global_mutation(client: TestClient) -> None:
    category = client.post("/api/categories", json={"name": "Homegrown"}).json()
    ingredient = client.post(
        "/api/ingredients",
        json={
            "name": "Garden herb",
            "category_id": category["id"],
            "measurement_family": "mass",
        },
    )
    assert ingredient.status_code == 201

    in_use = client.delete(f"/api/categories/{category['id']}")
    assert in_use.status_code == 409
    assert in_use.json()["code"] == "CATEGORY_IN_USE"

    produce = _category(client, "Produce")
    read_only = client.patch(f"/api/categories/{produce['id']}", json={"name": "Fresh Produce"})
    assert read_only.status_code == 403
    assert read_only.json()["code"] == "GLOBAL_CATEGORY_READ_ONLY"
