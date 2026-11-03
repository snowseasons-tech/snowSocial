"""Store profile-level monthly metrics and additional action counters."""

import sqlalchemy as sa
from alembic import op

revision = "20261103_google_profile_metrics"
down_revision = "20261103_google_refresh_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    post_columns = {column["name"] for column in sa.inspect(bind).get_columns("monthly_post_metrics")}
    for name in ("calls", "direction_requests"):
        if name not in post_columns:
            op.add_column("monthly_post_metrics", sa.Column(name, sa.Integer(), nullable=True))

    tables = set(sa.inspect(bind).get_table_names())
    if "monthly_channel_metrics" not in tables:
        op.create_table(
            "monthly_channel_metrics",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False),
            sa.Column("provider_account_id", sa.String(length=255), nullable=False),
            sa.Column("month", sa.Date(), nullable=False),
            sa.Column("impressions", sa.Integer(), nullable=True),
            sa.Column("reach", sa.Integer(), nullable=True),
            sa.Column("likes", sa.Integer(), nullable=True),
            sa.Column("comments", sa.Integer(), nullable=True),
            sa.Column("shares", sa.Integer(), nullable=True),
            sa.Column("saves", sa.Integer(), nullable=True),
            sa.Column("link_clicks", sa.Integer(), nullable=True),
            sa.Column("calls", sa.Integer(), nullable=True),
            sa.Column("direction_requests", sa.Integer(), nullable=True),
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("channel", "provider_account_id", "month", name="uq_channel_metric_account_month"),
        )
        op.create_index("ix_monthly_channel_metrics_channel", "monthly_channel_metrics", ["channel"])
        op.create_index("ix_monthly_channel_metrics_month", "monthly_channel_metrics", ["month"])


def downgrade() -> None:
    bind = op.get_bind()
    if "monthly_channel_metrics" in sa.inspect(bind).get_table_names():
        op.drop_index("ix_monthly_channel_metrics_month", table_name="monthly_channel_metrics")
        op.drop_index("ix_monthly_channel_metrics_channel", table_name="monthly_channel_metrics")
        op.drop_table("monthly_channel_metrics")
    post_columns = {column["name"] for column in sa.inspect(bind).get_columns("monthly_post_metrics")}
    for name in ("direction_requests", "calls"):
        if name in post_columns:
            op.drop_column("monthly_post_metrics", name)
