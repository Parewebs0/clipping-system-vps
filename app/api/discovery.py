"""Discovery API endpoints (Steps 1 + 5 of architecture_flow.md).

POST /discovery/run          → run all providers now, upsert campaigns, resolve assets.
GET  /discovery/providers    → list registered providers.
GET  /discovery/scoring/{id} → score a specific campaign.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.models.campaign import Campaign
from app.services.discovery.registry import all_providers
from app.services.discovery.scoring import score_campaign
from app.services.discovery.upsert import run_discovery
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.get("/providers", response_model=dict)
def list_providers(_: bool = Depends(require_bearer)):
    """List registered campaign providers (Whop today, more tomorrow)."""
    return {
        "providers": [
            {"name": p.name, "type": type(p).__name__}
            for p in all_providers()
        ],
        "config": {
            "cpm_min_usd_per_1k": getattr(settings, "cpm_min_usd_per_1k", 1.0),
            "prize_pool_min_usd": getattr(settings, "prize_pool_min_usd", 20000.0),
        },
    }


@router.post("/run", response_model=dict)
def run_now(
    limit: int = Query(50, ge=1, le=200),
    fetch_detail: bool = Query(True),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Run discovery for all providers now, upsert, resolve assets.

    New campaigns land in status='discovered'; the pipeline v2 crons
    (brief_reader → drive_resolver → scorer) take it from there. The old
    `analyze_after` step (pipeline v1 campaign_analyzer) was removed.
    """
    return run_discovery(db, fetch_detail=fetch_detail, limit=limit)


@router.get("/scoring/{campaign_id}", response_model=dict)
def get_scoring(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Score a specific campaign using the scoring algorithm."""
    c = db.get(Campaign, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    s = score_campaign(c)
    return {
        "campaign_id": c.id,
        "name": c.name,
        "score": s.model_dump(),
    }

