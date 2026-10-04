"""Persist background web-job progress and results."""

import sqlalchemy as sa
from alembic import op

revision = "0006_web_jobs"
down_revision = "0005_profile_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("profile_slug", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress_message", sa.String(500), nullable=False),
        sa.Column("result_json", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_web_jobs_account_id", "web_jobs", ["account_id"])
    op.create_index("ix_web_jobs_job_type", "web_jobs", ["job_type"])
    op.create_index("ix_web_jobs_profile_slug", "web_jobs", ["profile_slug"])
    op.create_index("ix_web_jobs_status", "web_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("web_jobs")
