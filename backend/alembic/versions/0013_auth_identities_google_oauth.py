"""Add provider identities and one-time OAuth login state."""

import sqlalchemy as sa
from alembic import op


revision = "0013_auth_identities_google_oauth"
down_revision = "0012_cpu_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "auth_identities" not in tables:
        op.create_table(
            "auth_identities",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("provider_subject", sa.String(length=255), nullable=False),
            sa.Column("provider_email", sa.String(length=320), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider", "provider_email", name="uq_auth_identity_provider_email"),
            sa.UniqueConstraint("provider", "provider_subject", name="uq_auth_identity_provider_subject"),
        )
    existing_indexes = {index["name"] for index in inspector.get_indexes("auth_identities")} if "auth_identities" in tables else set()
    if "ix_auth_identities_user_id" not in existing_indexes:
        op.create_index("ix_auth_identities_user_id", "auth_identities", ["user_id"], unique=False)
    if "ix_auth_identities_provider_subject" not in existing_indexes:
        op.create_index("ix_auth_identities_provider_subject", "auth_identities", ["provider_subject"], unique=False)

    if "oauth_login_states" not in tables:
        op.create_table(
            "oauth_login_states",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("state_hash", sa.String(length=64), nullable=False),
            sa.Column("nonce_hash", sa.String(length=64), nullable=False),
            sa.Column("redirect_uri", sa.String(length=2048), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("state_hash"),
        )
    existing_indexes = {index["name"] for index in inspector.get_indexes("oauth_login_states")} if "oauth_login_states" in tables else set()
    if "ix_oauth_login_states_state_hash" not in existing_indexes:
        op.create_index("ix_oauth_login_states_state_hash", "oauth_login_states", ["state_hash"], unique=False)
    if "ix_oauth_login_states_provider_expires" not in existing_indexes:
        op.create_index("ix_oauth_login_states_provider_expires", "oauth_login_states", ["provider", "expires_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "oauth_login_states" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("oauth_login_states")}
        if "ix_oauth_login_states_provider_expires" in indexes:
            op.drop_index("ix_oauth_login_states_provider_expires", table_name="oauth_login_states")
        if "ix_oauth_login_states_state_hash" in indexes:
            op.drop_index("ix_oauth_login_states_state_hash", table_name="oauth_login_states")
        op.drop_table("oauth_login_states")
    if "auth_identities" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("auth_identities")}
        if "ix_auth_identities_provider_subject" in indexes:
            op.drop_index("ix_auth_identities_provider_subject", table_name="auth_identities")
        if "ix_auth_identities_user_id" in indexes:
            op.drop_index("ix_auth_identities_user_id", table_name="auth_identities")
        op.drop_table("auth_identities")
