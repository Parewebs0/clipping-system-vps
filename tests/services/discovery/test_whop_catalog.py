"""Issue #21: catalog pagination, polite HTTP, hard filters, EV ranking (no network)."""
from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest

from app.services.discovery.whop_catalog import (
    CatalogClient,
    CatalogFetchError,
    DiscoveryFilters,
    compute_economics,
    detail_reject_reason,
    detail_summary,
    expected_value,
    hard_filter_reason,
    select_candidates,
)

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def card(cid, *, yt=200, ig=400, budget=1_000_000, spent=100_000, bps=None, platforms=None,
         creators=10, req_app=False, launch="2026-09-23T12:00:00.000Z", status="active", exp="exp_X"):
    platforms = platforms if platforms is not None else ["instagram", "youtube"]
    payouts = []
    if "youtube" in platforms:
        payouts.append({"platform": "youtube", "rateCents": yt, "minPayoutCents": 500, "maxPayoutCents": 50_000, "payoutType": "cpm"})
    if "instagram" in platforms:
        payouts.append({"platform": "instagram", "rateCents": ig, "minPayoutCents": 100, "maxPayoutCents": 80_000, "payoutType": "cpm"})
    return {
        "id": cid, "name": f"Camp {cid}", "organizationExperienceId": exp, "status": status,
        "budgetCents": budget, "payoutType": "cpm", "platforms": platforms, "payouts": payouts,
        "requiresApplication": req_app, "launchDate": launch, "referenceMaterials": [],
        "metrics": {"budgetSpentCents": spent, "budgetProgressBps": bps if bps is not None else int(10000 * spent / budget),
                    "creatorCount": creators, "approvedSubmissionCount": 5},
    }


def detail(cid, *, types=("clipping",), status="active", req_app=False):
    return {
        "id": cid, "status": status, "statusReason": "funded", "showOnDiscover": True, "requiresApplication": req_app,
        "configuration": {
            "content": {"contentTypes": [{"id": t} for t in types], "guidelines": "- tag @x\n- 30s min"},
            "creator": {"description": "<p>clip it</p>"},
            "requirement": {"linkInBio": True, "preApproval": False},
            "rules": {"sound": {"mustUseProvided": False}},
            "referenceMaterial": [{"url": "https://drive.google.com/drive/folders/abc"}],
        },
    }


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Routes by URL; values are payload dicts or exceptions (popped in order if list)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((url, dict(req.header_items()), timeout))
        for key, val in self.routes.items():
            if key in url:
                v = val.pop(0) if isinstance(val, list) else val
                if isinstance(v, Exception):
                    raise v
                return _Resp(json.dumps(v).encode())
        raise AssertionError(f"unexpected url {url}")


def client_for(routes, **kw):
    sleeps = []
    op = FakeOpener(routes)
    c = CatalogClient("https://cr.test", opener=op, sleep=sleeps.append, retries=kw.pop("retries", 2),
                      backoff_s=kw.pop("backoff_s", 1.0), pause_s=kw.pop("pause_s", 0.5), timeout_s=7, **kw)
    return c, op, sleeps


def test_fetch_catalog_paginates_with_cursor_pause_and_identifiable_ua():
    p1 = {"success": True, "data": [card("a"), card("b")], "pagination": {"nextCursor": "CUR2"}}
    p2 = {"success": True, "data": [card("c"), card("a")], "pagination": {"nextCursor": None}}
    c, op, sleeps = client_for({"cursor=CUR2": p2, "discover?": p1})
    out = c.fetch_catalog()
    assert [x["id"] for x in out] == ["a", "b", "c"]  # deduped
    assert len(op.calls) == 2
    assert "limit=50" in op.calls[0][0] and "cursor" not in op.calls[0][0]
    ua = op.calls[0][1].get("User-agent")
    assert "clipping-system-vps" in ua and "Mozilla" not in ua
    assert op.calls[0][2] == 7
    assert sleeps == [0.5]  # pause between the 2 requests only


def test_retries_with_exponential_backoff_then_succeeds():
    ok = {"success": True, "data": [card("a")], "pagination": {}}
    c, op, sleeps = client_for({"discover?": [urllib.error.URLError("dns"), urllib.error.URLError("dns"), ok]})
    assert [x["id"] for x in c.fetch_catalog()] == ["a"]
    assert 1.0 in sleeps and 2.0 in sleeps  # backoff 1, 2


def test_network_failure_raises_after_retries():
    c, op, sleeps = client_for({"discover?": urllib.error.URLError("Temporary failure in name resolution")}, retries=2)
    with pytest.raises(CatalogFetchError, match="name resolution"):
        c.fetch_catalog()
    assert len(op.calls) == 3


def test_http_4xx_is_not_retried():
    err = urllib.error.HTTPError("u", 403, "forbidden", {}, None)
    c, op, _ = client_for({"discover?": err})
    with pytest.raises(CatalogFetchError, match="403"):
        c.fetch_catalog()
    assert len(op.calls) == 1


def test_unexpected_payload_raises():
    c, _, _ = client_for({"discover?": {"success": False}}, retries=0)
    with pytest.raises(CatalogFetchError):
        c.fetch_catalog()


