"""campaigns.status: add 'needs_review' (issue #37, rules gate)

Set by the rules gate (scorer / download tick) when the campaign's RuleSet has
an unsupported rule or a human requirement (account, pre-approval, logo file)
not yet confirmed. Nothing is downloaded or rendered while in this status.
Additive.

Revision ID: 0020_campaign_needs_review
Revises: 0019_campaign_status_low_score
Create Date: 2026-10-03
"""
from alembic import op

revision = "0020_campaign_needs_review"
down_revision = "0019_campaign_status_low_score"
branch_labels = None
depends_on = None

OLD_STATUSES = (
    "discovered", "briefed", "assets_resolved", "scored",
    "blocked_no_assets", "failed_brief", "failed_resolve", "archived", "parked",
    "blocked_low_score",
)
NEW_STATUSES = OLD_STATUSES + ("needs_review",)


def upgrade() -> None:
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_status", "campaigns", f"status IN {NEW_STATUSES!r}")


def downgrade() -> None:
    op.execute("UPDATE campaigns SET status = 'parked' WHERE status = 'needs_review'")
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_status", "campaigns", f"status IN {OLD_STATUSES!r}")
