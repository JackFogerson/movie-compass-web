"""Add website accounts, profile ownership, and future sharing tables."""

import sqlalchemy as sa
from alembic import op

revision = "0004_web_accounts"
down_revision = "0003_mapping_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_accounts_email", "accounts", ["email"], unique=True)
    op.add_column("users", sa.Column("owner_account_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_users_owner_account_id", "users", ["owner_account_id"])
    op.create_foreign_key(
        "fk_users_owner_account_id",
        "users",
        "accounts",
        ["owner_account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_table(
        "friendships",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("requester_id", sa.BigInteger(), nullable=False),
        sa.Column("addressee_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["requester_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["addressee_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("requester_id", "addressee_id", name="uq_friendship_direction"),
    )
    op.create_index("ix_friendships_requester_id", "friendships", ["requester_id"])
    op.create_index("ix_friendships_addressee_id", "friendships", ["addressee_id"])
    op.create_index("ix_friendships_status", "friendships", ["status"])
    op.create_table(
        "profile_shares",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("permission", sa.String(length=20), nullable=False, server_default="movie_night"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["profile_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("profile_id", "account_id", name="uq_profile_share"),
    )
    op.create_index("ix_profile_shares_profile_id", "profile_shares", ["profile_id"])
    op.create_index("ix_profile_shares_account_id", "profile_shares", ["account_id"])


def downgrade() -> None:
    op.drop_table("profile_shares")
    op.drop_table("friendships")
    op.drop_constraint("fk_users_owner_account_id", "users", type_="foreignkey")
    op.drop_index("ix_users_owner_account_id", table_name="users")
    op.drop_column("users", "owner_account_id")
    op.drop_index("ix_accounts_email", table_name="accounts")
    op.drop_table("accounts")
