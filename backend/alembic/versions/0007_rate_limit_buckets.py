"""Persist API rate-limit windows across API replicas."""

from alembic import op

from app import models  # noqa: F401


revision = "0007_rate_limit_buckets"
down_revision = "0006_worker_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    models.RateLimitBucket.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    models.RateLimitBucket.__table__.drop(bind=op.get_bind(), checkfirst=True)
