"""Create persisted single-admin browser sessions."""
import sqlalchemy as sa
from alembic import op

revision = "20261103_admin_sessions"
down_revision = "20261103_google_profile_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "auth_sessions" not in inspector.get_table_names():
        op.create_table(
            "auth_sessions",
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("token_hash"),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("auth_sessions")}
    if "ix_auth_sessions_expires_at" not in indexes:
        op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "auth_sessions" not in tables:
        return
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("auth_sessions")}
    if "ix_auth_sessions_expires_at" in indexes:
        op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_table("auth_sessions")
