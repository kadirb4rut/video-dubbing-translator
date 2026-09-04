"""Track short-lived worker leases for truthful active-worker metrics."""

from alembic import op

from app.db import Base
from app import models  # noqa: F401


revision = "0006_worker_leases"
down_revision = "0005_usage_output_and_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["worker_leases"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    op.drop_table("worker_leases")
