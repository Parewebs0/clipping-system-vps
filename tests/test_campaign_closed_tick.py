"""Issue #23 / #72: campaign_closed_tick refreshes, parks, or archives."""
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
from app.services.campaign_closed import campaign_uuid_from_url, disposition, park_reasons
from app.services.discovery.whop_catalog import CatalogClient
from tests.services.discovery.test_whop_catalog import FakeOpener

ROOT = Path(__file__).resolve().parents[1]
TH = {"max_spent_pct": 95.0, "min_remaining_usd": 250.0, "platform": "youtube", "content_type": "clipping"}


@pytest.fixture(autouse=True)
def _budget_env(monkeypatch):
    monkeypatch.setenv("MIN_REMAINING_BUDGET_USD", "500")
    monkeypatch.setenv("MIN_REMAINING_BUDGET_PCT", "10")


def det(*, status="active", reason="funded", types=("clipping",), budget=1_000_000, spent=100_000,
        platforms=("youtube", "instagram"), req_app=False, show=True):
    return {
        "status": status, "statusReason": reason, "showOnDiscover": show, "requiresApplication": req_app,
        "launchDate": "2026-09-20T00:00:00.000Z",
        "metrics": {"budgetSpentCents": spent, "budgetProgressBps": int(10000 * spent / budget), "creatorCount": 9},
        "configuration": {
            "budget": budget,
            "content": {"contentTypes": [{"id": t} for t in types], "guidelines": "g"},
            "payoutModel": [{"platform": p, "rate": 200, "minPayout": 100, "maxPayout": 10000} for p in platforms],
        },
    }


@pytest.mark.parametrize("kw,expected", [
    ({}, []),
    ({"status": "paused", "reason": "budget_exhausted"}, ["closed:paused/budget_exhausted"]),
    ({"spent": 979_000}, ["almost_exhausted:spent_97.9%", "almost_exhausted:remaining_210usd"]),
    ({"budget": 100_000, "spent": 80_000}, ["almost_exhausted:remaining_200usd"]),
    ({"types": ("sound",)}, ["not_clipping:sound"]),
    ({"types": ()}, ["not_clipping:none"]),
    ({"platforms": ("instagram", "tiktok")}, ["no_youtube"]),
    ({"req_app": True}, ["requires_application"]),
    ({"show": False}, ["hidden_from_discover"]),
])
def test_park_reasons(kw, expected):
    reasons, econ, summ = park_reasons(det(**kw), th=TH)
    assert reasons == expected


def test_disposition_archives_closed_or_empty_and_parks_on_either_threshold():
    th = {
        "max_spent_pct": 90.0,
        "min_remaining_usd": 500.0,
        "min_remaining_pct": 10.0,
        "platform": "youtube",
        "content_type": "clipping",
    }
    reasons, econ, _ = park_reasons(det(budget=200_000, spent=160_000), th=th)
    assert reasons == ["almost_exhausted:remaining_400usd"]
    assert disposition(reasons, econ) == "parked"
    reasons, econ, _ = park_reasons(det(budget=1_000_000, spent=920_000), th=th)
    assert reasons == ["below_remaining_pct:8.0%"]
    assert disposition(reasons, econ) == "parked"
    reasons, econ, _ = park_reasons(det(budget=100_000, spent=100_000), th=th)
    assert reasons == ["budget_empty"]
    assert disposition(reasons, econ) == "archived"
    reasons, econ, _ = park_reasons(det(status="paused", reason="budget_exhausted"), th=th)
    assert disposition(reasons, econ) == "archived"


def test_no_fit_only_checks_closure():
    reasons, _, _ = park_reasons(det(types=("sound",), platforms=("x",)), check_fit=False, th=TH)
    assert reasons == []


def test_economics_from_detail_payload():
    _, econ, summ = park_reasons(det(), th=TH)
    assert econ["remaining_usd"] == 9000.0 and econ["target_rate_usd"] == 2.0
    assert set(econ["platforms"]) == {"youtube", "instagram"}
    assert summ["content_types"] == ["clipping"]


def test_uuid_from_url():
    assert campaign_uuid_from_url("https://whop.com/experiences/exp_A/campaigns/abc-123") == "abc-123"
    assert campaign_uuid_from_url("https://whop.com/experiences/exp_A/campaigns/abc-123/?x=1") == "abc-123"
    assert campaign_uuid_from_url(None) is None
    assert campaign_uuid_from_url("https://example.com") is None


