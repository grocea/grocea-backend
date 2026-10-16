"""Optional per-account pantry restock preferences.

Downgrade discards preferences only; stock and immutable history are retained.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0005"
down_revision = "20260801_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pantry_stocks", sa.Column("low_stock_threshold", sa.Numeric(15, 3), nullable=True))
    op.add_column("pantry_stocks", sa.Column("refill_quantity", sa.Numeric(15, 3), nullable=True))
    op.create_check_constraint("ck_pantry_stocks_low_stock_threshold_nonnegative", "pantry_stocks", "low_stock_threshold >= 0")
    op.create_check_constraint("ck_pantry_stocks_refill_quantity_nonnegative", "pantry_stocks", "refill_quantity >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_pantry_stocks_refill_quantity_nonnegative", "pantry_stocks", type_="check")
    op.drop_constraint("ck_pantry_stocks_low_stock_threshold_nonnegative", "pantry_stocks", type_="check")
    op.drop_column("pantry_stocks", "refill_quantity")
    op.drop_column("pantry_stocks", "low_stock_threshold")
