"""POST /worker/storage/check: read-only answer for worker-side cleanup."""
from __future__ import annotations

import uuid

import pytest

from app.models.asset import Asset, AssetStatus
from app.models.campaign import Campaign
from app.models.candidate import Candidate, CandidateStatus
from app.models.clip import Clip
from app.models.job import Job


def _campaign(db, status="scored"):
    c = Campaign(name=f"sc-{uuid.uuid4().hex[:8]}", source_provider="whop", status=status)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _asset(db, c, status=AssetStatus.TRANSCRIBED.value):
    a = Asset(campaign_id=c.id, source_url="https://example.com/v", source_provider="youtube",
              source_id=uuid.uuid4().hex, asset_type="video", status=status)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _cand(db, a, status):
    x = Candidate(campaign_id=a.campaign_id, asset_id=a.id, start_time=1.0, end_time=20.0,
                  score=0.9, reasoning="r", status=status, extra_metadata={})
    db.add(x)
    db.commit()
    return x


def _clip(db, a, status):
    k = Clip(campaign_id=a.campaign_id, asset_id=a.id, status=status, file_path="/x.mp4")
    db.add(k)
    db.commit()
    db.refresh(k)
    return k


def _job(db, status, **payload):
    j = Job(job_type="render", status=status, payload={k: str(v) for k, v in payload.items()})
    db.add(j)
    db.commit()
    return j


def _check(client, auth_headers, **body):
    r = client.post("/worker/storage/check", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_requires_auth(client):
    assert client.post("/worker/storage/check", json={}).status_code in (401, 403)


def test_asset_done_only_when_all_candidates_decided_and_no_active_jobs(client, auth_headers, db):
    c = _campaign(db)
    done = _asset(db, c)
    _cand(db, done, CandidateStatus.REJECTED.value)
    _cand(db, done, CandidateStatus.RENDERED.value)
    undecided = _asset(db, c)
    _cand(db, undecided, CandidateStatus.APPROVED.value)
    _cand(db, undecided, CandidateStatus.PENDING.value)
    busy = _asset(db, c)
    _cand(db, busy, CandidateStatus.APPROVED.value)
    _job(db, "pending", asset_id=busy.id, campaign_id=c.id)
    no_cands = _asset(db, c)
    downloaded = _asset(db, c, status=AssetStatus.DOWNLOADED.value)
    failed = _asset(db, c, status=AssetStatus.FAILED.value)
    finished = _asset(db, c)
    _cand(db, finished, CandidateStatus.APPROVED.value)
    _job(db, "completed", asset_id=finished.id)
    ids = [done, undecided, busy, no_cands, downloaded, failed, finished]
    out = _check(client, auth_headers, asset_ids=[str(a.id) for a in ids] + ["nope", str(uuid.uuid4())])
    a = out["assets"]
    assert a[str(done.id)]["done"] is True and a[str(done.id)]["campaign_id"] == c.id
    assert a[str(undecided.id)]["done"] is False and a[str(undecided.id)]["idle"] is True
    assert a[str(busy.id)] ["done"] is False and a[str(busy.id)]["idle"] is False
    assert a[str(no_cands.id)]["done"] is False
    assert a[str(downloaded.id)]["done"] is False
    assert a[str(failed.id)]["done"] is True
    assert a[str(finished.id)]["done"] is True
    unknown = [k for k, v in a.items() if not v["known"]]
    assert len(unknown) == 1 and a[unknown[0]]["done"] is False  # "nope" is dropped, random uuid unknown


@pytest.mark.parametrize("job_status", ["pending", "assigned", "processing"])
def test_active_statuses_block(client, auth_headers, db, job_status):
    c = _campaign(db, status="archived")
    a = _asset(db, c, status=AssetStatus.FAILED.value)
    _job(db, job_status, asset_id=a.id)
    out = _check(client, auth_headers, asset_ids=[str(a.id)], campaign_ids=[c.id])
    assert out["assets"][str(a.id)]["done"] is False
    assert out["campaigns"][str(c.id)] == {"known": True, "status": "archived", "active_jobs": True, "delete": False}


def test_campaign_delete_only_archived_and_idle(client, auth_headers, db):
    archived = _campaign(db, status="archived")
    parked = _campaign(db, status="parked")
    scored = _campaign(db, status="scored")
    busy = _campaign(db, status="archived")
    _job(db, "processing", campaign_id=busy.id)
    out = _check(client, auth_headers, campaign_ids=[archived.id, parked.id, scored.id, busy.id, 99999999, "x"])
    cs = out["campaigns"]
    assert cs[str(archived.id)]["delete"] is True
    assert cs[str(parked.id)]["delete"] is False
    assert cs[str(scored.id)]["delete"] is False
    assert cs[str(busy.id)]["delete"] is False
    assert cs["99999999"] == {"known": False, "delete": False}


def test_clip_release(client, auth_headers, db):
    c = _campaign(db)
    a = _asset(db, c)
    rejected = _clip(db, a, "rejected")
    published = _clip(db, a, "published")
    approved = _clip(db, a, "approved")
    publishing = _clip(db, a, "rejected")
    _job(db, "pending", clip_id=publishing.id)
    out = _check(client, auth_headers, clip_ids=[str(k.id) for k in (rejected, published, approved, publishing)])
    k = out["clips"]
    assert k[str(rejected.id)]["release"] is True
    assert k[str(published.id)]["release"] is True
    assert k[str(approved.id)]["release"] is False
    assert k[str(publishing.id)] == {"known": True, "status": "rejected", "active": True, "release": False}


def test_check_is_read_only(client, auth_headers, db):
    c = _campaign(db, status="archived")
    a = _asset(db, c, status=AssetStatus.FAILED.value)
    _check(client, auth_headers, asset_ids=[str(a.id)], campaign_ids=[c.id])
    db.expire_all()
    assert db.get(Campaign, c.id).status == "archived"
    assert db.get(Asset, a.id).status == AssetStatus.FAILED.value
