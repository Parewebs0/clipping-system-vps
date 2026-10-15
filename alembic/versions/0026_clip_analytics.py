"""clips.analytics — YouTube views and retention per clip (MOLI-55)

JSONB document written by scripts/clip_analytics_tick.py. Empty object until
the first successful (or attempted) read. Downgrade drops the column; the
candidate still keeps calibrated_clip_score inside extra_metadata when the
tick has run.

Revision ID: 0026_clip_analytics
Revises: 0025_social_account_accounts
Create Date: 2026-10-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0026_clip_analytics"
down_revision = "0025_social_account_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clips",
        sa.Column(
            "analytics",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("clips", "analytics")
