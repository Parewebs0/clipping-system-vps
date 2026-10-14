"""campaigns: prize_pool_usd — original total prize pool (MOLI-56)

First-class column for the original prize pool so Mission Control can show
"remaining / original" without parsing JSONB. Discovery fills it when a row
is created and only bumps it up on later syncs (Whop top-ups raise the total).

Existing rows are backfilled from source_metadata, in order of preference:
prize_pool_usd (top level, refreshed since 0023) -> discovered.prize_pool_usd
(original listing card) -> economics.budget_usd. Downgrade drops the column;
the metadata copies stay untouched, so no data is lost going down.

Revision ID: 0024_campaign_prize_pool
Revises: 0023_campaign_queue_sync
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0024_campaign_prize_pool"
down_revision = "0023_campaign_queue_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("prize_pool_usd", sa.Float(), nullable=True))
    # jsonb_typeof guards against non-numeric garbage the API wrote over time.
    op.execute(
        """
        UPDATE campaigns
        SET prize_pool_usd = COALESCE(
            CASE WHEN jsonb_typeof(source_metadata->'prize_pool_usd') = 'number'
                 THEN (source_metadata->>'prize_pool_usd')::double precision END,
            CASE WHEN jsonb_typeof(source_metadata->'discovered'->'prize_pool_usd') = 'number'
                 THEN (source_metadata->'discovered'->>'prize_pool_usd')::double precision END,
            CASE WHEN jsonb_typeof(source_metadata->'economics'->'budget_usd') = 'number'
                 THEN (source_metadata->'economics'->>'budget_usd')::double precision END
        )
        WHERE prize_pool_usd IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("campaigns", "prize_pool_usd")