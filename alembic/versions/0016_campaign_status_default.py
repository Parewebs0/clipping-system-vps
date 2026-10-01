"""campaigns.status DEFAULT 'draft' -> 'discovered'

Migration 0012 dropped 'draft' from ck_campaigns_status but left the column
default at 'draft', so any INSERT that omits status violates the constraint.
Use the pipeline v2 entry state instead.

Revision ID: 0016_campaign_status_default
Revises: 0015_llm_usage
Create Date: 2026-10-01
"""
from alembic import op


revision = "0016_campaign_status_default"
down_revision = "0015_llm_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE campaigns ALTER COLUMN status SET DEFAULT 'discovered'")


def downgrade() -> None:
    op.execute("ALTER TABLE campaigns ALTER COLUMN status SET DEFAULT 'draft'")