def test_compute_economics():
    e = compute_economics(card("a", yt=150, budget=1_000_000, spent=250_000, creators=30), now=NOW)
    assert e["budget_usd"] == 10_000 and e["remaining_usd"] == 7_500 and e["spent_pct"] == 25.0
    assert e["target_rate_usd"] == 1.5
    assert e["platform_rates"]["youtube"] == {"rate_usd": 1.5, "min_payout_usd": 5.0, "max_payout_usd": 500.0}
    assert e["platform_rates"]["instagram"]["rate_usd"] == 4.0
    assert e["creators"] == 30
    assert e["burn_usd_per_day"] == 250.0  # 2500$ over 10 days
    assert e["runway_days"] == 30.0


@pytest.mark.parametrize("kw,reason", [
    ({}, None),
    ({"platforms": ["instagram", "tiktok"]}, "no_youtube"),
    ({"yt": 99}, "rate_too_low"),
    ({"budget": 200_000, "spent": 100_000}, "remaining_too_low"),
    ({"budget": 10_000_000, "spent": 8_600_000}, "spent_too_high"),
    ({"req_app": True}, "requires_application"),
    ({"status": "paused"}, "status_not_active"),
])
def test_hard_filters(kw, reason):
    c = card("a", **kw)
    f = DiscoveryFilters()
    assert hard_filter_reason(c, compute_economics(c, now=NOW), f) == reason


def test_filters_from_env(monkeypatch):
    monkeypatch.setenv("DISCOVERY_MIN_RATE_USD", "2.5")
    monkeypatch.setenv("DISCOVERY_MIN_REMAINING_USD", "3000")
    monkeypatch.setenv("DISCOVERY_MAX_SPENT_PCT", "70")
    monkeypatch.setenv("DISCOVERY_ALLOW_APPLICATION", "1")
    monkeypatch.setenv("DISCOVERY_PLATFORM", "TikTok")
    f = DiscoveryFilters.from_env()
    assert (f.min_rate_usd, f.min_remaining_usd, f.max_spent_pct, f.allow_application, f.platform) == (2.5, 3000.0, 70.0, True, "tiktok")


def test_expected_value_penalises_competition_and_short_runway():
    base = {"target_rate_usd": 2.0, "remaining_usd": 5000.0}
    assert expected_value(base) == 10_000.0
    assert expected_value({**base, "creators": 75}) == 5000.0  # / sqrt(4)
    assert expected_value({**base, "runway_days": 3.5}) == 5000.0  # × 3.5/7
    assert expected_value({**base, "runway_days": 30}) == 10_000.0


def test_detail_reject_reason_and_summary():
    f = DiscoveryFilters()
    assert detail_reject_reason(detail("a"), f) is None
    assert detail_reject_reason(detail("a", types=("sound",)), f) == "not_clipping"
    assert detail_reject_reason(detail("a", types=("slideshows",)), f) == "not_clipping"
    assert detail_reject_reason(detail("a", status="paused"), f) == "detail_status_paused"
    s = detail_summary(detail("a"))
    assert s["content_types"] == ["clipping"] and s["guidelines"].startswith("- tag")
    assert s["requirement"]["linkInBio"] is True and s["status_reason"] == "funded"


def test_select_candidates_ranks_skips_known_and_stops_at_want():
    cards = [
        card("low", yt=100, budget=300_000, spent=0),          # EV small
        card("top", yt=300, budget=2_000_000, spent=200_000),   # EV big
        card("mid", yt=200, budget=1_000_000, spent=100_000),
        card("known", yt=300, budget=3_000_000, spent=0),
        card("sound", yt=300, budget=2_500_000, spent=0),
        card("noyt", platforms=["instagram"]),
    ]
    routes = {
        "discover?": {"success": True, "data": cards, "pagination": {}},
        "/campaigns/sound": {"success": True, "data": detail("sound", types=("sound",))},
        "/campaigns/top": {"success": True, "data": detail("top")},
        "/campaigns/mid": {"success": True, "data": detail("mid")},
        "/campaigns/low": {"success": True, "data": detail("low")},
    }
    c, op, _ = client_for(routes)
    acc, stats = select_candidates(c, DiscoveryFilters(), is_known=lambda u: u == "known", now=NOW, want=2)
    assert [a.card["id"] for a in acc] == ["top", "mid"]
    assert stats["catalog"] == 6 and stats["filtered"] == 5
    assert stats["rejected"] == {"no_youtube": 1}
    assert stats["known_skipped"] == 1
    assert stats["detail_rejected"] == {"not_clipping": 1}
    assert not any("/campaigns/low" in u for u, _, _ in op.calls)  # stopped at want
    assert not any("/campaigns/known" in u for u, _, _ in op.calls)


def test_select_candidates_detail_budget_cap():
    cards = [card(f"c{i}", yt=200 + i) for i in range(5)]
    routes = {"discover?": {"success": True, "data": cards, "pagination": {}},
              "/campaigns/": {"success": True, "data": detail("x", types=("ugc-faceless",))}}
    c, op, _ = client_for(routes)
    f = DiscoveryFilters(shortlist_size=3)
    acc, stats = select_candidates(c, f, now=NOW, want=3)
    assert acc == [] and stats["shortlist"] == 3 and stats["detail_rejected"] == {"not_clipping": 3}
