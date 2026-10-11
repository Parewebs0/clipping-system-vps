"""Read-only Mission Control endpoints over a fixture JSONL. No database."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.shadow.reports import infer_graph_path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SHADOW_REPORTS_DIR", str(FIXTURE_DIR))
    monkeypatch.setattr(settings, "mission_control_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "shadow-test-token", raising=False)
    monkeypatch.setattr(settings, "api_write_token", "", raising=False)
    return TestClient(app)


@pytest.fixture
def auth():
    return {"Authorization": "Bearer shadow-test-token"}


def test_list_and_detail_from_fixture(client, auth):
    r = client.get("/mission-control/shadow/runs", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 1
    ids = {it["id"] for it in body["items"]}
    assert "run" in ids
    run = next(it for it in body["items"] if it["id"] == "run")
    assert run["n_assets"] == 2
    assert run["match_pct"] == 75.0
    assert run["cost_usd_total"] == 0.03
    assert run["latency_ms_total"] == 49
    assert run["n_would_request_approval"] == 1

    d = client.get("/mission-control/shadow/runs/run", headers=auth)
    assert d.status_code == 200, d.text
    detail = d.json()
    assert len(detail["assets"]) == 2
    banned = next(a for a in detail["assets"] if a["label"] == "casita-banned-term")
    assert banned["current"]["n_clips"] == 2
    assert banned["graph"]["n_clips"] == 1
    assert banned["diff"]["iou"] == 0.5
    assert banned["current"]["captions"] == ["old", "house"]
    assert banned["graph"]["captions"] == ["house"]
    assert banned["graph"]["asked_more_context"] is True
    assert "need_more_context" in banned["graph"]["graph_path"]
    assert banned["graph"]["would_request_approval"] is True
    assert "habría pedido aprobación" in (banned["graph"]["approval_reason"] or "")
    assert banned["graph"]["stake_usd"] == 80.0


def test_missing_run_404(client, auth):
    r = client.get("/mission-control/shadow/runs/does-not-exist", headers=auth)
    assert r.status_code == 404


def test_path_traversal_rejected(client, auth):
    r = client.get("/mission-control/shadow/runs/..%2frun", headers=auth)
    assert r.status_code in (404, 422)


def test_requires_auth(client):
    r = client.get("/mission-control/shadow/runs")
    assert r.status_code in (401, 403)


def test_disabled_is_404(monkeypatch, auth):
    monkeypatch.setenv("SHADOW_REPORTS_DIR", str(FIXTURE_DIR))
    monkeypatch.setattr(settings, "mission_control_enabled", False, raising=False)
    monkeypatch.setattr(settings, "api_token", "shadow-test-token", raising=False)
    r = TestClient(app).get("/mission-control/shadow/runs", headers=auth)
    assert r.status_code == 404


def test_no_write_on_shadow_routes():
    from app.api.mission_control import router as mc_router

    for route in mc_router.routes:
        if getattr(route, "path", "").startswith("/mission-control/shadow"):
            assert getattr(route, "methods", set()) <= {"GET"}


def test_infer_graph_path_inserts_need_more_context():
    path = infer_graph_path({"graph": {"context_iteration": 1}})
    assert path.count("score_clips") == 2
    assert path.index("need_more_context") < path.index("decide")
    assert infer_graph_path({"graph": {"context_iteration": 0}})[-1] == "report"
