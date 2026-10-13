"""social_accounts: several accounts per platform (MOLI-53)

The primary key becomes (platform, account_key). Existing rows keep the
``default`` account key (server default), so current deployments behave the
same. New accounts are added as extra rows with their own key; the worker
maps each key to its own credentials.

Revision ID: 0024_social_account_accounts
Revises: 0023_campaign_queue_sync
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0024_social_account_accounts"
down_revision = "0023_campaign_queue_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "social_accounts",
        sa.Column("account_key", sa.String(64), nullable=False, server_default="default"),
    )
    op.drop_constraint("social_accounts_pkey", "social_accounts", type_="primary")
    op.create_primary_key(
        "social_accounts_pkey", "social_accounts", ["platform", "account_key"]
    )


def downgrade() -> None:
    op.drop_constraint("social_accounts_pkey", "social_accounts", type_="primary")
    op.create_primary_key("social_accounts_pkey", "social_accounts", ["platform"])
    op.drop_column("social_accounts", "account_key")