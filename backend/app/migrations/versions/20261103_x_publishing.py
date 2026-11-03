"""Add X OAuth PKCE state and channel support."""

import sqlalchemy as sa
from alembic import op

revision = "20261103_x_publishing"
down_revision = "20261103_admin_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("oauth_states")}
    if "encrypted_code_verifier" not in columns:
        op.add_column("oauth_states", sa.Column("encrypted_code_verifier", sa.String(length=255), nullable=True))
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE channel ADD VALUE IF NOT EXISTS 'x'")


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("oauth_states")}
    if "encrypted_code_verifier" in columns:
        op.drop_column("oauth_states", "encrypted_code_verifier")
