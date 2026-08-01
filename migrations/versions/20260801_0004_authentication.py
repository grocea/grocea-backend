"""Add personal-account authentication and user-scoped mutation replay."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0004"
down_revision: str | None = "20260731_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(length=320), nullable=True))
    op.add_column("users", sa.Column("normalized_email", sa.String(length=320), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_users_auth_fields_complete",
        "users",
        "(email IS NULL AND normalized_email IS NULL AND password_hash IS NULL) OR "
        "(email IS NOT NULL AND normalized_email IS NOT NULL AND password_hash IS NOT NULL)",
    )
    op.create_index(
        "uq_users_normalized_email",
        "users",
        ["normalized_email"],
        unique=True,
        postgresql_where=sa.text("normalized_email IS NOT NULL"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("csrf_token", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_sessions_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.drop_constraint("uq_processed_mutations_device_mutation", "processed_mutations", type_="unique")
    op.create_unique_constraint(
        "uq_processed_mutations_user_device_mutation",
        "processed_mutations",
        ["user_id", "device_id", "mutation_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_processed_mutations_user_device_mutation", "processed_mutations", type_="unique")
    op.create_unique_constraint(
        "uq_processed_mutations_device_mutation",
        "processed_mutations",
        ["device_id", "mutation_id"],
    )
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("uq_users_normalized_email", table_name="users")
    op.drop_constraint("ck_users_auth_fields_complete", "users", type_="check")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "normalized_email")
    op.drop_column("users", "email")
