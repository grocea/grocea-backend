"""Add full offline-first PWA domain."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260726_0002"
down_revision: str | None = "20260717_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("state_revision", sa.BigInteger(), server_default="0", nullable=False))
    op.create_table(
        "recipes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), server_default="draft", nullable=False),
        sa.Column("name", sa.String(120), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("base_servings", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_recipes_status"),
        sa.CheckConstraint("base_servings BETWEEN 1 AND 1000", name="ck_recipes_base_servings"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_recipes_user_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_recipes"),
    )
    op.create_index("ix_recipes_user_status", "recipes", ["user_id", "status"])
    op.create_table(
        "recipe_ingredients",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingredient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 3), nullable=True),
        sa.Column("quantity_input", sa.String(64), server_default="", nullable=False),
        sa.Column("unit", sa.String(8), nullable=False),
        sa.CheckConstraint("unit IN ('mg', 'g', 'kg', 'ml', 'L', 'item')", name="ck_recipe_ingredients_unit"),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipes.id"], name="fk_recipe_ingredients_recipe_id_recipes", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name="fk_recipe_ingredients_ingredient_id_ingredients",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recipe_ingredients"),
        sa.UniqueConstraint("recipe_id", "ingredient_id", name="uq_recipe_ingredients_recipe_ingredient"),
        sa.UniqueConstraint("recipe_id", "position", name="uq_recipe_ingredients_recipe_position"),
    )
    op.create_index("ix_recipe_ingredients_recipe_id", "recipe_ingredients", ["recipe_id"])
    op.create_index("ix_recipe_ingredients_ingredient_id", "recipe_ingredients", ["ingredient_id"])
    op.create_table(
        "recipe_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipes.id"], name="fk_recipe_steps_recipe_id_recipes", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recipe_steps"),
        sa.UniqueConstraint("recipe_id", "position", name="uq_recipe_steps_recipe_position"),
    )
    op.create_index("ix_recipe_steps_recipe_id", "recipe_steps", ["recipe_id"])
    op.create_table(
        "activity_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("servings", sa.SmallInteger(), nullable=True),
        sa.Column("reversal_of", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("event_type IN ('cooking', 'manual', 'reversal')", name="ck_activity_events_type"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_activity_events_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipes.id"], name="fk_activity_events_recipe_id_recipes", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of"],
            ["activity_events.id"],
            name="fk_activity_events_reversal_of_activity_events",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_activity_events"),
        sa.UniqueConstraint("reversal_of", name="uq_activity_events_reversal_of"),
    )
    op.create_index("ix_activity_events_user_occurred", "activity_events", ["user_id", "occurred_at"])
    op.create_table(
        "stock_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingredient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("before", sa.Numeric(15, 3), nullable=False),
        sa.Column("delta", sa.Numeric(15, 3), nullable=False),
        sa.Column("after", sa.Numeric(15, 3), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"], ["activity_events.id"], name="fk_stock_changes_event_id_activity_events", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name="fk_stock_changes_ingredient_id_ingredients",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stock_changes"),
        sa.UniqueConstraint("event_id", "ingredient_id", name="uq_stock_changes_event_ingredient"),
    )
    op.create_index("ix_stock_changes_event_id", "stock_changes", ["event_id"])
    op.create_index("ix_stock_changes_ingredient_id", "stock_changes", ["ingredient_id"])
    op.create_table(
        "processed_mutations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mutation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mutation_type", sa.String(80), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("imported", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_processed_mutations_user_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_processed_mutations"),
        sa.UniqueConstraint("device_id", "mutation_id", name="uq_processed_mutations_device_mutation"),
    )
    op.create_index("ix_processed_mutations_user_id", "processed_mutations", ["user_id"])


def downgrade() -> None:
    op.drop_table("processed_mutations")
    op.drop_table("stock_changes")
    op.drop_table("activity_events")
    op.drop_table("recipe_steps")
    op.drop_table("recipe_ingredients")
    op.drop_table("recipes")
    op.drop_column("users", "state_revision")
