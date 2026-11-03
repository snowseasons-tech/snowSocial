"""Add encrypted refresh token storage for Google OAuth."""

import sqlalchemy as sa
from alembic import op

revision = "20261103_google_refresh_tokens"
down_revision = "20261103_instagram_media_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("channel_connections")}
    if "encrypted_refresh_token" not in columns:
        op.add_column("channel_connections", sa.Column("encrypted_refresh_token", sa.String(length=4096), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("channel_connections")}
    if "encrypted_refresh_token" in columns:
        op.drop_column("channel_connections", "encrypted_refresh_token")
