"""Add Stripe customer, subscription, purchase, and webhook idempotency tables."""

import sqlalchemy as sa
from alembic import op
from app import models  # noqa: F401
from app.db import Base
from app.migration_compat import index_names, table_columns

revision = "0008_stripe_billing"
down_revision = "0007_rate_limit_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    user_columns = set(table_columns(bind, "users"))
    if "stripe_customer_id" not in user_columns:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("stripe_customer_id", sa.String(length=255), nullable=True))
    users_table = Base.metadata.tables["users"]
    existing_indexes = index_names(bind, "users")
    for index in users_table.indexes:
        if index.name == "ix_users_stripe_customer_id" and index.name not in existing_indexes:
            index.create(bind=bind)

    for table_name in ("subscriptions", "credit_purchases", "billing_events"):
        table = Base.metadata.tables[table_name]
        table.create(bind=bind, checkfirst=True)
        existing_indexes = index_names(bind, table_name)
        for index in table.indexes:
            if index.name not in existing_indexes:
                index.create(bind=bind)


def downgrade() -> None:
    op.drop_table("billing_events")
    op.drop_index("ix_credit_purchases_user_id", table_name="credit_purchases")
    op.drop_table("credit_purchases")
    op.drop_index("ix_subscriptions_provider_customer_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_users_stripe_customer_id", table_name="users")
    op.drop_column("users", "stripe_customer_id")
