from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from grocea.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "preferred_servings IS NULL OR preferred_servings BETWEEN 1 AND 1000",
            name="preferred_servings",
        ),
        CheckConstraint("measurement_system = 'metric'", name="measurement_system"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    preferred_servings: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    measurement_system: Mapped[str] = mapped_column(String(16), nullable=False, server_default="metric")
    state_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class Category(TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (
        Index(
            "uq_categories_global_normalized_name",
            "normalized_name",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index(
            "uq_categories_user_normalized_name",
            "user_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index("ix_categories_normalized_name", "normalized_name"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Ingredient(TimestampMixin, Base):
    __tablename__ = "ingredients"
    __table_args__ = (
        CheckConstraint("measurement_family IN ('mass', 'volume', 'count')", name="family"),
        Index(
            "uq_ingredients_global_normalized_name",
            "normalized_name",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index(
            "uq_ingredients_user_normalized_name",
            "user_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index("ix_ingredients_category_id", "category_id"),
        Index("ix_ingredients_normalized_name", "normalized_name"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    category_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    measurement_family: Mapped[str] = mapped_column(String(16), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PantryStock(TimestampMixin, Base):
    __tablename__ = "pantry_stocks"
    __table_args__ = (UniqueConstraint("user_id", "ingredient_id", name="uq_pantry_stocks_user_ingredient"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    ingredient_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 3), nullable=False, server_default="0.000")


class Recipe(TimestampMixin, Base):
    __tablename__ = "recipes"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published')", name="status"),
        CheckConstraint("base_servings BETWEEN 1 AND 1000", name="base_servings"),
        Index("ix_recipes_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")
    name: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    base_servings: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (
        CheckConstraint("unit IN ('mg', 'g', 'kg', 'ml', 'L', 'item')", name="unit"),
        UniqueConstraint("recipe_id", "ingredient_id", name="uq_recipe_ingredients_recipe_ingredient"),
        UniqueConstraint("recipe_id", "position", name="uq_recipe_ingredients_recipe_position"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    recipe_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ingredient_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(15, 3), nullable=True)
    quantity_input: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    unit: Mapped[str] = mapped_column(String(8), nullable=False)


class RecipeStep(Base):
    __tablename__ = "recipe_steps"
    __table_args__ = (UniqueConstraint("recipe_id", "position", name="uq_recipe_steps_recipe_position"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    recipe_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class ActivityEvent(Base):
    __tablename__ = "activity_events"
    __table_args__ = (
        CheckConstraint("event_type IN ('cooking', 'manual', 'reversal')", name="type"),
        Index("ix_activity_events_user_occurred", "user_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    recipe_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("recipes.id", ondelete="RESTRICT"), nullable=True
    )
    servings: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    reversal_of: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("activity_events.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StockChange(Base):
    __tablename__ = "stock_changes"
    __table_args__ = (UniqueConstraint("event_id", "ingredient_id", name="uq_stock_changes_event_ingredient"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("activity_events.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    ingredient_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    before: Mapped[Decimal] = mapped_column(Numeric(15, 3), nullable=False)
    delta: Mapped[Decimal] = mapped_column(Numeric(15, 3), nullable=False)
    after: Mapped[Decimal] = mapped_column(Numeric(15, 3), nullable=False)


class BasketItem(TimestampMixin, Base):
    __tablename__ = "basket_items"
    __table_args__ = (
        CheckConstraint("servings BETWEEN 1 AND 12", name="servings"),
        UniqueConstraint("user_id", "recipe_id", name="uq_basket_items_user_recipe"),
        UniqueConstraint("user_id", "position", name="uq_basket_items_user_position"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipe_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("recipes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    servings: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class GroceryList(TimestampMixin, Base):
    __tablename__ = "grocery_lists"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'completed')", name="status"),
        Index(
            "uq_grocery_lists_user_active",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_grocery_lists_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GroceryListRecipe(Base):
    __tablename__ = "grocery_list_recipes"
    __table_args__ = (
        CheckConstraint("servings BETWEEN 1 AND 12", name="servings"),
        UniqueConstraint("grocery_list_id", "recipe_snapshot_id", name="uq_grocery_list_recipes_list_recipe"),
        UniqueConstraint("grocery_list_id", "position", name="uq_grocery_list_recipes_list_position"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    grocery_list_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("grocery_lists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipe_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True
    )
    recipe_snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    recipe_name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    servings: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    base_servings: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class GroceryListItem(TimestampMixin, Base):
    __tablename__ = "grocery_list_items"
    __table_args__ = (
        CheckConstraint("origin IN ('generated', 'manual')", name="origin"),
        CheckConstraint(
            "measurement_family IS NULL OR measurement_family IN ('mass', 'volume', 'count')",
            name="family",
        ),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="positive_quantity"),
        Index("ix_grocery_list_items_list", "grocery_list_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    grocery_list_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("grocery_lists.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("ingredients.id", ondelete="SET NULL"), nullable=True
    )
    original_ingredient_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    category_name: Mapped[str] = mapped_column(String(120), nullable=False, server_default="Other")
    measurement_family: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(15, 3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    checked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    original_required: Mapped[Decimal | None] = mapped_column(Numeric(15, 3), nullable=True)
    original_pantry: Mapped[Decimal | None] = mapped_column(Numeric(15, 3), nullable=True)
    original_quantity: Mapped[Decimal | None] = mapped_column(Numeric(15, 3), nullable=True)


class GroceryListItemSource(Base):
    __tablename__ = "grocery_list_item_sources"

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    grocery_list_item_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("grocery_list_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipe_snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    recipe_name: Mapped[str] = mapped_column(String(120), nullable=False)
    servings: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(8), nullable=False)


class ProcessedMutation(Base):
    __tablename__ = "processed_mutations"
    __table_args__ = (UniqueConstraint("device_id", "mutation_id", name="uq_processed_mutations_device_mutation"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    device_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    mutation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    mutation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    response_body: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    imported: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
