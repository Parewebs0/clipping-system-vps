"""Publish enqueue — IG/TikTok per campaign platform, copy, scheduling,
multi-account payload and idempotency (MOLI-53)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.asset import Asset, AssetStatus
from app.models.campaign import Campaign, CampaignStatus
from app.models.candidate import Candidate, CandidateStatus
from app.models.clip import Clip
from app.models.clip_publication import ClipPublication
from app.models.job import Job
from app.models.social_account import SocialAccount
from app.services.job_service import claim_next_job
from app.services.publish_enqueue import enqueue_publish_jobs

UTC = timezone.utc


def _mk_db() -> SessionLocal:  # returns a fresh Session
    return SessionLocal()


def _seed_clip(db, *, platforms: list[str]) -> Clip:
    campaign = Campaign(
        name=f"m53_{uuid.uuid4().hex[:8]}",
        status=CampaignStatus.SCORED.value,
        source_provider="whop",
        source_url=f"https://whop.com/{uuid.uuid4()}",
        source_metadata={
            "ruleset": {"version": 2, "platforms": list(platforms)}
        },
        source_instructions="",
        spec={},
    )
    db.add(campaign)
    db.flush()
    asset = Asset(
        campaign_id=campaign.id,
        status=AssetStatus.TRANSCRIBED.value,
        source_url=f"https://whop.com/{uuid.uuid4()}",
        local_path=f"C:\\test\\{uuid.uuid4()}.mp4",
        extra_metadata={},
    )
    db.add(asset)
    db.flush()
    cand = Candidate(
        campaign_id=campaign.id,
        asset_id=asset.id,
        status=CandidateStatus.APPROVED.value,
        start_time=0.0,
        end_time=30.0,
        score=0.8,
        extra_metadata={
            "title": "Denzel builds the box",
            "caption": "Watch till the end.",
        },
    )
    db.add(cand)
    db.flush()
    clip = Clip(
        campaign_id=campaign.id,
        asset_id=asset.id,
        candidate_id=cand.id,
        file_path=f"C:\\test\\{uuid.uuid4()}.mp4",
        qa_status="pass",
        qa_result={},
        status="approved",
        location="pending_upload",
        compliance_status="pass",
        publish_approved_at=datetime.now(UTC),
    )
    db.add(clip)
    db.flush()
    for plat in platforms:
        db.add(
            ClipPublication(
                clip_id=clip.id, platform=plat, status="pending", submit_status="pending"
            )
        )
    db.commit()
    db.refresh(clip)
    return clip


def _seed_accounts(db, rows: list[tuple[str, str]]) -> None:
    db.execute(text("DELETE FROM social_accounts"))
    for plat, key in rows:
        db.add(SocialAccount(platform=plat, account_key=key, status="healthy"))
    db.commit()


@pytest.fixture(autouse=True)
def _channels(db):
    """Isolated publish state: other tests leave pending publications in the
    shared test DB; enqueue(platform=None) serves whatever is pending, so this
    fixture wipes publish tables per test and restores the seeded accounts
    afterwards (migration 0014 seeds youtube healthy)."""
    for table in ("jobs", "clip_publications", "clips", "candidates", "assets", "campaigns", "social_accounts"):
        db.execute(text(f"DELETE FROM {table}"))
    _seed_accounts(
        db,
        [
            ("youtube", "default"),
            ("instagram", "default"),
            ("tiktok", "default"),
        ],
    )
    yield
    db.execute(text("DELETE FROM jobs"))
    db.execute(text("DELETE FROM clip_publications"))
    db.execute(text("DELETE FROM clips"))
    db.execute(text("DELETE FROM candidates"))
    db.execute(text("DELETE FROM assets"))
    db.execute(text("DELETE FROM campaigns"))
    db.execute(text("DELETE FROM social_accounts"))
    for plat in ("youtube", "instagram", "tiktok"):
        db.add(SocialAccount(platform=plat, account_key="default", status="healthy"))
    db.commit()


def test_dry_run_enqueues_jobs_for_campaign_platforms_with_copy(db):
    clip = _seed_clip(db, platforms=["instagram", "tiktok"])
    ok = clip.id
    db.close()

    rows = enqueue_publish_jobs(_mk_db(), limit=10)

    by_plat = {r["platform"]: r for r in rows if not r.get("skipped")}
    assert set(by_plat) == {"instagram", "tiktok"}, by_plat
    # per-platform copy present in the job payload
    db2 = _mk_db()
    for plat, row in by_plat.items():
        job = db2.get(Job, row["job_id"])
        payload = job.payload
        assert payload["dry_run"] is True
        assert payload["platform"] == plat
        assert payload["title"]
        assert payload["caption"]
        assert payload["hashtags"] is not None
        assert payload["account"] == "default"
        assert payload["accounts"] == ["default"]
        # scheduled: no slot env → available immediately
        assert payload["scheduled_at"]
        assert job.available_at <= datetime.now(UTC) + timedelta(seconds=5)
    db2.close()
    assert by_plat["tiktok"]["clip_id"] == str(ok)
    assert by_plat["instagram"]["clip_id"] == str(ok)


def test_dry_run_covers_youtube_too(db):
    clip = _seed_clip(db, platforms=["youtube", "instagram"])
    db.close()
    rows = enqueue_publish_jobs(_mk_db(), limit=10)
    platforms = {r["platform"] for r in rows if not r.get("skipped")}
    assert platforms == {"youtube", "instagram"}


def test_idempotency_no_double_publish(db):
    clip = _seed_clip(db, platforms=["tiktok"])
    db.close()

    first = enqueue_publish_jobs(_mk_db(), platform="tiktok", limit=10)
    assert len([r for r in first if not r.get("skipped")]) == 1
    # job still pending → the same clip/platform cannot enqueue again
    second = enqueue_publish_jobs(_mk_db(), platform="tiktok", limit=10)
    assert second == []


def test_scheduled_job_not_claimed_before_its_hour(db, monkeypatch):
    clip = _seed_clip(db, platforms=["tiktok"])
    db.close()

    future = datetime.now(UTC) + timedelta(hours=6)
    monkeypatch.setattr("app.services.publish_enqueue.scheduled_at_for", lambda plat, now: future)
    monkeypatch.setenv("PUBLISH_DRY_RUN", "0")  # live → schedule is honoured

    rows = enqueue_publish_jobs(_mk_db(), platform="tiktok", limit=10)
    assert rows and rows[0]["scheduled_at"] == future.isoformat()
    db2 = _mk_db()
    job = db2.get(Job, rows[0]["job_id"])
    assert job.available_at == future
    worker_id = f"test-{uuid.uuid4().hex[:6]}"
    assert claim_next_job(db2, worker_id=worker_id) is None, (
        "a job scheduled in the future must not be claimable"
    )
    db2.execute(
        text("UPDATE jobs SET available_at = now() WHERE id = :id"), {"id": job.id}
    )
    db2.commit()
    claimed = claim_next_job(db2, worker_id=worker_id)
    assert claimed is not None and str(claimed.id) == rows[0]["job_id"]
    db2.close()


def test_dry_run_jobs_available_immediately_even_with_slots(db, monkeypatch):
    """Dry-run keeps its validation role: it is not held back by slots."""
    clip = _seed_clip(db, platforms=["tiktok"])
    db.close()
    monkeypatch.setenv("PUBLISH_SLOT_TIKTOK", "00:00-00:30")
    rows = enqueue_publish_jobs(_mk_db(), platform="tiktok", limit=10)
    db2 = _mk_db()
    job = db2.get(Job, rows[0]["job_id"])
    assert job.payload["dry_run"] is True
    assert job.available_at <= datetime.now(UTC) + timedelta(seconds=5)
    db2.close()


def test_two_accounts_on_one_platform_in_payload(db):
    db2 = _mk_db()
    _seed_accounts(db2, [("instagram", "default"), ("instagram", "ig2")])
    db2.close()
    clip = _seed_clip(_mk_db(), platforms=["instagram"])

    rows = enqueue_publish_jobs(_mk_db(), platform="instagram", limit=10)
    assert len(rows) == 1
    db2 = _mk_db()
    payload = db2.get(Job, rows[0]["job_id"]).payload
    assert payload["accounts"] == ["default", "ig2"]
    assert payload["account"] == "default"
    db2.close()


def test_missing_account_is_reported_not_silent():
    db2 = _mk_db()
    _seed_accounts(db2, [("instagram", "default")])  # no tiktok account
    db2.close()
    clip = _seed_clip(_mk_db(), platforms=["instagram", "tiktok"])
    rows = enqueue_publish_jobs(_mk_db(), limit=10)
    skipped = [r for r in rows if r.get("skipped")]
    assert skipped and skipped[0]["reason"] == "blocked_missing_account:tiktok"
    assert {r["platform"] for r in rows if not r.get("skipped")} == {"instagram"}