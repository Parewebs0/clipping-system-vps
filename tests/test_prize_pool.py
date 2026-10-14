"""MOLI-56: original prize pool (remaining / original) persistence + API.

Covers:
  * merge_prize_pool: first-seen wins, top-ups bump, garbage is ignored.
  * apply_refresh stamps/bumps campaigns.prize_pool_usd on every sync.
  * upsert_campaign fills the column at discovery (create) and bumps it.
  * migration 0024 add/backfill/downgrade shape (source assertions, like 0023).
  * Mission Control list + detail expose prize_pool_usd / remaining_usd /
    spent_pct, with the budget_history fallback for the money block.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.services.campaign_closed import apply_refresh, merge_prize_pool
from app.services.discovery.models import DiscoveredCampaign
from app.services.discovery.upsert import upsert_campaign

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# merge_prize_pool (calculation)
# ---------------------------------------------------------------------------

def test_merge_prize_pool_first_seen_wins_and_garbage_ignored():
    assert merge_prize_pool(None, 5000.0) == 5000.0
    assert merge_prize_pool(5000.0, 4000.0) == 5000.0
    assert merge_prize_pool(None, None) is None
    assert merge_prize_pool(None, "abc") is None
    assert merge_prize_pool(5000.0, 0) == 5000.0
    assert merge_prize_pool(5000.0, -100) == 5000.0
    assert merge_prize_pool(5000.0, "6000") == 6000.0


def test_merge_prize_pool_bumps_on_topup():
    assert merge_prize_pool(5000.0, 8000.0) == 8000.0
    assert merge_prize_pool(None, 238000.0) == 238000.0


# ---------------------------------------------------------------------------
# apply_refresh + upsert (persistence)
# ---------------------------------------------------------------------------

_ECON = {"budget_usd": 5000.0, "remaining_usd": 3850.0, "spent_pct": 23.0,
         "target_rate_usd": 1.5, "platforms": ["youtube"]}


def _campaign(**kw) -> SimpleNamespace:
    base = dict(status="discovered", source_metadata={}, budget_history=[],
                prize_pool_usd=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_apply_refresh_sets_and_bumps_prize_pool_column():
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    camp = _campaign()
    assert apply_refresh(camp, [], dict(_ECON), {"status": "active"},
                         now=now, actor="t") == "kept"
    assert camp.prize_pool_usd == 5000.0
    # Top-up: Whop raised the total — bump, never lower.
    assert apply_refresh(camp, [], {**_ECON, "budget_usd": 8000.0,
                                    "remaining_usd": 7000.0},
                         {"status": "active"}, now=now, actor="t") == "kept"
    assert camp.prize_pool_usd == 8000.0
    # A sync that fails to read the budget does not wipe the stored total.
    assert apply_refresh(camp, [], {"remaining_usd": 100.0}, {"status": "active"},
                         now=now, actor="t") == "kept"
    assert camp.prize_pool_usd == 8000.0

    # Metadata copy stays in sync for older readers of source_metadata.
    assert camp.source_metadata["prize_pool_usd"] == 8000.0


def test_upsert_campaign_fills_prize_pool_on_create_and_bump(db):
    url = f"https://whop.com/experiences/exp-{uuid.uuid4().hex[:8]}/campaigns/{uuid.uuid4().hex[:8]}"
    d = DiscoveredCampaign(
        provider="whop", external_id=uuid.uuid4().hex, detail_url=url,
        name=f"pool-{uuid.uuid4().hex[:6]}", prize_pool_usd=5000.0,
        economics={"budget_usd": 5000.0, "remaining_usd": 3850.0},
    )
    created = upsert_campaign(db, d, status="queued")
    assert created.prize_pool_usd == 5000.0

    # Same campaign re-discovered with a topped-up budget: bump, not reset.
    d2 = d.model_copy(update={"prize_pool_usd": 4500.0,
                              "economics": {"budget_usd": 8000.0,
                                            "remaining_usd": 7000.0}})
    updated = upsert_campaign(db, d2, status="queued")
    assert updated.id == created.id
    assert updated.prize_pool_usd == 8000.0


# ---------------------------------------------------------------------------
# Migration 0024 shape
# ---------------------------------------------------------------------------

def test_migration_0024_backfills_without_destroying():
    src = (ROOT / "alembic" / "versions" / "0024_campaign_prize_pool.py").read_text(encoding="utf-8")
    assert 'revision = "0024_campaign_prize_pool"' in src
    assert 'down_revision = "0023_campaign_queue_sync"' in src
    assert "prize_pool_usd" in src
    assert "jsonb_typeof" in src  # garbage-safe backfill
    assert "WHERE prize_pool_usd IS NULL" in src
    assert "op.drop_column" in src  # safe downgrade
    assert "DELETE FROM" not in src


# ---------------------------------------------------------------------------
# Mission Control money block (API)
# ---------------------------------------------------------------------------

@pytest.fixture
def mc_client(monkeypatch):
    monkeypatch.setattr(settings, "mission_control_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "mc-test-token", raising=False)
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _insert_campaign(sm: dict, pool: float | None, history: list | None) -> int:
    s = SessionLocal()
    try:
        n = f"mc-pool-{uuid.uuid4().hex[:8]}"
        s.execute(
            text(
                "INSERT INTO campaigns (name, status, source_provider, spec,"
                " source_metadata, prize_pool_usd, budget_history)"
                " VALUES (:n, 'discovered', 'whop', '{}'::jsonb,"
                " CAST(:sm AS jsonb), :pool, CAST(:hist AS jsonb))"
            ),
            {
                "n": n,
                "sm": json.dumps(sm),
                "pool": pool,
                "hist": json.dumps(history or []),
            },
        )
        s.commit()
        return s.execute(text("SELECT id FROM campaigns WHERE name = :n"), {"n": n}).scalar_one()
    finally:
        s.close()


def _delete_campaign(cid: int) -> None:
    s = SessionLocal()
    try:
        s.execute(text("DELETE FROM campaigns WHERE id = :id"), {"id": cid})
        s.commit()
    finally:
        s.close()


def test_mission_control_exposes_prize_pool_fields(mc_client, auth_headers):
    cid = _insert_campaign(
        {"economics": {"remaining_usd": 3850.0, "spent_pct": 23.0},
         "prize_pool_usd": 5000.0},
        pool=5000.0, history=[],
    )
    try:
        r = mc_client.get("/mission-control/campaigns", headers=auth_headers)
        assert r.status_code == 200, r.text
        item = next(i for i in r.json()["items"] if i["id"] == cid)
        assert item["prize_pool_usd"] == 5000.0
        assert item["remaining_usd"] == 3850.0
        assert item["spent_pct"] == 23.0

        r = mc_client.get(f"/mission-control/campaigns/{cid}", headers=auth_headers)
        assert r.status_code == 200, r.text
        c = r.json()["campaign"]
        assert c["prize_pool_usd"] == 5000.0
        assert c["remaining_usd"] == 3850.0
        assert c["spent_pct"] == 23.0
    finally:
        _delete_campaign(cid)


def test_mission_control_falls_back_to_columns_and_history(mc_client, auth_headers):
    # Column not yet backfilled for this synthetic row: metadata carries the
    # pool; economics is empty, so the last budget_history point feeds
    # remaining/spent (0023 belt-and-braces path).
    cid = _insert_campaign(
        {"prize_pool_usd": 8000.0},
        pool=None,
        history=[{"at": "2026-10-12T10:00:00+00:00", "remaining_usd": 900.0,
                  "spent_pct": 10.0},
                 {"at": "2026-10-13T10:00:00+00:00", "remaining_usd": 1200.0,
                  "spent_pct": 85.0}],
    )
    try:
        r = mc_client.get("/mission-control/campaigns", headers=auth_headers)
        assert r.status_code == 200, r.text
        item = next(i for i in r.json()["items"] if i["id"] == cid)
        assert item["prize_pool_usd"] == 8000.0

        # List has no budget_history in the query; remaining stays null there.
        assert item["remaining_usd"] is None

        r = mc_client.get(f"/mission-control/campaigns/{cid}", headers=auth_headers)
        c = r.json()["campaign"]
        assert c["remaining_usd"] == 1200.0
        assert c["spent_pct"] == 85.0
    finally:
        _delete_campaign(cid)