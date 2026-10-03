"""Bearer token authentication tests."""
def test_system_info_without_token(client):
    r = client.get("/system/info")
    assert r.status_code in {401, 403}


def test_system_info_with_wrong_token(client):
    r = client.get(
        "/system/info",
        headers={"Authorization": "***"},
    )
    assert r.status_code == 401


def test_system_info_with_correct_token(client, auth_headers):
    r = client.get("/system/info", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "clipping-api"
    assert body["environment"] in {"production", "development", "staging"}


# --- API_WRITE_TOKEN (issue #11) ---------------------------------------------
import uuid

import pytest

from app.config import settings


@pytest.fixture
def write_token(monkeypatch):
    monkeypatch.setattr(settings, "api_write_token", "write-secret-test", raising=False)
    return {"Authorization": "Bearer write-secret-test"}


def test_without_write_token_api_token_can_write(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "api_write_token", "", raising=False)
    r = client.post("/campaigns", json={"name": f"wt-{uuid.uuid4().hex[:6]}"}, headers=auth_headers)
    assert r.status_code == 201


def test_write_token_required_for_campaign_writes(client, auth_headers, write_token):
    name = f"wt-{uuid.uuid4().hex[:6]}"
    r = client.post("/campaigns", json={"name": name}, headers=auth_headers)
    assert r.status_code == 403 and "API_WRITE_TOKEN" in r.json()["detail"]
    r = client.post("/campaigns", json={"name": name}, headers=write_token)
    assert r.status_code == 201
    cid = r.json()["id"]
    assert client.patch(f"/campaigns/{cid}", json={"name": name + "x"}, headers=auth_headers).status_code == 403
    assert client.post(f"/campaigns/{cid}/status", json={"status": "archived"}, headers=auth_headers).status_code == 403
    assert client.delete(f"/campaigns/{cid}", headers=auth_headers).status_code == 403
    assert client.patch(f"/campaigns/{cid}", json={"name": name + "x"}, headers=write_token).status_code == 200
    assert client.post(f"/campaigns/{cid}/status", json={"status": "archived"}, headers=write_token).status_code == 200
    assert client.delete(f"/campaigns/{cid}", headers=write_token).status_code == 204
    bad = {"Authorization": "Bearer nope"}
    assert client.patch(f"/campaigns/{cid}", json={"name": "y"}, headers=bad).status_code == 401


def test_reads_accept_both_tokens(client, auth_headers, write_token):
    assert client.get("/campaigns", headers=auth_headers).status_code == 200
    assert client.get("/campaigns", headers=write_token).status_code == 200
    assert client.get("/campaigns/status-machine", headers=write_token).status_code == 200


def test_worker_endpoints_keep_api_token(client, auth_headers, write_token):
    # the worker's token still works on non-campaign endpoints
    assert client.get("/system/info", headers=auth_headers).status_code == 200
