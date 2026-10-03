"""clips: compliance_status + compliance_report (issue #43, post-render verifier)

Additive. compliance_status in (pending, pass, fail); publish requires 'pass'.

Revision ID: 0021_clip_compliance
Revises: 0020_campaign_needs_review
Create Date: 2026-10-03
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_clip_compliance"
down_revision = "0020_campaign_needs_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("compliance_status", sa.String(16), nullable=False, server_default="pending"))
    op.add_column("clips", sa.Column("compliance_report", postgresql.JSONB(), nullable=False,
                                     server_default=sa.text("'{}'::jsonb")))
    op.create_check_constraint("ck_clips_compliance_status", "clips",
                               "compliance_status IN ('pending', 'pass', 'fail')")


def downgrade() -> None:
    op.drop_constraint("ck_clips_compliance_status", "clips", type_="check")
    op.drop_column("clips", "compliance_report")
    op.drop_column("clips", "compliance_status")
