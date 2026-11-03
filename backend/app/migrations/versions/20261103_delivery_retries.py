from alembic import op
from sqlalchemy import Column, DateTime, inspect

from app import models  # noqa: F401 - register current metadata
from app.database import Base

revision = "20261103_delivery_retries"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # This also initializes an empty database and leaves existing tables untouched.
    Base.metadata.create_all(bind=bind)
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("channel_deliveries")}
    for name in ("last_attempt_at", "next_attempt_at", "claimed_at"):
        if name not in columns:
            op.add_column("channel_deliveries", Column(name, DateTime(timezone=True)))
    inspector = inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("channel_deliveries")}
    if "ix_channel_deliveries_next_attempt_at" not in indexes:
        op.create_index("ix_channel_deliveries_next_attempt_at", "channel_deliveries", ["next_attempt_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("channel_deliveries")}
    if "ix_channel_deliveries_next_attempt_at" in indexes:
        op.drop_index("ix_channel_deliveries_next_attempt_at", table_name="channel_deliveries")
    columns = {column["name"] for column in inspect(bind).get_columns("channel_deliveries")}
    for name in ("claimed_at", "next_attempt_at", "last_attempt_at"):
        if name in columns:
            op.drop_column("channel_deliveries", name)
