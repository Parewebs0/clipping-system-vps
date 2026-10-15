"""approve_publish platform defaults (MOLI-53): the campaign RuleSet decides."""
from __future__ import annotations

import uuid

from app.db.database import SessionLocal
from app.models.campaign import Campaign, CampaignStatus
from app.models.asset import Asset, AssetStatus
from app.models.candidate import Candidate, CandidateStatus
from app.models.clip import Clip
from app.services.publish_gate import approve_clip_publish, campaign_platforms


def _make_clip(*, ruleset_platforms: list[str] | None = None) -> str:
    db = SessionLocal()
    try:
        meta = (
            {"ruleset": {"version": 2, "platforms": list(ruleset_platforms)}}
            if ruleset_platforms is not None
            else {}
        )
        c = Campaign(
            name=f"m53g_{uuid.uuid4().hex[:8]}",
            status=CampaignStatus.SCORED.value,
            source_provider="whop",
            source_url=f"https://whop.com/{uuid.uuid4()}",
            source_metadata=meta,
            source_instructions="",
            spec={},
        )
        db.add(c)
        db.flush()
        a = Asset(
            campaign_id=c.id,
            status=AssetStatus.TRANSCRIBED.value,
            source_url=f"https://whop.com/{uuid.uuid4()}",
            local_path=f"C:\\test\\{uuid.uuid4()}.mp4",
            extra_metadata={},
        )
        db.add(a)
        db.flush()
        cand = Candidate(
            campaign_id=c.id,
            asset_id=a.id,
            status=CandidateStatus.APPROVED.value,
            start_time=0.0,
            end_time=30.0,
            score=0.8,
            extra_metadata={},
        )
        db.add(cand)
        db.flush()
        clip = Clip(
            campaign_id=c.id,
            asset_id=a.id,
            candidate_id=cand.id,
            file_path=f"C:\\test\\{uuid.uuid4()}.mp4",
            qa_status="pass",
            qa_result={},
            status="approved",
            location="pending_upload",
            compliance_status="pass",
        )
        db.add(clip)
        db.commit()
        return str(clip.id)
    finally:
        db.close()


def _platforms_for(clip_id: str) -> list[str]:
    from app.models.clip_publication import ClipPublication

    db = SessionLocal()
    try:
        rows = db.query(ClipPublication).filter(ClipPublication.clip_id == clip_id).all()
        return sorted(r.platform for r in rows)
    finally:
        db.close()


def _approve(clip_id: str):
    db = SessionLocal()
    try:
        clip, pubs, already = approve_clip_publish(db, clip_id)
        return already is False
    finally:
        db.close()


def test_rule_platforms_expand_default_approval():
    clip_id = _make_clip(ruleset_platforms=["instagram", "tiktok"])
    assert _approve(clip_id)
    assert _platforms_for(clip_id) == ["instagram", "tiktok"]


def test_rule_platforms_include_youtube_when_named():
    clip_id = _make_clip(ruleset_platforms=["youtube", "instagram"])
    assert _approve(clip_id)
    assert _platforms_for(clip_id) == ["instagram", "youtube"]


def test_unknown_rule_platform_ignored():
    clip_id = _make_clip(ruleset_platforms=["facebook", "instagram"])
    assert _approve(clip_id)
    assert _platforms_for(clip_id) == ["instagram"]


def test_no_ruleset_keeps_youtube_only():
    clip_id = _make_clip()
    assert _approve(clip_id)
    assert _platforms_for(clip_id) == ["youtube"]


def test_empty_rule_platforms_keep_youtube_only():
    clip_id = _make_clip(ruleset_platforms=[])
    assert _approve(clip_id)
    assert _platforms_for(clip_id) == ["youtube"]


def test_explicit_platforms_win():
    clip_id = _make_clip(ruleset_platforms=["instagram", "tiktok"])
    db = SessionLocal()
    try:
        clip, pubs, already = approve_clip_publish(db, clip_id, platforms=["tiktok"])
        assert already is False
    finally:
        db.close()
    assert _platforms_for(clip_id) == ["tiktok"]


def test_explicit_empty_list_keeps_legacy_default():
    """platforms=[] historically meant 'youtube only' — keep it."""
    clip_id = _make_clip(ruleset_platforms=["instagram", "tiktok"])
    db = SessionLocal()
    try:
        approve_clip_publish(db, clip_id, platforms=[])
    finally:
        db.close()
    assert _platforms_for(clip_id) == ["youtube"]


def test_campaign_platforms_helper():
    clip_id = _make_clip(ruleset_platforms=["TIKTOK", "instagram"])
    db = SessionLocal()
    try:
        clip = db.get(Clip, clip_id)
        campaign = db.get(Campaign, clip.campaign_id)
        from app.services.rules.schema import load_ruleset

        rs = load_ruleset(campaign.source_metadata)
        assert rs.platforms == ["TIKTOK", "instagram"]
        # campaign order is preserved, names normalised to lowercase
        assert campaign_platforms(db, campaign) == ["tiktok", "instagram"]
    finally:
        db.close()