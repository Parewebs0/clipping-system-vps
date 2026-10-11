"""candidates.status: add 'needs_review' (decider copy failed twice)

A candidate whose caption fails check_copy after one retry is stored as
needs_review instead of pending. Downgrade maps those rows back to pending;
the reason stays in extra_metadata.review_reason.

Revision ID: 0022_candidate_needs_review
Revises: 0021_clip_compliance
Create Date: 2026-10-10
"""
from alembic import op

revision = "0022_candidate_needs_review"
down_revision = "0021_clip_compliance"
branch_labels = None
depends_on = None

OLD_STATUSES = (
    "pending", "approved", "rejected", "rendered", "superseded",
)
NEW_STATUSES = OLD_STATUSES + ("needs_review",)


def upgrade() -> None:
    op.drop_constraint("ck_candidates_status", "candidates", type_="check")
    op.create_check_constraint(
        "ck_candidates_status",
        "candidates",
        f"status IN {NEW_STATUSES!r}",
    )


def downgrade() -> None:
    op.execute("UPDATE candidates SET status = 'pending' WHERE status = 'needs_review'")
    op.drop_constraint("ck_candidates_status", "candidates", type_="check")
    op.create_check_constraint(
        "ck_candidates_status",
        "candidates",
        f"status IN {OLD_STATUSES!r}",
    )
