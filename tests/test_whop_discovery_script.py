"""Issue #21: scripts/whop_discovery.py end-to-end against the test DB (no network)."""
from __future__ import annotations

import argparse
import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.database import SessionLocal
from app.services.discovery.whop_catalog import CatalogClient
from tests.services.discovery.test_whop_catalog import FakeOpener, card, detail

import urllib.error

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    spec = importlib.util.spec_from_file_location("whop_discovery_script", ROOT / "scripts" / "whop_discovery.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def script():
    return _load_script()


@pytest.fixture
def session():
    s = SessionLocal()
    created_urls: list[str] = []
    yield s, created_urls
    s.rollback()
    for u in created_urls:
        s.execute(text("DELETE FROM campaigns WHERE source_url = :u"), {"u": u})
    s.commit()
    s.close()


def _args(max_active, dry_run=False):
    return argparse.Namespace(max_active=max_active, dry_run=dry_run, shortlist=None, limit=None, fetch_detail=None)


def _client(routes):
    return CatalogClient("https://cr.test", opener=FakeOpener(routes), sleep=lambda s: None, retries=1, backoff_s=0, pause_s=0)


def test_inserts_best_ranked_up_to_free_slots_with_metadata(script, session):
    s, created = session
    exp = "exp_T" + uuid.uuid4().hex[:8]
    ids = [f"t-{uuid.uuid4().hex[:8]}" for _ in range(3)]
    cards = [
        card(ids[0], yt=150, budget=500_000, spent=0, exp=exp),
        card(ids[1], yt=300, budget=2_000_000, spent=100_000, exp=exp),
        card(ids[2], yt=50, exp=exp),  # rate too low
    ]
    cards[1]["name"] = "Top " + ids[1]
    cards[0]["name"] = "Second " + ids[0]
    routes = {"discover?": {"success": True, "data": cards, "pagination": {}}}
    for i in ids:
        routes[f"/campaigns/{i}"] = {"success": True, "data": detail(i)}
    created += [f"https://whop.com/experiences/{exp}/campaigns/{i}" for i in ids]

    active = script._active_count(s)
    code, summary = script.run(_args(active + 1), s, client=_client(routes))
    assert code == 0, summary
    assert summary["catalog"] == 3 and summary["filtered"] == 2 and summary["upserted"] == 1
    row = s.execute(text("SELECT status, source_metadata FROM campaigns WHERE source_url=:u"),
                    {"u": created[1]}).one()
    assert row.status == "discovered"
    econ = row.source_metadata["economics"]
    assert econ["target_rate_usd"] == 3.0 and econ["remaining_usd"] == 19_000.0
    assert econ["platform_rates"]["instagram"]["max_payout_usd"] == 800.0
    assert econ["expected_value"] > 0
    assert row.source_metadata["detail"]["content_types"] == ["clipping"]
    assert row.source_metadata["detail"]["guidelines"]
    assert "economics" not in row.source_metadata["discovered"]
    # second-best not inserted (no slot)
    assert s.execute(text("SELECT count(*) FROM campaigns WHERE source_url=:u"), {"u": created[0]}).scalar() == 0


def test_no_slots_skips_network(script, session):
    s, _ = session
    def boom(*a, **k):
        raise AssertionError("must not hit network")
    c = CatalogClient("https://cr.test", opener=boom, sleep=lambda s: None)
    code, summary = script.run(_args(0), s, client=c)
    assert code == 0 and summary["slots"] == 0 and c.requests_made == 0


def test_network_error_returns_exit_2_with_error(script, session):
    s, _ = session
    c = _client({"discover?": urllib.error.URLError("Temporary failure in name resolution")})
    code, summary = script.run(_args(script._active_count(s) + 3), s, client=c)
    assert code == 2
    assert "catalog_fetch_failed" in summary["error"] and "name resolution" in summary["error"]
    assert summary["upserted"] == 0


def test_main_exit_code_on_network_error(script, monkeypatch, capsys):
    import app.services.discovery.whop_catalog as wc
    def failing_open(*a, **k):
        raise urllib.error.URLError("dns down")
    monkeypatch.setattr(wc.urllib.request, "urlopen", failing_open)
    monkeypatch.setenv("DISCOVERY_HTTP_RETRIES", "0")
    monkeypatch.setenv("DISCOVERY_MAX_ACTIVE", "999")
    monkeypatch.setattr(script, "_setup_logging", lambda: None)
    rc = script.main(["--max-active", "999"])
    out = capsys.readouterr()
    assert rc == 2
    assert '"error": "catalog_fetch_failed' in out.out
