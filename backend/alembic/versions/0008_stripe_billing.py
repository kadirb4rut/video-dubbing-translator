"""Add Stripe customer, subscription, purchase, and webhook idempotency tables."""

import sqlalchemy as sa
from alembic import op


revision = "0008_stripe_billing"
down_revision = "0007_rate_limit_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("stripe_customer_id", sa.String(length=255), nullable=True))
    op.create_index("ix_users_stripe_customer_id", "users", ["stripe_customer_id"], unique=True)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_customer_id", sa.String(length=255), nullable=False),
        sa.Column("provider_subscription_id", sa.String(length=255), nullable=False),
        sa.Column("price_id", sa.String(length=255), nullable=True),
        sa.Column("plan_key", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_subscription_id", name="uq_subscription_provider_id"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_provider_customer_id", "subscriptions", ["provider_customer_id"])

    op.create_table(
        "credit_purchases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_checkout_session_id", sa.String(length=255), nullable=False),
        sa.Column("provider_payment_intent_id", sa.String(length=255), nullable=True),
        sa.Column("pack_key", sa.String(length=64), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=12), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_checkout_session_id", name="uq_purchase_checkout_session"),
    )
    op.create_index("ix_credit_purchases_user_id", "credit_purchases", ["user_id"])

    op.create_table(
        "billing_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("object_id", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_event_id", name="uq_billing_provider_event"),
    )


def downgrade() -> None:
    op.drop_table("billing_events")
    op.drop_index("ix_credit_purchases_user_id", table_name="credit_purchases")
    op.drop_table("credit_purchases")
    op.drop_index("ix_subscriptions_provider_customer_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_users_stripe_customer_id", table_name="users")
    op.drop_column("users", "stripe_customer_id")
