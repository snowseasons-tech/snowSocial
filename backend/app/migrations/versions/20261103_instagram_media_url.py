"""Add optional public image URL for Instagram photo publishing."""

import sqlalchemy as sa
from alembic import op

revision = "20261103_instagram_media_url"
down_revision = "20261103_monthly_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("posts")}
    if "media_url" not in columns:
        op.add_column("posts", sa.Column("media_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("posts")}
    if "media_url" in columns:
        op.drop_column("posts", "media_url")
