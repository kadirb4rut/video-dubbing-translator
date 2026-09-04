"""Record input and output bytes for usage accounting."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0004_usage_bytes"
down_revision = "0003_job_retries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("usage_records")}
    with op.batch_alter_table("usage_records") as batch:
        if "input_bytes" not in columns:
            batch.add_column(sa.Column("input_bytes", sa.Integer(), nullable=True))
        if "output_bytes" not in columns:
            batch.add_column(sa.Column("output_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        batch.drop_column("output_bytes")
        batch.drop_column("input_bytes")
