"""#31 — pending downloads of non-workable campaigns get cancelled."""
import uuid

import pytest
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.campaign import Campaign
from app.models.job import Job
from app.services.download_gate import cancel_unworkable


@pytest.fixture
def s():
    s = SessionLocal()
    made = []
    yield s, made
    s.rollback()
    s.execute(text("DELETE FROM jobs WHERE job_type='download'"))
    s.execute(text("DELETE FROM campaigns WHERE id = ANY(:ids)"), {"ids": made})
    s.commit()
    s.close()


def _camp(s, made, status):
    c = Campaign(name=f"dg-{uuid.uuid4().hex[:8]}", status=status, source_provider="whop",
                 source_url=f"https://whop.com/c/{uuid.uuid4().hex}", source_metadata={})
    s.add(c)
    s.commit()
    made.append(c.id)
    return c


def _job(s, cid, status="pending"):
    j = Job(job_type="download", status=status, payload={"campaign_id": str(cid), "asset_id": str(uuid.uuid4())})
    s.add(j)
    s.commit()
    return j


def test_cancels_only_pending_of_unworkable(s):
    db, made = s
    good = _camp(db, made, "scored")
    bad = _camp(db, made, "blocked_low_score")
    parked = _camp(db, made, "parked")
    j_ok = _job(db, good.id)
    j_bad = _job(db, bad.id)
    j_parked = _job(db, parked.id)
    j_running = _job(db, bad.id, status="processing")
    assert cancel_unworkable(db) == 2
    for j in (j_ok, j_bad, j_parked, j_running):
        db.refresh(j)
    assert j_ok.status == "pending"
    assert j_bad.status == "cancelled" and "not workable" in j_bad.error_message
    assert j_parked.status == "cancelled"
    assert j_running.status == "processing"


def test_dry_run_changes_nothing(s):
    db, made = s
    bad = _camp(db, made, "parked")
    j = _job(db, bad.id)
    assert cancel_unworkable(db, dry_run=True) == 1
    db.refresh(j)
    assert j.status == "pending"
