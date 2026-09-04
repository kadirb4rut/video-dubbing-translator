"""Track credit reversals for refunded Stripe purchases."""

import sqlalchemy as sa
from alembic import op


revision = "0009_billing_refunds"
down_revision = "0008_stripe_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("credit_purchases", sa.Column("refunded_credits", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("credit_purchases", "refunded_credits")
