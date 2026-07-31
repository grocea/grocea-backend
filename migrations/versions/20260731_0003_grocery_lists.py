"""Add recipe baskets and grocery lists."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0003"
down_revision: str | None = "20260726_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "basket_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("servings", sa.SmallInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("servings BETWEEN 1 AND 12", name="ck_basket_items_servings"),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipes.id"], name="fk_basket_items_recipe_id_recipes", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_basket_items_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_basket_items"),
        sa.UniqueConstraint("user_id", "recipe_id", name="uq_basket_items_user_recipe"),
        sa.UniqueConstraint("user_id", "position", name="uq_basket_items_user_position"),
    )
    op.create_index("ix_basket_items_recipe_id", "basket_items", ["recipe_id"])
    op.create_index("ix_basket_items_user_id", "basket_items", ["user_id"])

    op.create_table(
        "grocery_lists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'completed')", name="ck_grocery_lists_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_grocery_lists_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_grocery_lists"),
    )
    op.create_index("ix_grocery_lists_user_created", "grocery_lists", ["user_id", "created_at"])
    op.create_index(
        "uq_grocery_lists_user_active",
        "grocery_lists",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "grocery_list_recipes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grocery_list_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recipe_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipe_name", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("servings", sa.SmallInteger(), nullable=False),
        sa.Column("base_servings", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("servings BETWEEN 1 AND 12", name="ck_grocery_list_recipes_servings"),
        sa.ForeignKeyConstraint(
            ["grocery_list_id"],
            ["grocery_lists.id"],
            name="fk_grocery_list_recipes_grocery_list_id_grocery_lists",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipes.id"], name="fk_grocery_list_recipes_recipe_id_recipes", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_grocery_list_recipes"),
        sa.UniqueConstraint("grocery_list_id", "recipe_snapshot_id", name="uq_grocery_list_recipes_list_recipe"),
        sa.UniqueConstraint("grocery_list_id", "position", name="uq_grocery_list_recipes_list_position"),
    )
    op.create_index("ix_grocery_list_recipes_grocery_list_id", "grocery_list_recipes", ["grocery_list_id"])

    op.create_table(
        "grocery_list_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grocery_list_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingredient_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("original_ingredient_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("category_name", sa.String(120), server_default="Other", nullable=False),
        sa.Column("measurement_family", sa.String(16), nullable=True),
        sa.Column("quantity", sa.Numeric(15, 3), nullable=True),
        sa.Column("unit", sa.String(40), nullable=True),
        sa.Column("checked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("edited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("original_required", sa.Numeric(15, 3), nullable=True),
        sa.Column("original_pantry", sa.Numeric(15, 3), nullable=True),
        sa.Column("original_quantity", sa.Numeric(15, 3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("origin IN ('generated', 'manual')", name="ck_grocery_list_items_origin"),
        sa.CheckConstraint(
            "measurement_family IS NULL OR measurement_family IN ('mass', 'volume', 'count')",
            name="ck_grocery_list_items_family",
        ),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_grocery_list_items_positive_quantity"),
        sa.ForeignKeyConstraint(
            ["grocery_list_id"],
            ["grocery_lists.id"],
            name="fk_grocery_list_items_grocery_list_id_grocery_lists",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name="fk_grocery_list_items_ingredient_id_ingredients",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_grocery_list_items"),
    )
    op.create_index("ix_grocery_list_items_list", "grocery_list_items", ["grocery_list_id"])

    op.create_table(
        "grocery_list_item_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grocery_list_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipe_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipe_name", sa.String(120), nullable=False),
        sa.Column("servings", sa.SmallInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 3), nullable=False),
        sa.Column("unit", sa.String(8), nullable=False),
        sa.ForeignKeyConstraint(
            ["grocery_list_item_id"],
            ["grocery_list_items.id"],
            name="fk_grocery_item_sources_item",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_grocery_list_item_sources"),
    )
    op.create_index(
        "ix_grocery_list_item_sources_grocery_list_item_id", "grocery_list_item_sources", ["grocery_list_item_id"]
    )


def downgrade() -> None:
    op.drop_table("grocery_list_item_sources")
    op.drop_table("grocery_list_items")
    op.drop_table("grocery_list_recipes")
    op.drop_table("grocery_lists")
    op.drop_table("basket_items")
