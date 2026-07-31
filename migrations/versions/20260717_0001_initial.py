"""Create first vertical slice tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260717_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("preferred_servings", sa.SmallInteger(), nullable=True),
        sa.Column("measurement_system", sa.String(length=16), server_default="metric", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "preferred_servings IS NULL OR preferred_servings BETWEEN 1 AND 1000",
            name="ck_users_preferred_servings",
        ),
        sa.CheckConstraint("measurement_system = 'metric'", name="ck_users_measurement_system"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_categories_user_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
    )
    op.create_index(
        "uq_categories_global_normalized_name",
        "categories",
        ["normalized_name"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_index(
        "uq_categories_user_normalized_name",
        "categories",
        ["user_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index("ix_categories_normalized_name", "categories", ["normalized_name"], unique=False)

    op.create_table(
        "ingredients",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("measurement_family", sa.String(length=16), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("measurement_family IN ('mass', 'volume', 'count')", name="ck_ingredients_family"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name="fk_ingredients_category_id_categories", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_ingredients_user_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_ingredients"),
    )
    op.create_index(
        "uq_ingredients_global_normalized_name",
        "ingredients",
        ["normalized_name"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_index(
        "uq_ingredients_user_normalized_name",
        "ingredients",
        ["user_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index("ix_ingredients_category_id", "ingredients", ["category_id"], unique=False)
    op.create_index("ix_ingredients_normalized_name", "ingredients", ["normalized_name"], unique=False)

    op.create_table(
        "pantry_stocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingredient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=15, scale=3), server_default="0.000", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name="fk_pantry_stocks_ingredient_id_ingredients",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_pantry_stocks_user_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_pantry_stocks"),
        sa.UniqueConstraint("user_id", "ingredient_id", name="uq_pantry_stocks_user_ingredient"),
    )
    op.create_index("ix_pantry_stocks_ingredient_id", "pantry_stocks", ["ingredient_id"], unique=False)
    op.create_index("ix_pantry_stocks_user_id", "pantry_stocks", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_pantry_stocks_user_id", table_name="pantry_stocks")
    op.drop_index("ix_pantry_stocks_ingredient_id", table_name="pantry_stocks")
    op.drop_table("pantry_stocks")
    op.drop_index("ix_ingredients_normalized_name", table_name="ingredients")
    op.drop_index("ix_ingredients_category_id", table_name="ingredients")
    op.drop_index("uq_ingredients_user_normalized_name", table_name="ingredients")
    op.drop_index("uq_ingredients_global_normalized_name", table_name="ingredients")
    op.drop_table("ingredients")
    op.drop_index("ix_categories_normalized_name", table_name="categories")
    op.drop_index("uq_categories_user_normalized_name", table_name="categories")
    op.drop_index("uq_categories_global_normalized_name", table_name="categories")
    op.drop_table("categories")
    op.drop_table("users")
