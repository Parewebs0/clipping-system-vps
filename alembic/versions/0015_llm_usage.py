"""llm_usage: one row per LLM call (tokens, latency, estimated cost)

Additive only.

Revision ID: 0015_llm_usage
Revises: 0014_publish_gate
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0015_llm_usage"
down_revision = "0014_publish_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("provider", sa.String(32), nullable=False, server_default="xai"),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("stage", sa.String(64), nullable=False, server_default="unknown"),
        sa.Column("campaign_id", sa.Integer, nullable=True),
        sa.Column("asset_id", sa.String(64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("ok", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("extra", JSONB, nullable=True),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    op.create_index("ix_llm_usage_stage", "llm_usage", ["stage"])
    op.create_index("ix_llm_usage_campaign_id", "llm_usage", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_campaign_id", table_name="llm_usage")
    op.drop_index("ix_llm_usage_stage", table_name="llm_usage")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
