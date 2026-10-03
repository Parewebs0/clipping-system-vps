"""Candidate review from Mission Control (issue #17).

* GET /mission-control/candidates: shape, filters, transcript excerpt,
  counts, render-job linkage.
* approve/reject state guards (409) and write-token auth.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.main import app


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "mission_control_enabled", True, raising=False)
    return TestClient(app)


@pytest.fixture
def h() -> Dict[str, str]:
    return {"Authorization": f"Bearer {settings.api_token}"}


@pytest.fixture(autouse=True)
def clean_db():
    s = SessionLocal()
    try:
        for t in ("clips", "jobs", "candidates", "assets", "campaigns"):
            s.execute(text(f"DELETE FROM {t}"))
        s.commit()
        yield
    finally:
        s.close()


SEGMENTS = [
    {"start": 0.0, "end": 9.0, "text": "intro before the clip"},
    {"start": 9.0, "end": 20.0, "text": "first line inside"},
    {"start": 20.0, "end": 41.0, "text": "second line inside"},
    {"start": 45.0, "end": 50.0, "text": "after the clip"},
]


def _seed(client, h) -> Dict[str, Any]:
    r = client.post("/campaigns", json={
        "name": f"TEST review {uuid.uuid4().hex[:6]}",
        "source_provider": "manual",
        "source_instructions": "Make 20-60s clips in english.",
    }, headers=h)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.post("/assets", json={
        "campaign_id": cid,
        "source_url": f"https://www.youtube.com/watch?v={uuid.uuid4().hex[:11]}",
        "source_provider": "manual",
        "duration_seconds": 120.0,
        "extra_metadata": {
            "title": "Source video",
            "transcription": {"text": " ".join(s["text"] for s in SEGMENTS),
                              "segments": SEGMENTS},
        },
    }, headers=h)
    assert r.status_code == 201, r.text
    return {"campaign_id": cid, "asset_id": r.json()["id"]}


def _cand(client, h, seed, start=10.0, end=40.0, score=0.8, meta=None):
    r = client.post("/candidates", json={
        "campaign_id": seed["campaign_id"], "asset_id": seed["asset_id"],
        "start_time": start, "end_time": end, "score": score,
        "reasoning": "fixture",
        "extra_metadata": meta or {"title": "Hook", "caption": "Line 1\nLine 2",
                                   "source": "grok_clip_decider", "kind": "speech"},
    }, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


# --- read endpoint ---------------------------------------------------------

def test_list_candidates_shape_and_excerpt(client, h):
    seed = _seed(client, h)
    c = _cand(client, h, seed)
    r = client.get(f"/mission-control/candidates?campaign_id={seed['campaign_id']}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["counts_by_status"] == {"pending": 1}
    it = body["items"][0]
    assert it["id"] == c["id"]
    assert it["title"] == "Hook" and it["caption"] == "Line 1\nLine 2"
    assert it["kind"] == "speech" and it["source"] == "grok_clip_decider"
    assert it["duration_seconds"] == 30.0
    assert it["asset_title"] == "Source video"
    assert it["asset_source_url"].startswith("https://www.youtube.com/")
    assert [l["text"] for l in it["transcript_excerpt"]] == [
        "first line inside", "second line inside",
    ]
    assert it["render_job_id"] is None and it["clip_id"] is None


def test_list_candidates_filters_and_order(client, h):
    seed = _seed(client, h)
    other = _seed(client, h)
    low = _cand(client, h, seed, score=0.2)
    high = _cand(client, h, seed, score=0.9)
    rej = _cand(client, h, seed, score=0.99)
    _cand(client, h, other)
    assert client.post(f"/candidates/{rej['id']}/reject", json={"reason": "x"}, headers=h).status_code == 200

    r = client.get(f"/mission-control/candidates?campaign_id={seed['campaign_id']}", headers=h)
    ids = [i["id"] for i in r.json()["items"]]
    # pending first (by score), rejected last
    assert ids == [high["id"], low["id"], rej["id"]]
    assert r.json()["counts_by_status"] == {"pending": 2, "rejected": 1}

    r = client.get(
        f"/mission-control/candidates?campaign_id={seed['campaign_id']}&status=rejected", headers=h
    )
    items = r.json()["items"]
    assert [i["id"] for i in items] == [rej["id"]]
    assert items[0]["rejected_reason"] == "x"
    # counts ignore the status filter (tab badges)
    assert r.json()["counts_by_status"]["pending"] == 2

    r = client.get("/mission-control/candidates", headers=h)
    assert r.json()["count"] == 4


def test_list_candidates_shows_render_job_after_approve(client, h):
    seed = _seed(client, h)
    c = _cand(client, h, seed)
    r = client.post(f"/candidates/{c['id']}/approve", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    job_id = r.json()["render_job_id"]
    it = client.get("/mission-control/candidates", headers=h).json()["items"][0]
    assert it["status"] == "approved"
    assert it["render_job_id"] == job_id
    assert it["render_job_status"] == "pending"
    assert it["approved_at"]


def test_list_candidates_requires_auth_and_flag(client, monkeypatch):
    assert client.get("/mission-control/candidates").status_code in (401, 403)
    monkeypatch.setattr(settings, "mission_control_enabled", False, raising=False)
    r = client.get("/mission-control/candidates",
                   headers={"Authorization": f"Bearer {settings.api_token}"})
    assert r.status_code == 404


# --- state guards ----------------------------------------------------------

def test_cannot_approve_rejected_candidate(client, h):
    seed = _seed(client, h)
    c = _cand(client, h, seed)
    client.post(f"/candidates/{c['id']}/reject", headers=h)
    r = client.post(f"/candidates/{c['id']}/approve", headers=h)
    assert r.status_code == 409
    s = SessionLocal()
    try:
        n = s.execute(text("SELECT count(*) FROM jobs WHERE job_type='render'")).scalar()
    finally:
        s.close()
    assert n == 0


def test_cannot_reject_after_render_job(client, h):
    seed = _seed(client, h)
    c = _cand(client, h, seed)
    assert client.post(f"/candidates/{c['id']}/approve", headers=h).json()["status"] == "approved"
    r = client.post(f"/candidates/{c['id']}/reject", headers=h)
    assert r.status_code == 409


def test_reject_rendered_is_409_not_500(client, h):
    seed = _seed(client, h)
    c = _cand(client, h, seed)
    s = SessionLocal()
    try:
        s.execute(text("UPDATE candidates SET status='rendered' WHERE id=:i"), {"i": c["id"]})
        s.commit()
    finally:
        s.close()
    assert client.post(f"/candidates/{c['id']}/reject", headers=h).status_code == 409


def test_reject_is_idempotent_keeps_reason(client, h):
    seed = _seed(client, h)
    c = _cand(client, h, seed)
    client.post(f"/candidates/{c['id']}/reject", json={"reason": "first"}, headers=h)
    r = client.post(f"/candidates/{c['id']}/reject", json={"reason": "second"}, headers=h)
    assert r.status_code == 200
    assert r.json()["extra_metadata"]["rejected_reason"] == "first"


def test_approve_out_of_window_returns_rejected(client, h):
    seed = _seed(client, h)
    c = _cand(client, h, seed, start=0.0, end=5.0)
    r = client.post(f"/candidates/{c['id']}/approve", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "rejected" and r.json()["reason"]


# --- write token -----------------------------------------------------------

def test_approve_reject_require_write_token_when_set(client, h, monkeypatch):
    seed = _seed(client, h)
    c = _cand(client, h, seed)
    monkeypatch.setattr(settings, "api_write_token", "write-secret", raising=False)
    w = {"Authorization": "Bearer write-secret"}
    assert client.post(f"/candidates/{c['id']}/approve", headers=h).status_code == 403
    assert client.post(f"/candidates/{c['id']}/reject", headers=h).status_code == 403
    # reads still accept both tokens
    assert client.get("/mission-control/candidates", headers=h).status_code == 200
    assert client.get("/mission-control/candidates", headers=w).status_code == 200
    r = client.post(f"/candidates/{c['id']}/reject", json={"reason": "ui"}, headers=w)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
