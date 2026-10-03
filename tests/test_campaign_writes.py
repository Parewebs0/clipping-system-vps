"""Write endpoints for campaigns + manual state machine (issue #9)."""
import uuid

import pytest
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.campaign import CAMPAIGN_STATUS_VALUES
from app.services.campaign_transitions import MANUAL_TRANSITIONS

FORWARD = {  # transitions only the pipeline ticks may do
    ("discovered", "briefed"), ("discovered", "scored"), ("briefed", "assets_resolved"),
    ("briefed", "scored"), ("assets_resolved", "scored"), ("failed_brief", "briefed"),
    ("failed_resolve", "assets_resolved"), ("blocked_no_assets", "scored"),
    ("archived", "scored"), ("archived", "briefed"),
}


def _new(client, auth_headers, **kw):
    body = {"name": f"w-{uuid.uuid4().hex[:8]}", "source_provider": "manual", **kw}
    r = client.post("/campaigns", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()


def _force_status(cid: int, status: str) -> None:
    s = SessionLocal()
    try:
        s.execute(text("UPDATE campaigns SET status=:s WHERE id=:id"), {"s": status, "id": cid})
        s.commit()
    finally:
        s.close()


def test_state_machine_covers_every_status_and_never_goes_forward():
    assert set(MANUAL_TRANSITIONS) == set(CAMPAIGN_STATUS_VALUES)
    for src, targets in MANUAL_TRANSITIONS.items():
        for t in targets:
            assert t in CAMPAIGN_STATUS_VALUES
            assert (src, t) not in FORWARD, f"{src}->{t} would skip a pipeline step"
    # every status can be archived, archived can only go back to the entry state
    for st in CAMPAIGN_STATUS_VALUES:
        if st != "archived":
            assert "archived" in MANUAL_TRANSITIONS[st]
    assert MANUAL_TRANSITIONS["archived"] == ("discovered",)


def test_archived_not_consumed_by_ticks():
    """No tick selects 'archived' and discovery doesn't count it as active."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1] / "scripts"
    for f in root.glob("*.py"):
        assert "archived" not in re.sub(r"#.*", "", f.read_text()), f"{f.name} references archived"
    import ast
    tree = ast.parse((root / "whop_discovery.py").read_text())
    active = next(
        ast.literal_eval(n.value) for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ACTIVE_STATUSES"
    )
    assert "discovered" in active and "archived" not in active


def test_status_machine_endpoint(client, auth_headers):
    r = client.get("/campaigns/status-machine", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transitions"]["scored"] == ["assets_resolved", "blocked_no_assets", "archived"]
    by_val = {s["value"]: s for s in body["statuses"]}
    assert by_val["discovered"]["consumed_by"].startswith("brief_reader_tick")
    assert by_val["archived"]["effect"]
    assert client.get("/campaigns/status-machine").status_code in (401, 403)


@pytest.mark.parametrize("src,dst", sorted({(s, t) for s, ts in MANUAL_TRANSITIONS.items() for t in ts}))
def test_every_allowed_transition_works(client, auth_headers, src, dst):
    c = _new(client, auth_headers)
    _force_status(c["id"], src)
    r = client.post(f"/campaigns/{c['id']}/status", json={"status": dst, "reason": "test"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == dst
    h = body["source_metadata"]["status_history"][-1]
    assert (h["from"], h["to"], h["reason"], h["by"]) == (src, dst, "test", "api:status")


@pytest.mark.parametrize("src,dst", sorted(FORWARD))
def test_forward_transitions_rejected(client, auth_headers, src, dst):
    c = _new(client, auth_headers)
    _force_status(c["id"], src)
    r = client.post(f"/campaigns/{c['id']}/status", json={"status": dst}, headers=auth_headers)
    assert r.status_code == 409, r.text
    assert "not allowed" in r.json()["detail"]
    assert client.get(f"/campaigns/{c['id']}", headers=auth_headers).json()["status"] == src


def test_status_unknown_and_noop_and_404(client, auth_headers):
    c = _new(client, auth_headers)
    assert client.post(f"/campaigns/{c['id']}/status", json={"status": "draft"}, headers=auth_headers).status_code == 400
    r = client.post(f"/campaigns/{c['id']}/status", json={"status": "discovered"}, headers=auth_headers)
    assert r.status_code == 200 and "status_history" not in r.json()["source_metadata"]
    assert client.post("/campaigns/999999/status", json={"status": "archived"}, headers=auth_headers).status_code == 404
    assert client.post(f"/campaigns/{c['id']}/status", json={"status": "archived", "x": 1}, headers=auth_headers).status_code == 422


def test_patch_full_edit_manual(client, auth_headers):
    c = _new(client, auth_headers, spec={"duration_min": 10, "duration_max": 40, "extra": {"score": 77}})
    r = client.patch(
        f"/campaigns/{c['id']}",
        json={
            "name": c["name"] + "-ed",
            "source_url": "https://example.com/brief",
            "source_id": "ext-1",
            "source_instructions": "nuevas",
            "spec": {"duration_max": 60, "format": "9:16", "keywords": ["a", "b"], "extra": {"note": "x"}},
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["name"].endswith("-ed")
    assert b["source_url"] == "https://example.com/brief"
    assert b["source_id"] == "ext-1"
    assert b["spec"]["duration_min"] == 10  # untouched
    assert b["spec"]["duration_max"] == 60
    assert b["spec"]["keywords"] == ["a", "b"]
    assert b["spec"]["extra"] == {"score": 77, "note": "x"}  # scorer data preserved
    # explicit null clears
    r = client.patch(f"/campaigns/{c['id']}", json={"source_url": None, "source_instructions": None}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["source_url"] is None and r.json()["source_instructions"] is None


def test_patch_validation(client, auth_headers):
    c = _new(client, auth_headers)
    url = f"/campaigns/{c['id']}"
    assert client.patch(url, json={"spec": {"duration_min": 50, "duration_max": 10}}, headers=auth_headers).status_code == 422
    assert client.patch(url, json={"source_url": "ftp://x"}, headers=auth_headers).status_code == 422
    assert client.patch(url, json={"source_provider": "whop"}, headers=auth_headers).status_code == 422
    assert client.patch(url, json={"name": "   "}, headers=auth_headers).status_code == 422
    assert client.patch(url, json={"spec": {"bogus": 1}}, headers=auth_headers).status_code == 422
    # existing duration_min=50 vs new max 10 -> 400 (merge check)
    assert client.patch(url, json={"spec": {"duration_min": 50}}, headers=auth_headers).status_code == 200
    assert client.patch(url, json={"spec": {"duration_max": 10}}, headers=auth_headers).status_code == 400


def test_patch_duplicate_name_is_409(client, auth_headers):
    a = _new(client, auth_headers)
    b = _new(client, auth_headers)
    r = client.patch(f"/campaigns/{b['id']}", json={"name": a["name"]}, headers=auth_headers)
    assert r.status_code == 409, r.text


def test_patch_whop_source_url_locked(client, auth_headers):
    c = _new(client, auth_headers, source_provider="whop", source_url="https://whop.com/x")
    r = client.patch(f"/campaigns/{c['id']}", json={"source_url": "https://whop.com/y"}, headers=auth_headers)
    assert r.status_code == 409
    # same value / other fields are fine
    r = client.patch(f"/campaigns/{c['id']}", json={"source_url": "https://whop.com/x", "name": c["name"] + "2"}, headers=auth_headers)
    assert r.status_code == 200, r.text


def test_delete_only_archived(client, auth_headers):
    c = _new(client, auth_headers)
    url = f"/campaigns/{c['id']}"
    assert client.delete(url, headers=auth_headers).status_code == 409
    assert client.post(f"{url}/status", json={"status": "archived"}, headers=auth_headers).status_code == 200
    s = SessionLocal()
    try:
        s.execute(
            text("INSERT INTO jobs (id, job_type, status, payload) VALUES (gen_random_uuid(), 'download', 'pending', CAST(:p AS jsonb))"),
            {"p": '{"campaign_id": %d}' % c["id"]},
        )
        s.commit()
    finally:
        s.close()
    r = client.delete(url, headers=auth_headers)
    assert r.status_code == 409 and "active job" in r.json()["detail"]
    s = SessionLocal()
    try:
        s.execute(text("DELETE FROM jobs"))
        s.commit()
    finally:
        s.close()
    assert client.delete(url, headers=auth_headers).status_code == 204
    assert client.get(url, headers=auth_headers).status_code == 404
    assert client.delete(url, headers=auth_headers).status_code == 404


def test_write_endpoints_require_auth(client):
    assert client.patch("/campaigns/1", json={"name": "x"}).status_code in (401, 403)
    assert client.post("/campaigns/1/status", json={"status": "archived"}).status_code in (401, 403)
    assert client.delete("/campaigns/1").status_code in (401, 403)
