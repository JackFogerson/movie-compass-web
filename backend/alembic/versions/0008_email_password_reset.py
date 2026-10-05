"""Expire password-reset codes delivered by email."""

import sqlalchemy as sa
from alembic import op

revision = "0008_email_password_reset"
down_revision = "0007_account_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts", sa.Column("recovery_code_expires_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.drop_column("accounts", "recovery_code_expires_at")
