"""Tests for the read-only Mission Control dashboard.

Verifies:
  * auth gate (401/403 without bearer)
  * feature flag gate (404 when MISSION_CONTROL_ENABLED=false)
  * shape of overview / campaigns / campaigns/{id} / jobs/recent / clips responses
  * LIMIT cap respected (requests for >500 do not crash)
  * NO write verb exposed by the router (no POST/PUT/PATCH/DELETE declared)
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.main import app


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "mission_control_enabled", True, raising=False)


def _disabled(monkeypatch):
    monkeypatch.setattr(settings, "mission_control_enabled", False, raising=False)


@pytest.fixture
def enabled_client(monkeypatch):
    _enabled(monkeypatch)
    # The router is mounted only at import time, but since `_enabled_or_404`
    # checks `settings.mission_control_enabled` at request time, the
    # feature flag is respected without re-importing.
    return TestClient(app)


@pytest.fixture
def disabled_client(monkeypatch):
    _disabled(monkeypatch)
    return TestClient(app)


def _create_campaign(name: str | None = None):
    s = SessionLocal()
    try:
        n = name or f"mc-test-{uuid.uuid4().hex[:8]}"
        s.execute(
            text(
                """
                INSERT INTO campaigns (name, status, source_provider, spec)
                VALUES (:n, 'discovered', 'manual', '{}'::jsonb)
                """
            ),
            {"n": n},
        )
        s.commit()
        return s.execute(text("SELECT id, name FROM campaigns WHERE name = :n"), {"n": n}).first()
    finally:
        s.close()


def _cleanup_campaign(cid: int):
    s = SessionLocal()
    try:
        # CASCADE removes assets; clips are also FK-cascaded.
        s.execute(text("DELETE FROM campaigns WHERE id = :id"), {"id": cid})
        s.commit()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

def test_overview_requires_auth(enabled_client):
    r = enabled_client.get("/mission-control/overview")
    assert r.status_code in (401, 403)


def test_overview_requires_auth_when_disabled(disabled_client):
    r = disabled_client.get("/mission-control/overview")
    # 404 (feature off) takes precedence; never reveals existence.
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def test_disabled_returns_404(disabled_client, auth_headers):
    r = disabled_client.get(
        "/mission-control/overview", headers=auth_headers
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Shape checks
# ---------------------------------------------------------------------------

def test_overview_shape(enabled_client, auth_headers):
    r = enabled_client.get(
        "/mission-control/overview", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "generated_at", "total_campaigns", "total_jobs_last_24h",
        "total_clips_last_24h", "disk_unavailable_videos",
        "campaigns_by_status", "assets_by_status",
        "clips_by_qa_status", "clips_by_status",
        "recent_errors",
    ):
        assert key in body, f"missing key: {key}"


def test_campaigns_list_includes_all_statuses(enabled_client, auth_headers):
    # Seed one campaign of each status allowed by ck_campaigns_status
    # (pipeline v2 values; legacy draft/ready/... were dropped in 0012).
    from app.models.campaign import CAMPAIGN_STATUS_VALUES
    statuses = list(CAMPAIGN_STATUS_VALUES)
    created_ids = []
    for st in statuses:
        n = f"mc-{st}-{uuid.uuid4().hex[:6]}"
        s = SessionLocal()
        try:
            s.execute(
                text(
                    "INSERT INTO campaigns (name, status, source_provider, spec) "
                    "VALUES (:n, :st, 'manual', '{}'::jsonb)"
                ),
                {"n": n, "st": st},
            )
            s.commit()
            row = s.execute(
                text("SELECT id FROM campaigns WHERE name=:n"), {"n": n}
            ).first()
            created_ids.append(row[0])
        finally:
            s.close()

    try:
        r = enabled_client.get(
            "/mission-control/campaigns", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        names_back = {it["name"] for it in body["items"]}
        for n in [f"mc-{st}-{uuid.uuid4().hex[:6]}" for st in statuses]:
            pass  # names are random — we just check we got something
        # Make sure at least the campaigns we just created are there
        # (we re-fetch their names from the DB to be precise):
        s = SessionLocal()
        try:
            names_seeded = [
                r[0] for r in s.execute(
                    text("SELECT name FROM campaigns WHERE id = ANY(:ids)"),
                    {"ids": created_ids},
                ).all()
            ]
        finally:
            s.close()
        for n in names_seeded:
            assert n in names_back, f"campaign {n} missing from response"
    finally:
        for cid in created_ids:
            _cleanup_campaign(cid)


def test_campaign_detail_404_for_missing(enabled_client, auth_headers):
    r = enabled_client.get(
        "/mission-control/campaigns/999999999", headers=auth_headers
    )
    assert r.status_code == 404


def test_campaign_detail_ok(enabled_client, auth_headers):
    row = _create_campaign()
    try:
        r = enabled_client.get(
            f"/mission-control/campaigns/{row[0]}", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["campaign"]["name"] == row[1]
        assert "assets" in body
        assert "active_jobs" in body
        assert "clips" in body
    finally:
        _cleanup_campaign(row[0])


# ---------------------------------------------------------------------------
# Limit cap
# ---------------------------------------------------------------------------

def test_jobs_recent_limit_capped(enabled_client, auth_headers):
    # Request way more than the cap; should not raise.
    r = enabled_client.get(
        "/mission-control/jobs/recent?limit=5000", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert len(body["items"]) <= 500


def test_campaign_rules_shape(enabled_client, auth_headers):
    """Endpoint nuevo /campaigns/{id}/rules: shape estable, sanea JSONB."""
    row = _create_campaign()
    try:
        r = enabled_client.get(
            f"/mission-control/campaigns/{row[0]}/rules", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Keys mínimas que el frontend necesita
        for key in (
            "campaign_id", "campaign_name", "status",
            "spec", "spec_is_empty", "rules", "card_text",
            "discovered", "asset_links_brief", "asset_links_raw",
            "asset_links_count", "drive_ids",
            "priority_tier", "priority_score", "priority_components",
            "briefed_at", "joined", "cpm_usd_per_1k", "prize_pool_usd",
        ):
            assert key in body, f"missing key in rules: {key}"
        assert body["campaign_id"] == row[0]
        assert body["spec_is_empty"] is True  # spec vacío recién creado
        assert isinstance(body["asset_links_raw"], list)
        assert isinstance(body["drive_ids"], list)
    finally:
        _cleanup_campaign(row[0])


def test_campaign_rules_404_for_missing(enabled_client, auth_headers):
    r = enabled_client.get(
        "/mission-control/campaigns/999999999/rules", headers=auth_headers
    )
    assert r.status_code == 404


def test_campaign_rules_requires_auth(enabled_client):
    r = enabled_client.get("/mission-control/campaigns/1/rules")
    assert r.status_code in (401, 403)


def test_campaign_rules_drives_extraction(enabled_client, auth_headers):
    """Si source_metadata tiene un link de Drive, debe aparecer en drive_ids."""
    row = _create_campaign()
    s = SessionLocal()
    try:
        s.execute(
            text("UPDATE campaigns SET source_metadata = :sm WHERE id = :id"),
            {
                "sm": '{"asset_links": ["https://drive.google.com/file/d/1ABCxyz-_/view"], "rules": {"platforms": ["tiktok"]}}',
                "id": row[0],
            },
        )
        s.commit()
    finally:
        s.close()
    try:
        r = enabled_client.get(
            f"/mission-control/campaigns/{row[0]}/rules", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["asset_links_count"] == 1
        assert "1ABCxyz-_" in body["drive_ids"]
        assert body["rules"]["platforms"] == ["tiktok"]
    finally:
        _cleanup_campaign(row[0])


# ---------------------------------------------------------------------------
# Stricter test: no POST/PUT/PATCH/DELETE declared
# ---------------------------------------------------------------------------

def test_no_write_verbs_in_router():
    """Static assertion that mission_control_router exposes only GET."""
    from app.api.mission_control import router as mc_router
    allowed = {"GET"}
    seen = set()
    for route in mc_router.routes:
        if hasattr(route, "methods"):
            for m in route.methods:
                seen.add(m)
                assert m in allowed, f"mission_control exposes {m} on {route.path}"
    # And we expect at least the documented endpoints.
    paths = {r.path for r in mc_router.routes if hasattr(r, "path")}
    expected = {
        "/mission-control/overview",
        "/mission-control/campaigns",
        "/mission-control/campaigns/{campaign_id}",
        "/mission-control/campaigns/{campaign_id}/rules",
        "/mission-control/jobs/recent",
        "/mission-control/pipeline/{campaign_id}",
        "/mission-control/videos",
        "/mission-control/clips",
    }
    missing = expected - paths
    assert not missing, f"missing endpoints: {missing}; saw: {paths}"


# ---------------------------------------------------------------------------
# Issue #7: contract aligned with what the pipeline v2 ticks really write
# ---------------------------------------------------------------------------

_REAL_SCORE_META = {
    "score": {
        "value": 62.5, "real_assets": 2, "cpm_usd": 1.5, "prize_pool_usd": 24000.0,
        "breakdown": {"value": 62.5, "base": 70.0, "penalties": {"captions": 7.5},
                       "penalty_total": 7.5, "min_to_run": 50.0, "eligible": True},
    },
    "score_preview": {"value": 55.0},
    "brief_docs": [{"url": "https://docs.google.com/document/d/x", "chars": 120}],
    "resolve_error": {"kind": "social_only", "message": "social_only: only social links"},
    "briefing_error": "old plain string",
    "discovered": {"external_id": 12345, "joined": 1, "cpm_usd_per_1k": "1.5"},
}


def _set_meta(cid: int, meta: dict) -> None:
    import json as _json
    s = SessionLocal()
    try:
        s.execute(
            text("UPDATE campaigns SET source_metadata = CAST(:sm AS jsonb) WHERE id = :id"),
            {"sm": _json.dumps(meta), "id": cid},
        )
        s.commit()
    finally:
        s.close()


def test_campaigns_list_reads_real_score_shape(enabled_client, auth_headers):
    row = _create_campaign()
    _set_meta(row[0], _REAL_SCORE_META)
    try:
        r = enabled_client.get("/mission-control/campaigns", headers=auth_headers)
        assert r.status_code == 200, r.text
        it = next(i for i in r.json()["items"] if i["id"] == row[0])
        assert it["priority_score"] == 62.5
        assert it["score_eligible"] is True
        assert it["score_min_to_run"] == 50.0
        assert it["priority_breakdown"]["penalties"] == {"captions": 7.5}
        assert it["resolve_error"] == {"kind": "social_only", "message": "social_only: only social links", "at": None}
        assert it["briefing_error"]["kind"] == "legacy_string"
    finally:
        _cleanup_campaign(row[0])


def test_campaign_rules_reads_real_score_and_brief_docs(enabled_client, auth_headers):
    row = _create_campaign()
    _set_meta(row[0], _REAL_SCORE_META)
    try:
        r = enabled_client.get(f"/mission-control/campaigns/{row[0]}/rules", headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["priority_score"] == 62.5
        assert body["priority_components"]["min_to_run"] == 50.0
        assert body["score_preview"] == {"value": 55.0}
        assert body["brief_docs"][0]["chars"] == 120
        assert body["discovered"]["external_id"] == "12345"
        assert body["discovered"]["cpm_usd_per_1k"] == 1.5
    finally:
        _cleanup_campaign(row[0])


def test_campaign_detail_exposes_llm_usage_and_clip_location(enabled_client, auth_headers):
    row = _create_campaign()
    s = SessionLocal()
    try:
        s.execute(
            text(
                "INSERT INTO llm_usage (provider, model, stage, campaign_id, total_tokens, cost_usd, ok) "
                "VALUES ('xai','m','brief_reader',:cid,1000,0.25,true),"
                "       ('xai','m','clip_decider',:cid,500,0.10,false)"
            ),
            {"cid": row[0]},
        )
        aid = s.execute(
            text(
                "INSERT INTO assets (id, campaign_id, source_url, asset_type, status, extra_metadata) "
                "VALUES (gen_random_uuid(), :cid, 'https://example.com/v.mp4', 'video', 'transcribed', "
                "'{\"last_error_kind\": \"x\"}'::jsonb) RETURNING id"
            ),
            {"cid": row[0]},
        ).scalar_one()
        s.execute(
            text(
                "INSERT INTO clips (id, campaign_id, asset_id, qa_status, status, location, final_path_worker) "
                "VALUES (gen_random_uuid(), :cid, :aid, 'pass', 'approved', 'pending_upload', 'C:/clips/pending_upload/a.mp4')"
            ),
            {"cid": row[0], "aid": aid},
        )
        s.commit()
    finally:
        s.close()
    try:
        r = enabled_client.get(f"/mission-control/campaigns/{row[0]}", headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["llm_usage"]["calls"] == 2
        assert body["llm_usage"]["errors"] == 1
        assert abs(body["llm_usage"]["cost_usd"] - 0.35) < 1e-6
        assert {b["stage"] for b in body["llm_usage"]["by_stage"]} == {"brief_reader", "clip_decider"}
        assert body["clips"][0]["location"] == "pending_upload"
        assert body["clips"][0]["final_path_worker"].endswith("a.mp4")
        assert body["assets"][0]["extra_metadata"] == {"last_error_kind": "x"}

        inv = enabled_client.get(
            f"/mission-control/clips?campaign_id={row[0]}", headers=auth_headers
        ).json()
        assert inv["items"][0]["location"] == "pending_upload"

        ov = enabled_client.get("/mission-control/overview", headers=auth_headers).json()
        assert ov["llm_usage_total"]["calls"] >= 2
        assert "llm_usage_24h" in ov
    finally:
        s = SessionLocal()
        try:
            s.execute(text("DELETE FROM llm_usage WHERE campaign_id = :cid"), {"cid": row[0]})
            s.commit()
        finally:
            s.close()
        _cleanup_campaign(row[0])
