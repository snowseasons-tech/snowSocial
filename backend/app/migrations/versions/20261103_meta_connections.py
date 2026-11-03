"""Add encrypted Meta channel connections and OAuth state."""

import sqlalchemy as sa
from alembic import op

revision = "20261103_meta_connections"
down_revision = "20261103_delivery_retries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("provider_account_id", sa.String(length=255), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=False),
        sa.Column("encrypted_access_token", sa.String(length=4096), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "channel", "provider_account_id", name="uq_channel_connection_account"
        ),
    )
    op.create_index("ix_channel_connections_provider", "channel_connections", ["provider"])
    op.create_index("ix_channel_connections_channel", "channel_connections", ["channel"])
    op.create_table(
        "oauth_states",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index("ix_oauth_states_expires_at", "oauth_states", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_oauth_states_expires_at", table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_index("ix_channel_connections_channel", table_name="channel_connections")
    op.drop_index("ix_channel_connections_provider", table_name="channel_connections")
    op.drop_table("channel_connections")
