"""Add per-job media pipeline telemetry fields."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0010_media_pipeline_telemetry"
down_revision = "0009_billing_refunds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("usage_records")}
    additions = {
        "queue_wait_seconds": sa.Float(),
        "compute_startup_seconds": sa.Float(),
        "model_load_seconds": sa.Float(),
        "real_time_factor": sa.Float(),
        "peak_vram_mb": sa.Float(),
        "peak_ram_mb": sa.Float(),
        "compute_cost_per_input_minute_usd": sa.Float(),
    }
    with op.batch_alter_table("usage_records") as batch:
        for name, column in additions.items():
            if name not in columns:
                batch.add_column(sa.Column(name, column, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        for name in (
            "compute_cost_per_input_minute_usd",
            "peak_ram_mb",
            "peak_vram_mb",
            "real_time_factor",
            "model_load_seconds",
            "compute_startup_seconds",
            "queue_wait_seconds",
        ):
            batch.drop_column(name)
