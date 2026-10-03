"""campaigns.status: add 'parked' (issue #23)

Set automatically by scripts/campaign_closed_tick.py when the source campaign
is paused / budget-exhausted / almost exhausted / removed, or does not fit the
pipeline (not clipping, no YouTube, requires application). No pipeline tick
selects it and it is not in whop_discovery.ACTIVE_STATUSES. Additive.

Revision ID: 0018_campaign_status_parked
Revises: 0017_campaign_status_archived
Create Date: 2026-10-03
"""
from alembic import op
from sqlalchemy import text

revision = "0018_campaign_status_parked"
down_revision = "0017_campaign_status_archived"
branch_labels = None
depends_on = None

OLD_STATUSES = (
    "discovered", "briefed", "assets_resolved", "scored",
    "blocked_no_assets", "failed_brief", "failed_resolve", "archived",
)
NEW_STATUSES = OLD_STATUSES + ("parked",)


def upgrade() -> None:
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_status", "campaigns", f"status IN {NEW_STATUSES!r}")


def downgrade() -> None:
    n = op.get_bind().execute(text("SELECT COUNT(*) FROM campaigns WHERE status = 'parked'")).scalar_one()
    if n:
        raise RuntimeError(f"Refusing to downgrade: {n} parked campaign(s). Re-open or archive them first.")
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_status", "campaigns", f"status IN {OLD_STATUSES!r}")
