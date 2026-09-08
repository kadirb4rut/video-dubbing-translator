"""Record CPU utilization for CPU and GPU worker runs."""

import sqlalchemy as sa
from alembic import op
from app.migration_compat import table_columns

revision = "0012_cpu_telemetry"
down_revision = "0011_usage_language_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = set(table_columns(op.get_bind(), "usage_records"))
    if "cpu_utilization_percent" not in columns:
        op.add_column("usage_records", sa.Column("cpu_utilization_percent", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_records", "cpu_utilization_percent")
