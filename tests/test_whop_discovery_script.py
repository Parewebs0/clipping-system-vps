"""Issue #21 / #72: scripts/whop_discovery.py against the test DB (no network)."""
from __future__ import annotations

import argparse
import importlib.util
import urllib.error
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.campaign import Campaign
from app.models.job import Job
from app.services.discovery.whop_catalog import CatalogClient, compute_economics, expected_value
from tests.services.discovery.test_whop_catalog import FakeOpener, card, detail

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


@pytest.fixture(autouse=True)
def _stable_thresholds(monkeypatch):
    monkeypatch.setenv("DISCOVERY_MIN_REMAINING_USD", "1500")
    monkeypatch.setenv("DISCOVERY_MAX_SPENT_PCT", "85")
    monkeypatch.setenv("DISCOVERY_MIN_RATE_USD", "1.0")
    monkeypatch.setenv("DISCOVERY_PLATFORM", "youtube")
    monkeypatch.setenv("DISCOVERY_CONTENT_TYPE", "clipping")
    monkeypatch.setenv("DISCOVERY_ALLOW_APPLICATION", "0")
    monkeypatch.setenv("MIN_REMAINING_BUDGET_USD", "500")
    monkeypatch.setenv("MIN_REMAINING_BUDGET_PCT", "10")
    monkeypatch.setenv("DISCOVERY_PAUSE_S", "0")
    monkeypatch.setenv("DISCOVERY_HTTP_BACKOFF_S", "0")
    monkeypatch.setenv("DISCOVERY_HTTP_RETRIES", "0")


def _args(max_active, dry_run=False):
    return argparse.Namespace(max_active=max_active, dry_run=dry_run, shortlist=None, limit=None, fetch_detail=None)


class _QuietOpener:
    """Unknown detail URLs become a network error so leftover rows are not rewritten."""

    def __init__(self, routes):
        self.inner = FakeOpener(routes)

    def __call__(self, req, timeout=None):
        try:
            return self.inner(req, timeout)
        except AssertionError as exc:
            raise urllib.error.URLError(str(exc))


def _client(routes):
    return CatalogClient(
        "https://cr.test", opener=_QuietOpener(routes), sleep=lambda s: None, retries=0, backoff_s=0, pause_s=0,
    )


def _money_detail(*, status="active", reason="funded", budget=1_000_000, spent=100_000):
    return {
        "status": status,
        "statusReason": reason,
        "showOnDiscover": True,
        "requiresApplication": False,
        "launchDate": "2026-09-20T00:00:00.000Z",
        "metrics": {
            "budgetSpentCents": spent,
            "budgetProgressBps": int(10000 * spent / budget) if budget else 0,
            "creatorCount": 9,
        },
        "configuration": {
            "budget": budget,
            "content": {"contentTypes": [{"id": "clipping"}], "guidelines": "g"},
            "payoutModel": [{"platform": "youtube", "rate": 200, "minPayout": 100, "maxPayout": 10000}],
        },
    }


def _add(s, created, *, status, meta=None):
    uid = uuid.uuid4().hex
    url = f"https://whop.com/experiences/exp_Q/campaigns/{uid}"
    camp = Campaign(
        name=f"q-{uid[:12]}",
        status=status,
        source_provider="whop",
        source_url=url,
        source_metadata=meta or {"economics": {"remaining_usd": 17000}},
    )
    s.add(camp)
    s.commit()
    s.refresh(camp)
    created.append(url)
    return camp, uid


def _queued_ceiling(s) -> float:
    best = 0.0
    rows = s.execute(text("SELECT source_metadata FROM campaigns WHERE status = 'queued'")).all()
    for (meta,) in rows:
        meta = meta or {}
        raw = meta.get("queue_score")
        if raw is None:
            raw = (meta.get("economics") or {}).get("expected_value")
        try:
            best = max(best, float(raw or 0))
        except (TypeError, ValueError):
            continue
    return best


def _card_above(cid, floor, *, exp, budget=2_000_000, spent=100_000, yt=300):
    while True:
        built = card(cid, yt=yt, budget=budget, spent=spent, exp=exp)
        ev = expected_value(compute_economics(built))
        if ev > floor:
            return built, ev
        yt *= 2


def _row(s, url):
    return s.execute(
        text("SELECT status, source_metadata, last_synced_at, budget_history FROM campaigns WHERE source_url=:u"),
        {"u": url},
    ).one()


