"""Add normalized monthly post metrics."""

import sqlalchemy as sa
from alembic import op

revision = "20261103_monthly_metrics"
down_revision = "20261103_meta_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "monthly_post_metrics" not in tables:
        op.create_table(
            "monthly_post_metrics",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("delivery_id", sa.Uuid(), nullable=False),
            sa.Column("month", sa.Date(), nullable=False),
            sa.Column("impressions", sa.Integer(), nullable=True),
            sa.Column("reach", sa.Integer(), nullable=True),
            sa.Column("likes", sa.Integer(), nullable=True),
            sa.Column("comments", sa.Integer(), nullable=True),
            sa.Column("shares", sa.Integer(), nullable=True),
            sa.Column("saves", sa.Integer(), nullable=True),
            sa.Column("link_clicks", sa.Integer(), nullable=True),
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["delivery_id"], ["channel_deliveries.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("delivery_id", "month", name="uq_post_metric_delivery_month"),
        )
        op.create_index("ix_monthly_post_metrics_delivery_id", "monthly_post_metrics", ["delivery_id"])
        op.create_index("ix_monthly_post_metrics_month", "monthly_post_metrics", ["month"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "monthly_post_metrics" in tables:
        op.drop_index("ix_monthly_post_metrics_month", table_name="monthly_post_metrics")
        op.drop_index("ix_monthly_post_metrics_delivery_id", table_name="monthly_post_metrics")
        op.drop_table("monthly_post_metrics")
