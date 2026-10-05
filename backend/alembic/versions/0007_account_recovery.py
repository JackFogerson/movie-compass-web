"""Add one-time account recovery codes and revocable sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0007_account_recovery"
down_revision = "0006_web_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("recovery_code_hash", sa.String(500)))
    op.add_column(
        "accounts",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("accounts", "session_version")
    op.drop_column("accounts", "recovery_code_hash")
