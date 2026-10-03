"""campaigns.status: add terminal 'archived' (issue #9)

Manual-only terminal state set from Mission Control. No pipeline tick selects
it (brief_reader: discovered; drive_resolver: briefed/failed_resolve/
assets_resolved; scorer: assets_resolved; download_enqueue: scored/ready) and
it is not in whop_discovery.ACTIVE_STATUSES, so archiving takes a campaign out
of the pipeline and frees a discovery slot. Re-discovery keeps the existing
status (upsert by source_url), so archive is sticky.

Revision ID: 0017_campaign_status_archived
Revises: 0016_campaign_status_default
Create Date: 2026-10-03
"""
from alembic import op
from sqlalchemy import text

revision = "0017_campaign_status_archived"
down_revision = "0016_campaign_status_default"
branch_labels = None
depends_on = None

V2_STATUSES = (
    "discovered", "briefed", "assets_resolved", "scored",
    "blocked_no_assets", "failed_brief", "failed_resolve",
)
NEW_STATUSES = V2_STATUSES + ("archived",)


def upgrade() -> None:
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_status", "campaigns", f"status IN {NEW_STATUSES!r}")


def downgrade() -> None:
    n = op.get_bind().execute(text("SELECT COUNT(*) FROM campaigns WHERE status = 'archived'")).scalar_one()
    if n:
        raise RuntimeError(
            f"Refusing to downgrade: {n} archived campaign(s). Delete or un-archive them first."
        )
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_status", "campaigns", f"status IN {V2_STATUSES!r}")
