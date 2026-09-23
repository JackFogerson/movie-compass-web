"""Persist generated profile documents in PostgreSQL."""

import sqlalchemy as sa
from alembic import op

revision = "0005_profile_artifacts"
down_revision = "0004_web_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.String(50), nullable=False),
        sa.Column("artifact_key", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "artifact_type", "artifact_key", name="uq_profile_artifact"
        ),
    )
    op.create_index("ix_profile_artifacts_user_id", "profile_artifacts", ["user_id"])
    op.create_index(
        "ix_profile_artifacts_artifact_type", "profile_artifacts", ["artifact_type"]
    )


def downgrade() -> None:
    op.drop_index("ix_profile_artifacts_artifact_type", table_name="profile_artifacts")
    op.drop_index("ix_profile_artifacts_user_id", table_name="profile_artifacts")
    op.drop_table("profile_artifacts")
