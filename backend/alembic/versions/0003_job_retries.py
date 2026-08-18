"""Track bounded job retries."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_job_retries"
down_revision = "0002_artifacts_safety_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "retry_count" not in {column["name"] for column in inspect(op.get_bind()).get_columns("jobs")}:
        with op.batch_alter_table("jobs") as batch:
            batch.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("retry_count")
