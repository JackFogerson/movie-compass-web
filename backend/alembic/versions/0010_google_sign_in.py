"""Support passwordless Google accounts."""

import sqlalchemy as sa
from alembic import op

revision = "0010_google_sign_in"
down_revision = "0009_registration_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("password_login_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("accounts", sa.Column("google_subject", sa.String(255)))
    op.create_index("ix_accounts_google_subject", "accounts", ["google_subject"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_accounts_google_subject", table_name="accounts")
    op.drop_column("accounts", "google_subject")
    op.drop_column("accounts", "password_login_enabled")