def _load():
    spec = importlib.util.spec_from_file_location("closed_tick", ROOT / "scripts" / "campaign_closed_tick.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def camps():
    s = SessionLocal()
    made = []

    def make(status="briefed"):
        uid = uuid.uuid4().hex
        c = Campaign(name=f"closed-{uid[:8]}", status=status, source_provider="whop",
                     source_url=f"https://whop.com/experiences/exp_T/campaigns/{uid}",
                     source_metadata={"discovered": {"name": "x"}})
        s.add(c)
        s.commit()
        s.refresh(c)
        made.append(c.id)
        return c, uid

    yield s, make
    s.rollback()
    s.execute(text("DELETE FROM campaigns WHERE id = ANY(:ids)"), {"ids": made})
    s.commit()
    s.close()


def _client(routes):
    return CatalogClient("https://cr.test", opener=FakeOpener(routes), sleep=lambda s: None, retries=0, pause_s=0)


def _args(cid, **kw):
    return argparse.Namespace(limit=100, campaign_id=cid, no_fit=kw.get("no_fit", False), dry_run=kw.get("dry_run", False))


def test_tick_archives_paused_keeps_render_and_keeps_healthy(camps, monkeypatch):
    monkeypatch.setenv("MIN_REMAINING_BUDGET_USD", "500")
    monkeypatch.setenv("MIN_REMAINING_BUDGET_PCT", "10")
    s, make = camps
    m = _load()
    bad, bad_uid = make("failed_resolve")
    good, good_uid = make("scored")
    job = Job(job_type="render", status="processing", payload={"campaign_id": bad.id})
    s.add(job)
    s.commit()
    routes = {
        f"/campaigns/{bad_uid}": {"success": True, "data": det(status="paused", reason="budget_exhausted")},
        f"/campaigns/{good_uid}": {"success": True, "data": det()},
    }
    code, summ = m.run(_args(bad.id), s, client=_client(routes))
    assert code == 0 and summ.get("archived") == 1 and summ["parked"] == 0
    code, summ = m.run(_args(good.id), s, client=_client(routes))
    assert code == 0 and summ["kept"] == 1
    s.expire_all()
    b = s.get(Campaign, bad.id)
    g = s.get(Campaign, good.id)
    assert b.status == "archived" and g.status == "scored"
    assert s.get(Job, job.id).status == "processing"
    h = b.source_metadata["status_history"][-1]
    assert h["from"] == "failed_resolve" and h["to"] == "archived" and h["by"] == "campaign_closed_tick"
    assert "closed:paused/budget_exhausted" in h["reason"]
    assert b.source_metadata["archive"]["previous_status"] == "failed_resolve"
    assert b.last_synced_at is not None and b.budget_history[-1]["remaining_usd"] == 9000.0
    assert g.source_metadata["economics"]["remaining_usd"] == 9000.0
    assert g.source_metadata["closed_check"]["reasons"] == []
    assert g.source_metadata["detail"]["content_types"] == ["clipping"]


def test_tick_404_archives_not_found(camps):
    s, make = camps
    c, uid = make()
    err = urllib.error.HTTPError("u", 404, "nf", {}, None)
    code, summ = _load().run(_args(c.id), s, client=_client({f"/campaigns/{uid}": err}))
    s.expire_all()
    assert s.get(Campaign, c.id).status == "archived"
    assert summ["campaigns"][0]["reasons"] == ["not_found_on_source"]
    assert summ.get("archived") == 1


def test_tick_network_error_exits_2_and_parks_nothing(camps):
    s, make = camps
    c, uid = make()
    code, summ = _load().run(_args(c.id), s, client=_client({f"/campaigns/{uid}": urllib.error.URLError("dns")}))
    s.expire_all()
    assert code == 2 and summ["errors"] == 1
    assert s.get(Campaign, c.id).status == "briefed"


def test_tick_parks_under_remaining_threshold_without_archiving(camps, monkeypatch):
    monkeypatch.setenv("MIN_REMAINING_BUDGET_USD", "500")
    monkeypatch.setenv("MIN_REMAINING_BUDGET_PCT", "10")
    s, make = camps
    c, uid = make("scored")
    code, summ = _load().run(
        _args(c.id), s,
        client=_client({f"/campaigns/{uid}": {"success": True, "data": det(budget=200_000, spent=160_000)}}),
    )
    s.expire_all()
    assert code == 0 and summ["parked"] == 1 and summ.get("archived", 0) == 0
    row = s.get(Campaign, c.id)
    assert row.status == "parked"
    assert "almost_exhausted:remaining_400usd" in row.source_metadata["park"]["reasons"]


def test_tick_dry_run_does_not_write(camps):
    s, make = camps
    c, uid = make()
    code, summ = _load().run(_args(c.id, dry_run=True), s,
                             client=_client({f"/campaigns/{uid}": {"success": True, "data": det(types=("sound",))}}))
    s.expire_all()
    assert summ["parked"] == 1 and s.get(Campaign, c.id).status == "briefed"


def test_parked_is_terminal_for_ticks_and_not_active():
    import ast
    tree = ast.parse((ROOT / "scripts" / "whop_discovery.py").read_text())
    active = next(ast.literal_eval(n.value) for n in tree.body
                  if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ACTIVE_STATUSES")
    assert "parked" not in active
    for f in (ROOT / "scripts").glob("*.py"):
        if f.name == "campaign_closed_tick.py":
            continue
        assert '"parked"' not in f.read_text(), f.name
