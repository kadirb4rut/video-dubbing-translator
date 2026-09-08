"""Record output duration and provider/model version in usage telemetry."""

import sqlalchemy as sa
from alembic import op
from app.migration_compat import table_columns

revision = "0005_usage_output_and_model"
down_revision = "0004_usage_bytes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = set(table_columns(op.get_bind(), "usage_records"))
    with op.batch_alter_table("usage_records") as batch:
        if "output_duration_seconds" not in columns:
            batch.add_column(sa.Column("output_duration_seconds", sa.Float(), nullable=True))
        if "model_version" not in columns:
            batch.add_column(sa.Column("model_version", sa.String(length=160), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        batch.drop_column("model_version")
        batch.drop_column("output_duration_seconds")
