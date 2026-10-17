"""Publish enqueue — IG/TikTok per campaign platform, copy, scheduling,
multi-account payload and idempotency (MOLI-53)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.config import settings
from app.db.database import SessionLocal
from app.models.asset import Asset, AssetStatus
from app.models.campaign import Campaign, CampaignStatus
from app.models.candidate import Candidate, CandidateStatus
from app.models.clip import Clip
from app.models.clip_publication import ClipPublication
from app.models.job import Job
from app.models.social_account import SocialAccount
from app.services.job_service import _claim_filter, claim_next_job, create_job
from app.services.publish_enqueue import enqueue_publish_jobs

UTC = timezone.utc
RENDER_WORKER = "windows-gpu-worker-01"
FALLBACK_WORKER = "minipc-cpu-worker-01"

# Sessions created for a single enqueue/seed call. The engine pool is 2+3;
# leaving them open overflows it once the whole suite is collected in one process.
_OPEN_SESSIONS: list = []


def _mk_db() -> SessionLocal:  # returns a fresh Session
    session = SessionLocal()
    _OPEN_SESSIONS.append(session)
    return session


def _job(db, *, job_type: str, worker_id: str | None, payload: dict) -> Job:
    job = Job(
        job_type=job_type,
        status="completed",
        worker_id=worker_id,
        payload=payload,
        priority=5,
        max_attempts=3,
    )
    db.add(job)
    db.flush()
    return job


def _seed_clip(
    db,
    *,
    platforms: list[str],
    via: str = "render",
    worker: str | None = RENDER_WORKER,
    decoy_qa_affinity: str | None = None,
) -> Clip:
    """Seed a publishable clip.

    ``via`` says where the file owner comes from: ``render`` (worker_id of
    the render job), ``qa`` (QA affinity pin), ``qa_worker`` (QA worker_id),
    ``render_affinity`` (render payload pin only), ``none`` (no owner).
    """
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
    render_job = None
    qa_job = None
    if via != "none":
        render_payload: dict = {"candidate_id": str(cand.id)}
        render_worker_id = worker if via == "render" else None
        if via == "render_affinity" and worker:
            render_payload["affinity_worker_id"] = worker
        render_job = _job(
            db, job_type="render", worker_id=render_worker_id, payload=render_payload,
        )
        if via in {"qa", "qa_worker"} or decoy_qa_affinity:
            qa_payload: dict = {}
            qa_worker_id = worker if via == "qa_worker" else None
            if via == "qa" and worker:
                qa_payload["affinity_worker_id"] = worker
            if decoy_qa_affinity:
                qa_payload["affinity_worker_id"] = decoy_qa_affinity
            qa_job = _job(db, job_type="qa", worker_id=qa_worker_id, payload=qa_payload)
    clip = Clip(
        campaign_id=campaign.id,
        asset_id=asset.id,
        candidate_id=cand.id,
        render_job_id=render_job.id if render_job is not None else None,
        qa_job_id=qa_job.id if qa_job is not None else None,
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


@pytest.fixture(autouse=True)
def _close_mk_sessions():
    """Close every `_mk_db()` session, including ones passed straight into
    `enqueue_publish_jobs` and never stored by the test.

    Defined after `_channels` so this teardown runs first and returns the
    connection before the cleanup deletes rows.
    """
    yield
    while _OPEN_SESSIONS:
        _OPEN_SESSIONS.pop().close()


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
    assert job.payload["affinity_worker_id"] == RENDER_WORKER
    assert rows[0]["worker"] == RENDER_WORKER
    # The owner is the only worker allowed to take the job, and not before
    # the slot. A random worker would also miss it because of the pin.
    assert claim_next_job(db2, worker_id=RENDER_WORKER) is None, (
        "a job scheduled in the future must not be claimable"
    )
    db2.execute(
        text("UPDATE jobs SET available_at = now() WHERE id = :id"), {"id": job.id}
    )
    db2.commit()
    claimed = claim_next_job(db2, worker_id=RENDER_WORKER)
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


def _publish_payload(rows: list[dict]) -> dict:
    created = [r for r in rows if not r.get("skipped")]
    assert len(created) == 1, rows
    db2 = _mk_db()
    try:
        job = db2.get(Job, created[0]["job_id"])
        assert job is not None
        return job.payload
    finally:
        db2.close()


def test_publish_job_pinned_to_render_worker(db):
    clip = _seed_clip(db, platforms=["youtube"], decoy_qa_affinity="other-worker")
    db.close()

    rows = enqueue_publish_jobs(_mk_db(), platform="youtube", limit=1)

    assert rows[0]["worker"] == RENDER_WORKER
    assert rows[0]["clip_id"] == str(clip.id)
    assert _publish_payload(rows)["affinity_worker_id"] == RENDER_WORKER


def test_affinity_from_qa_pin_when_render_worker_missing(db):
    clip = _seed_clip(db, platforms=["youtube"], via="qa")
    db.close()

    rows = enqueue_publish_jobs(_mk_db(), platform="youtube", limit=1)

    assert rows[0]["worker"] == RENDER_WORKER
    assert rows[0]["clip_id"] == str(clip.id)
    assert _publish_payload(rows)["affinity_worker_id"] == RENDER_WORKER


def test_affinity_from_qa_worker_id(db):
    _seed_clip(db, platforms=["youtube"], via="qa_worker")
    db.close()

    rows = enqueue_publish_jobs(_mk_db(), platform="youtube", limit=1)

    assert rows[0]["worker"] == RENDER_WORKER
    assert _publish_payload(rows)["affinity_worker_id"] == RENDER_WORKER


def test_affinity_from_render_pin_when_worker_id_cleared(db):
    _seed_clip(db, platforms=["youtube"], via="render_affinity")
    db.close()

    rows = enqueue_publish_jobs(_mk_db(), platform="youtube", limit=1)

    assert rows[0]["worker"] == RENDER_WORKER
    assert _publish_payload(rows)["affinity_worker_id"] == RENDER_WORKER


def test_unknown_worker_is_not_enqueued(db, caplog):
    clip = _seed_clip(db, platforms=["youtube", "instagram"], via="none")
    db.close()
    skipped = [
        {
            "skipped": True,
            "clip_id": str(clip.id),
            "platform": plat,
            "reason": "blocked_unknown_worker",
        }
        for plat in ("youtube", "instagram")
    ]

    with caplog.at_level(logging.WARNING, logger="app.services.publish_enqueue"):
        rows = enqueue_publish_jobs(_mk_db(), limit=5)
        again = enqueue_publish_jobs(_mk_db(), limit=5)

    assert rows == skipped
    assert again == rows
    # Once per clip: two platforms and a second run do not log again.
    warnings = [r for r in caplog.records if "blocked_unknown_worker" in r.message]
    assert len(warnings) == 1
    db2 = _mk_db()
    n = db2.execute(text("SELECT count(*) FROM jobs WHERE job_type = 'publish'")).scalar()
    db2.close()
    assert n == 0


def test_unknown_worker_does_not_consume_the_limit(db):
    bad = _seed_clip(db, platforms=["youtube"], via="none")
    good = _seed_clip(db, platforms=["youtube"])
    db.execute(
        text("UPDATE clip_publications SET created_at = now() - interval '1 hour' WHERE clip_id = :id"),
        {"id": bad.id},
    )
    db.commit()
    db.close()

    rows = enqueue_publish_jobs(_mk_db(), platform="youtube", limit=1)

    skipped = [r for r in rows if r.get("skipped")]
    created = [r for r in rows if not r.get("skipped")]
    assert len(skipped) == 1 and skipped[0]["clip_id"] == str(bad.id)
    assert skipped[0]["reason"] == "blocked_unknown_worker"
    assert len(created) == 1 and created[0]["clip_id"] == str(good.id)
    assert created[0]["worker"] == RENDER_WORKER


def test_dry_run_shows_worker_without_creating_a_job(db):
    clip = _seed_clip(db, platforms=["tiktok"])
    clip_id, campaign_id, file_path = str(clip.id), clip.campaign_id, clip.file_path
    db.close()

    rows = enqueue_publish_jobs(_mk_db(), platform="tiktok", limit=1, create_jobs=False)

    assert len(rows) == 1
    row = rows[0]
    assert row["clip_id"] == clip_id
    assert row["campaign_id"] == campaign_id
    assert row["platform"] == "tiktok"
    assert row["file_path"] == file_path
    assert row["account"] == "default"
    assert row["dry_run"] is True
    assert row["worker"] == RENDER_WORKER
    assert row["job_id"] is None
    assert row["would_create"] is True
    assert row["title"]
    assert row["scheduled_at"]
    db2 = _mk_db()
    n = db2.execute(text("SELECT count(*) FROM jobs WHERE job_type = 'publish'")).scalar()
    db2.close()
    assert n == 0


def test_fallback_cannot_claim_pinned_publish_when_gpu_is_down(db, monkeypatch):
    """CPU fallback may take a free download while the GPU is stale, and
    still must leave the pinned publish job waiting for its owner."""
    monkeypatch.setattr(settings, "fallback_worker_ids", FALLBACK_WORKER)
    monkeypatch.setattr(settings, "worker_stale_seconds", 600)
    db.execute(text("DELETE FROM workers"))
    db.commit()
    _seed_clip(db, platforms=["youtube"])
    db.close()

    rows = enqueue_publish_jobs(_mk_db(), platform="youtube", limit=1)
    job_id = rows[0]["job_id"]
    assert rows[0]["worker"] == RENDER_WORKER

    db2 = _mk_db()
    now = datetime.now(UTC)
    pinned = db2.get(Job, job_id)
    assert pinned is not None
    visible_to_cpu = db2.execute(
        select(Job.id).where(Job.id == pinned.id, _claim_filter(db2, FALLBACK_WORKER, now))
    ).scalar_one_or_none()
    visible_to_gpu = db2.execute(
        select(Job.id).where(Job.id == pinned.id, _claim_filter(db2, RENDER_WORKER, now))
    ).scalar_one_or_none()
    assert visible_to_cpu is None
    assert visible_to_gpu == pinned.id

    download = create_job(db2, job_type="download", payload={"probe": 1})
    claimed_dl = claim_next_job(db2, worker_id=FALLBACK_WORKER)
    assert claimed_dl is not None and claimed_dl.id == download.id

    assert claim_next_job(db2, worker_id=FALLBACK_WORKER) is None
    publish = db2.get(Job, job_id)
    assert publish.status == "pending"
    assert publish.worker_id is None
    assert publish.payload["affinity_worker_id"] == RENDER_WORKER

    claimed = claim_next_job(db2, worker_id=RENDER_WORKER)
    assert claimed is not None and str(claimed.id) == job_id
    db2.close()