"""Issue #72: queue slot ranking and the reversible sync migration."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.campaign_closed import append_budget_point
from app.services.discovery.slots import rank_for_download

ROOT = Path(__file__).resolve().parents[1]


def test_rank_for_download_orders_by_score_then_id():
    camps = [
        SimpleNamespace(id=1, source_metadata={"score": {"value": 10}}),
        SimpleNamespace(id=2, source_metadata={"score": {"value": 90}}),
        SimpleNamespace(id=3, source_metadata={"score": {"total": 40}}),
        SimpleNamespace(id=4, source_metadata={"economics": {"expected_value": 70}}),
    ]
    assert [c.id for c in rank_for_download(camps, 3)] == [2, 4, 3]
    assert rank_for_download(camps, 0) == []
    assert rank_for_download(camps, -1) == []


def test_budget_history_dedupes_an_unchanged_utc_day():
    first = {"at": "2026-10-13T01:00:00+00:00", "remaining_usd": 1000, "whop_status": "active"}
    later = {"at": "2026-10-13T18:00:00+00:00", "remaining_usd": 1000, "whop_status": "active"}
    changed = {"at": "2026-10-13T19:00:00+00:00", "remaining_usd": 900, "whop_status": "active"}
    assert append_budget_point([first], later) == [first]
    assert append_budget_point([first], changed) == [first, changed]


def test_migration_0023_requeues_without_deleting():
    src = (ROOT / "alembic" / "versions" / "0023_campaign_queue_sync.py").read_text(encoding="utf-8")
    assert 'revision = "0023_campaign_queue_sync"' in src
    assert 'down_revision = "0022_candidate_needs_review"' in src
    assert "last_synced_at" in src and "budget_history" in src and "queued" in src
    assert "UPDATE campaigns SET status = 'discovered' WHERE status = 'queued'" in src
    assert "DELETE FROM campaigns" not in src
