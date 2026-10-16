"""Hashed password reset tokens and shared recovery throttles."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261016_0006"
down_revision = "20261016_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="ck_password_reset_tokens_hash_length"),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index("ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"])
    op.create_table(
        "recovery_throttles",
        sa.Column("action", sa.String(32), primary_key=True),
        sa.Column("subject_hash", sa.LargeBinary(32), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("count", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recovery_throttles_expires_at", "recovery_throttles", ["expires_at"])


def downgrade() -> None:
    op.drop_table("recovery_throttles")
    op.drop_table("password_reset_tokens")
