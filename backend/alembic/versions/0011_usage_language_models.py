"""Add language and model manifest fields to usage telemetry."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0011_usage_language_models"
down_revision = "0010_media_pipeline_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("usage_records")}
    additions = {
        "source_language": sa.String(length=16),
        "target_language": sa.String(length=16),
        "models_json": sa.Text(),
    }
    with op.batch_alter_table("usage_records") as batch:
        for name, column in additions.items():
            if name not in columns:
                batch.add_column(sa.Column(name, column, nullable=True, server_default="{}" if name == "models_json" else None))


def downgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        for name in ("models_json", "target_language", "source_language"):
            batch.drop_column(name)