def test_inserts_every_passing_card_and_activates_only_the_best(script, session):
    s, created = session
    exp = "exp_T" + uuid.uuid4().hex[:8]
    ids = [f"t-{uuid.uuid4().hex[:8]}" for _ in range(3)]
    best, _ev = _card_above(ids[1], _queued_ceiling(s), exp=exp)
    second = card(ids[0], yt=150, budget=500_000, spent=0, exp=exp)
    low = card(ids[2], yt=50, exp=exp)
    best["name"] = "Top " + ids[1]
    second["name"] = "Second " + ids[0]
    routes = {"discover?": {"success": True, "data": [second, best, low], "pagination": {}}}
    for i in ids:
        routes[f"/campaigns/{i}"] = {"success": True, "data": detail(i)}
    created += [f"https://whop.com/experiences/{exp}/campaigns/{i}" for i in ids]

    active = script._active_count(s)
    code, summary = script.run(_args(active + 1), s, client=_client(routes))
    assert code == 0, summary
    assert summary["catalog"] == 3 and summary["filtered"] == 2 and summary["upserted"] == 2
    assert summary["entered"] == 2 and summary["activated"] == 1
    assert "refreshed=" in summary["log"] and "entered=" in summary["log"]
    assert "archived=" in summary["log"] and "activated=" in summary["log"]
    top = _row(s, created[1])
    other = _row(s, created[0])
    assert top.status == "discovered"
    assert other.status == "queued"
    econ = top.source_metadata["economics"]
    assert econ["remaining_usd"] == 19_000.0
    assert econ["platform_rates"]["instagram"]["max_payout_usd"] == 800.0
    assert econ["expected_value"] > 0
    assert top.source_metadata["detail"]["content_types"] == ["clipping"]
    assert top.source_metadata["detail"]["guidelines"]
    assert "economics" not in top.source_metadata["discovered"]
    assert s.execute(text("SELECT count(*) FROM campaigns WHERE source_url=:u"), {"u": created[2]}).scalar() == 0


def test_full_slots_still_refresh_budgets_and_queue_new_cards(script, session):
    s, created = session
    before = script._active_count(s)
    held = []
    for _ in range(3):
        camp, uid = _add(s, created, status="scored")
        held.append((camp, uid))
    assert script._active_count(s) == before + 3
    exp = "exp_N" + uuid.uuid4().hex[:8]
    nid = "n-" + uuid.uuid4().hex[:8]
    fresh = card(nid, yt=200, budget=800_000, spent=0, exp=exp)
    routes = {"discover?": {"success": True, "data": [fresh], "pagination": {}}}
    routes[f"/campaigns/{nid}"] = {"success": True, "data": detail(nid)}
    for _camp, uid in held:
        routes[f"/campaigns/{uid}"] = {"success": True, "data": _money_detail()}
    new_url = f"https://whop.com/experiences/{exp}/campaigns/{nid}"
    created.append(new_url)

    code, summary = script.run(_args(script._active_count(s)), s, client=_client(routes))
    assert code == 0, summary
    assert summary["slots"] == 0 and summary["activated"] == 0 and summary["entered"] == 1
    assert summary["refreshed"] >= 3 and summary["requests"] > 0
    assert _row(s, new_url).status == "queued"
    for camp, _uid in held:
        row = _row(s, camp.source_url)
        assert row.status == "scored"
        assert row.source_metadata["economics"]["remaining_usd"] == 9000.0
        assert row.last_synced_at is not None
        assert row.budget_history[-1]["remaining_usd"] == 9000.0


def test_closed_campaign_is_archived_and_render_keeps_running(script, session):
    s, created = session
    camp, uid = _add(s, created, status="scored")
    job = Job(job_type="render", status="processing", payload={"campaign_id": camp.id})
    s.add(job)
    s.commit()
    routes = {
        "discover?": {"success": True, "data": [], "pagination": {}},
        f"/campaigns/{uid}": {"success": True, "data": _money_detail(status="paused", reason="budget_exhausted")},
    }
    try:
        code, summary = script.run(_args(0), s, client=_client(routes))
        assert code == 0, summary
        s.expire_all()
        assert s.get(Campaign, camp.id).status == "archived"
        assert s.get(Job, job.id).status == "processing"
        assert summary["archived"] >= 1 and summary["activated"] == 0
        assert "archived=" in summary["log"]
    finally:
        s.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": str(job.id)})
        s.commit()


def test_low_remaining_parks_instead_of_archiving(script, session):
    s, created = session
    camp, uid = _add(s, created, status="scored")
    routes = {
        "discover?": {"success": True, "data": [], "pagination": {}},
        f"/campaigns/{uid}": {"success": True, "data": _money_detail(budget=200_000, spent=160_000)},
    }
    code, summary = script.run(_args(0), s, client=_client(routes))
    assert code == 0, summary
    s.expire_all()
    row = s.get(Campaign, camp.id)
    assert row.status == "parked"
    assert summary["parked"] >= 1
    assert "almost_exhausted:remaining_400usd" in row.source_metadata["park"]["reasons"]


def test_needs_review_does_not_take_a_slot(script, session):
    s, created = session
    assert "needs_review" not in script.ACTIVE_STATUSES
    before = script._active_count(s)
    for _ in range(3):
        _add(s, created, status="needs_review")
    assert script._active_count(s) == before
    exp = "exp_R" + uuid.uuid4().hex[:8]
    nid = "r-" + uuid.uuid4().hex[:8]
    fresh, _ev = _card_above(nid, _queued_ceiling(s), exp=exp)
    routes = {"discover?": {"success": True, "data": [fresh], "pagination": {}}}
    routes[f"/campaigns/{nid}"] = {"success": True, "data": detail(nid)}
    new_url = f"https://whop.com/experiences/{exp}/campaigns/{nid}"
    created.append(new_url)
    code, summary = script.run(_args(before + 1), s, client=_client(routes))
    assert code == 0, summary
    assert summary["activated"] == 1
    assert _row(s, new_url).status == "discovered"


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
    monkeypatch.setattr(script, "_setup_logging", lambda: None)
    rc = script.main(["--max-active", "999"])
    out = capsys.readouterr()
    assert rc == 2
    assert '"error": "catalog_fetch_failed' in out.out
