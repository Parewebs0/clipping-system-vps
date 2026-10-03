"""#29 — ticks must not overwrite a status another tick set meanwhile."""
import uuid

import pytest
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.campaign import Campaign
from app.services.campaign_transitions import lock_if_status


@pytest.fixture
def make():
    s = SessionLocal()
    made = []

    def _make(status):
        c = Campaign(name=f"race-{uuid.uuid4().hex[:8]}", status=status, source_provider="whop",
                     source_url=f"https://whop.com/c/{uuid.uuid4().hex}", source_metadata={})
        s.add(c)
        s.commit()
        made.append(c.id)
        return c

    yield s, _make
    s.rollback()
    s.execute(text("DELETE FROM campaigns WHERE id = ANY(:ids)"), {"ids": made})
    s.commit()
    s.close()


def test_lock_if_status_sees_concurrent_park(make):
    s, _make = make
    c = _make("failed_resolve")
    _ = c.status  # loaded in this session (stale snapshot)
    other = SessionLocal()
    other.execute(text("UPDATE campaigns SET status='parked' WHERE id=:i"), {"i": c.id})
    other.commit()
    other.close()
    c.status = "assets_resolved"  # stale in-memory write, must be discarded
    assert lock_if_status(s, c, ("briefed", "failed_resolve", "assets_resolved")) is False
    assert c.status == "parked"
    s.commit()


def test_lock_if_status_true_when_unchanged(make):
    s, _make = make
    c = _make("assets_resolved")
    assert lock_if_status(s, c, ("assets_resolved",)) is True
    s.commit()
