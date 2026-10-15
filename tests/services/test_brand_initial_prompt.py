"""MOLI-54 — brand_dictionary flows into the transcribe payload (initial_prompt)."""
import uuid

from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.asset import Asset
from app.models.campaign import Campaign
from app.services.job_state_transitions import _brand_initial_prompt
from app.services.rules.schema import RuleSet


def _camp_with_dictionary():
    db = SessionLocal()
    rs = RuleSet()
    rs.captions.brand_dictionary = ["BOXABL", "CASITA", "3D PRINTED"]
    c = Campaign(
        name=f"prompt-{uuid.uuid4().hex[:8]}",
        status="scored",
        source_provider="manual",
        source_metadata={"ruleset": rs.dump()},
    )
    db.add(c)
    db.commit()
    return db, c


def test_initial_prompt_joins_the_dictionary():
    db, c = _camp_with_dictionary()
    try:
        asset = Asset(campaign_id=c.id, source_url="https://example.com/v.mp4", status="downloaded")
        db.add(asset)
        db.commit()
        prompt = _brand_initial_prompt(db, asset)
        assert prompt is not None
        assert "BOXABL" in prompt
        assert "3D PRINTED" in prompt
        assert len(prompt) <= 900
    finally:
        db.rollback()


def test_initial_prompt_is_none_without_dictionary():
    db = SessionLocal()
    try:
        rs = RuleSet()
        c = Campaign(
            name=f"prompt-empty-{uuid.uuid4().hex[:8]}",
            status="scored",
            source_provider="manual",
            source_metadata={"ruleset": rs.dump()},
        )
        db.add(c)
        db.commit()
        asset = Asset(campaign_id=c.id, source_url="https://example.com/v2.mp4", status="downloaded")
        db.add(asset)
        db.commit()
        assert _brand_initial_prompt(db, asset) is None
    finally:
        db.rollback()


def test_initial_prompt_none_when_no_campaign():
    """Campaign row gone/missing → no prompt; the helper never raises."""
    db = SessionLocal()
    try:
        # Not inserted (FK would fail): plain instance is enough — the helper
        # only reads asset.campaign_id to find the campaign.
        fake = Asset(campaign_id=999999999, source_url="https://x.mp4")
        assert _brand_initial_prompt(db, fake) is None
    finally:
        db.rollback()