"""campaigns: queue status, last_synced_at, budget_history (issue #72)

New discoveries wait in `queued` until a processing slot opens. Every sync
stores when the Whop detail was read and a short remaining-budget series.
Downgrade moves queued rows back to `discovered` (nothing is deleted) and
drops the two columns.

Revision ID: 0023_campaign_queue_sync
Revises: 0022_candidate_needs_review
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_campaign_queue_sync"
down_revision = "0022_candidate_needs_review"
branch_labels = None
depends_on = None

OLD_STATUSES = (
    "discovered", "briefed", "assets_resolved", "scored",
    "blocked_no_assets", "failed_brief", "failed_resolve", "archived", "parked",
    "blocked_low_score", "needs_review",
)
NEW_STATUSES = ("queued",) + OLD_STATUSES


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "campaigns",
        sa.Column(
            "budget_history",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_status", "campaigns", f"status IN {NEW_STATUSES!r}")


def downgrade() -> None:
    op.execute("UPDATE campaigns SET status = 'discovered' WHERE status = 'queued'")
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_status", "campaigns", f"status IN {OLD_STATUSES!r}")
    op.drop_column("campaigns", "budget_history")
    op.drop_column("campaigns", "last_synced_at")
