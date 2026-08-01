from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from grocea.constants import LOCAL_USER_ID
from grocea.models import Ingredient, PantryStock


def _category_id(client: TestClient, name: str) -> str:
    response = client.get("/api/categories", params={"query": name})
    assert response.status_code == 200
    return next(item["id"] for item in response.json() if item["name"] == name)


def _global_ingredient(client: TestClient, name: str) -> dict[str, object]:
    response = client.get("/api/ingredients", params={"query": name, "scope": "global", "limit": 100})
    assert response.status_code == 200
    return next(item for item in response.json()["items"] if item["name"] == name)


def test_ingredient_catalog_filter_pagination_and_global_read_only(client: TestClient) -> None:
    page = client.get("/api/ingredients", params={"limit": 10, "offset": 0})
    assert page.status_code == 200
    assert page.json()["total"] == 150
    assert len(page.json()["items"]) == 10
    assert page.json()["limit"] == 10
    assert page.json()["offset"] == 0

    rice = client.get("/api/ingredients", params={"query": "RICE", "scope": "global", "limit": 100})
    assert rice.status_code == 200
    assert rice.json()["total"] >= 5
    assert all("rice" in item["name"].lower() for item in rice.json()["items"])

    basmati = _global_ingredient(client, "Basmati rice")
    read_only = client.patch(f"/api/ingredients/{basmati['id']}", json={"name": "Basmati"})
    assert read_only.status_code == 403
    assert read_only.json()["code"] == "GLOBAL_INGREDIENT_READ_ONLY"


def test_tracked_ingredient_is_atomic_locks_family_and_restores_same_uuid(client: TestClient) -> None:
    pantry_id = _category_id(client, "Pantry")
    created = client.post(
        "/api/ingredients",
        json={
            "name": "  Kimchi  ",
            "category_id": pantry_id,
            "measurement_family": "mass",
            "track_in_pantry": True,
        },
    )
    assert created.status_code == 201
    ingredient_id = created.json()["id"]
    assert created.json()["name"] == "Kimchi"
    assert created.json()["tracked_in_pantry"] is True

    locked = client.patch(f"/api/ingredients/{ingredient_id}", json={"measurement_family": "volume"})
    assert locked.status_code == 409
    assert locked.json()["code"] == "MEASUREMENT_FAMILY_LOCKED"

    archived = client.delete(f"/api/ingredients/{ingredient_id}")
    assert archived.status_code == 200
    assert archived.json()["tracked_in_pantry"] is True
    assert archived.json()["archived_at"] is not None

    reserved = client.post(
        "/api/ingredients",
        json={"name": "KIMCHI", "category_id": pantry_id, "measurement_family": "mass"},
    )
    assert reserved.status_code == 409
    assert reserved.json()["code"] == "INGREDIENT_NAME_ARCHIVED"
    assert reserved.json()["details"]["ingredient_id"] == ingredient_id

    restored = client.post(f"/api/ingredients/{ingredient_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["id"] == ingredient_id
    assert restored.json()["archived_at"] is None


def test_untracked_family_can_change_and_nonzero_stock_blocks_archive(
    client: TestClient,
    db_session: Session,
) -> None:
    produce_id = _category_id(client, "Produce")
    untracked = client.post(
        "/api/ingredients",
        json={"name": "Young jackfruit", "category_id": produce_id, "measurement_family": "count"},
    )
    assert untracked.status_code == 201
    assert untracked.json()["tracked_in_pantry"] is False
    changed = client.patch(
        f"/api/ingredients/{untracked.json()['id']}",
        json={"measurement_family": "mass"},
    )
    assert changed.status_code == 200
    assert changed.json()["measurement_family"] == "mass"

    tracked = client.post(
        "/api/ingredients",
        json={
            "name": "Fresh noodles",
            "category_id": produce_id,
            "measurement_family": "mass",
            "track_in_pantry": True,
        },
    )
    ingredient_id = UUID(tracked.json()["id"])
    stock = db_session.scalar(
        select(PantryStock).where(
            PantryStock.user_id == LOCAL_USER_ID,
            PantryStock.ingredient_id == ingredient_id,
        )
    )
    assert stock is not None
    stock.quantity = Decimal("2.000")
    db_session.commit()

    rejected = client.delete(f"/api/ingredients/{ingredient_id}")
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "INGREDIENT_HAS_STOCK"


def test_duplicate_global_name_and_archived_filter(client: TestClient) -> None:
    pantry_id = _category_id(client, "Pantry")
    duplicate = client.post(
        "/api/ingredients",
        json={"name": " basmati RICE ", "category_id": pantry_id, "measurement_family": "mass"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "INGREDIENT_NAME_EXISTS"

    custom = client.post(
        "/api/ingredients",
        json={"name": "Archived sample", "category_id": pantry_id, "measurement_family": "mass"},
    ).json()
    client.delete(f"/api/ingredients/{custom['id']}")
    active = client.get("/api/ingredients", params={"query": "Archived sample"}).json()
    archived = client.get(
        "/api/ingredients",
        params={"query": "Archived sample", "include_archived": True},
    ).json()
    assert active["total"] == 0
    assert archived["total"] == 1
    assert archived["items"][0]["id"] == custom["id"]


def test_tracking_creates_exactly_one_stock_row(client: TestClient, db_session: Session) -> None:
    pantry_id = _category_id(client, "Pantry")
    created = client.post(
        "/api/ingredients",
        json={
            "name": "Single stock row",
            "category_id": pantry_id,
            "measurement_family": "mass",
            "track_in_pantry": True,
        },
    )
    assert created.status_code == 201
    ingredient_id = UUID(created.json()["id"])
    rows = db_session.scalars(select(PantryStock).where(PantryStock.ingredient_id == ingredient_id)).all()
    assert len(rows) == 1
    assert rows[0].quantity == Decimal("0.000")
    ingredient = db_session.get(Ingredient, ingredient_id)
    assert ingredient is not None
