from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from grocea.constants import LOCAL_USER_ID
from grocea.models import Category, Ingredient, Recipe, RecipeIngredient, RecipeStep, User


def test_health_and_profile_contract(client: TestClient) -> None:
    live = client.get("/api/health/live")
    ready = client.get("/api/health/ready")
    profile = client.get("/api/profile")

    assert live.status_code == 200
    assert ready.status_code == 200
    assert profile.status_code == 200
    assert profile.json()["id"] == str(LOCAL_USER_ID)
    assert profile.json()["measurement_system"] == "metric"
    assert profile.json()["preferred_servings"] == 2
    assert profile.json()["created_at"].endswith("Z")
    UUID(profile.headers["X-Request-ID"])


def test_profile_patch_supports_trimmed_name_and_null_preference(client: TestClient) -> None:
    response = client.patch(
        "/api/profile",
        json={"display_name": "  Grocie Crumbsworth Jr  ", "preferred_servings": None},
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Grocie Crumbsworth Jr"
    assert response.json()["preferred_servings"] is None


def test_validation_errors_use_stable_shape(client: TestClient) -> None:
    response = client.patch("/api/profile", json={"preferred_servings": 0})

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["message"] == "Request validation failed."
    assert payload["details"]["fields"][0]["field"] == "preferred_servings"
    assert payload["request_id"] == response.headers["X-Request-ID"]
    UUID(payload["request_id"])


def test_readiness_reports_missing_seed(client: TestClient, db_session: Session) -> None:
    db_session.execute(delete(RecipeIngredient))
    db_session.execute(delete(RecipeStep))
    db_session.execute(delete(Recipe))
    db_session.execute(delete(Ingredient))
    db_session.execute(delete(Category))
    db_session.execute(delete(User))
    db_session.commit()

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["code"] == "GLOBAL_CATALOG_NOT_SEEDED"
