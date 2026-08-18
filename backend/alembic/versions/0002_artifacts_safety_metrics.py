"""Add artifacts, stage metrics, safety records, and model economics."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.db import Base
from app import models  # noqa: F401

revision = "0002_artifacts_safety_metrics"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    media_columns = {column["name"] for column in inspector.get_columns("media_assets")}
    if "status" not in media_columns:
        with op.batch_alter_table("media_assets") as batch:
            batch.add_column(sa.Column("status", sa.String(length=24), nullable=False, server_default="ready"))
    job_columns = {column["name"]: column for column in inspector.get_columns("jobs")}
    if "media_asset_id" in job_columns and job_columns["media_asset_id"].get("nullable") is False:
        with op.batch_alter_table("jobs") as batch:
            batch.alter_column("media_asset_id", existing_type=sa.String(length=36), nullable=True)
    for table_name in ("job_artifacts", "job_stage_metrics", "abuse_events", "password_reset_tokens", "model_versions", "gpu_cost_profiles"):
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("gpu_cost_profiles", "model_versions", "password_reset_tokens", "abuse_events", "job_stage_metrics", "job_artifacts"):
        op.drop_table(table)
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column("media_asset_id", existing_type=sa.String(length=36), nullable=False)
    with op.batch_alter_table("media_assets") as batch:
        batch.drop_column("status")
