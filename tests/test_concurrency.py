from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from grocea.auth import create_account
from grocea.constants import LOCAL_USER_ID
from grocea.errors import DomainError
from grocea.models import Category, Ingredient, User
from grocea.normalization import normalize_name
from grocea.schemas import IngredientCreate, MeasurementFamily
from grocea.services import create_ingredient


def test_concurrent_duplicate_ingredient_creates_only_one_row(test_engine: Engine) -> None:
    name = f"Concurrent ingredient {uuid4()}"
    barrier = Barrier(2)
    created_user = False
    with Session(test_engine) as session:
        user = session.get(User, LOCAL_USER_ID)
        if user is None:
            user, _issued = create_account(
                session,
                email=f"concurrency-{uuid4()}@example.com",
                password="correct horse battery staple",
                display_name="Concurrency test",
                user_id=LOCAL_USER_ID,
            )
            session.commit()
            created_user = True
        category_id = session.scalar(select(Category.id).where(Category.name == "Pantry"))
        assert category_id is not None

    def create() -> str:
        with Session(test_engine) as session:
            user = session.get(User, LOCAL_USER_ID)
            assert user is not None
            barrier.wait()
            try:
                create_ingredient(
                    session,
                    user,
                    IngredientCreate(
                        name=name,
                        category_id=category_id,
                        measurement_family=MeasurementFamily.MASS,
                    ),
                )
                session.commit()
            except DomainError as exc:
                return exc.code
            return "CREATED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))

    assert sorted(results) == ["CREATED", "INGREDIENT_NAME_EXISTS"]
    with Session(test_engine) as session:
        count = session.scalar(
            select(func.count()).select_from(Ingredient).where(Ingredient.normalized_name == normalize_name(name))
        )
        assert count == 1
        session.execute(delete(Ingredient).where(Ingredient.normalized_name == normalize_name(name)))
        if created_user:
            session.delete(session.get(User, LOCAL_USER_ID))
        session.commit()
