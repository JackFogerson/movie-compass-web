"""Require short-lived email verification for new production accounts."""

import sqlalchemy as sa
from alembic import op

revision = "0009_registration_email_verification"
down_revision = "0008_email_password_reset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.add_column("accounts", sa.Column("verification_code_hash", sa.String(500)))
    op.add_column(
        "accounts",
        sa.Column("verification_code_expires_at", sa.DateTime(timezone=True)),
    )
    # Accounts created before this feature already authenticated successfully and must not be
    # locked out when the migration is deployed.
    op.execute("UPDATE accounts SET email_verified_at = CURRENT_TIMESTAMP")


def downgrade() -> None:
    op.drop_column("accounts", "verification_code_expires_at")
    op.drop_column("accounts", "verification_code_hash")
    op.drop_column("accounts", "email_verified_at")
