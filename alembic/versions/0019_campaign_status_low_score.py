"""campaigns.status: add 'blocked_low_score' (issue #25)

Written by scripts/campaign_scorer_tick.py when a campaign HAS real assets but
its score / publish-platform rate / host is not good enough. Before #25 those
were mislabelled 'blocked_no_assets'. Additive; no tick consumes it.

Revision ID: 0019_campaign_status_low_score
Revises: 0018_campaign_status_parked
Create Date: 2026-10-03
"""
from alembic import op
from sqlalchemy import text

revision = "0019_campaign_status_low_score"
down_revision = "0018_campaign_status_parked"
branch_labels = None
depends_on = None

OLD_STATUSES = (
    "discovered", "briefed", "assets_resolved", "scored",
    "blocked_no_assets", "failed_brief", "failed_resolve", "archived", "parked",
)
NEW_STATUSES = OLD_STATUSES + ("blocked_low_score",)


def upgrade() -> None:
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_status", "campaigns", f"status IN {NEW_STATUSES!r}")


def downgrade() -> None:
    op.execute("UPDATE campaigns SET status = 'blocked_no_assets' WHERE status = 'blocked_low_score'")
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_status", "campaigns", f"status IN {OLD_STATUSES!r}")
