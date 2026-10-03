"""#37 — rules gate: needs_review, confirmations, approval / download blocked."""
import uuid

import pytest
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.campaign import Campaign
from app.models.job import Job
from app.services.rules.enforcement import assign_enforcement
from app.services.rules.gate import GateError, apply_gate, confirm, gate_campaigns, is_blocked, pending
from app.services.rules.schema import AccountRequirement, Evidence, RuleSet, UnsupportedRule


def _rs(account=True, unsupported=False, pre=False):
    rs = RuleSet()
    if account:
        rs.account_requirements.append(AccountRequirement(required=True, kind="bio", text="Put the game link in your bio",
                                                          evidence=[Evidence(quote="link in bio", source="guidelines", verified=True)]))
    if pre:
        rs.pre_approval.required = True
    if unsupported:
        rs.unsupported.append(UnsupportedRule(text="Assets only on frame.io", reason="host not supported",
                                              evidence=[Evidence(quote="frame.io", source="guidelines", verified=True)]))
    return assign_enforcement(rs)


@pytest.fixture
def s():
    s = SessionLocal()
    made = []
    yield s, made
    s.rollback()
    s.execute(text("DELETE FROM jobs WHERE job_type='download'"))
    s.execute(text("DELETE FROM campaigns WHERE id = ANY(:ids)"), {"ids": made})
    s.commit()
    s.close()


def _camp(s, made, status="scored", rs=None):
    meta = {"ruleset": rs.dump()} if rs is not None else {}
    c = Campaign(name=f"gate-{uuid.uuid4().hex[:8]}", status=status, source_provider="whop",
                 source_url=f"https://whop.com/c/{uuid.uuid4().hex}", source_metadata=meta)
    s.add(c)
    s.commit()
    made.append(c.id)
    return c


def test_no_ruleset_or_no_blockers_is_workable(s):
    db, made = s
    a = _camp(db, made)
    b = _camp(db, made, rs=_rs(account=False))
    assert not is_blocked(a) and not is_blocked(b)
    assert gate_campaigns(db) == 0
    db.refresh(a), db.refresh(b)
    assert a.status == b.status == "scored"


def test_gate_moves_to_needs_review_and_cancels_downloads(s):
    db, made = s
    c = _camp(db, made, rs=_rs(pre=True))
    j = Job(job_type="download", status="pending", payload={"campaign_id": str(c.id), "asset_id": str(uuid.uuid4())})
    db.add(j)
    db.commit()
    from app.services.download_gate import cancel_unworkable

    # same order as scripts/download_enqueue_tick.py
    assert gate_campaigns(db) == 1
    assert cancel_unworkable(db) == 1
    db.expire_all()
    c = db.get(Campaign, c.id)
    j = db.get(Job, j.id)
    assert c.status == "needs_review"
    h = c.source_metadata["status_history"][-1]
    assert h["to"] == "needs_review" and "requisito" in h["reason"]
    assert c.source_metadata["rules_gate"]["previous_status"] == "scored"
    assert j.status == "cancelled"


def test_confirm_releases_back_to_previous(s):
    db, made = s
    c = _camp(db, made, rs=_rs(pre=True))
    assert apply_gate(c)
    keys = [b["key"] for b in pending(c)]
    assert len(keys) == 2
    r = confirm(c, keys[:1], "ok")
    assert not r["released"] and c.status == "needs_review"
    r = confirm(c, keys[1:], "done")
    assert r["released"] and c.status == "scored" and r["pending"] == []
    assert not apply_gate(c)


def test_unsupported_cannot_be_confirmed(s):
    db, made = s
    c = _camp(db, made, rs=_rs(account=False, unsupported=True))
    assert apply_gate(c)
    key = pending(c)[0]["key"]
    with pytest.raises(GateError):
        confirm(c, [key], "x")
    with pytest.raises(GateError):
        confirm(c, ["nope"], "x")


def test_api_ruleset_and_confirm(s, client, auth_headers):
    db, made = s
    c = _camp(db, made, rs=_rs())
    apply_gate(c)
    db.commit()
    r = client.get(f"/campaigns/{c.id}/ruleset", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "needs_review" and body["pending_count"] == 1
    assert body["ruleset"]["version"] >= 2
    key = body["blockers"][0]["key"]
    assert client.post(f"/campaigns/{c.id}/rules/confirm", json={"keys": [key]}).status_code in (401, 403)
    assert client.post(f"/campaigns/{c.id}/rules/confirm", json={"keys": ["bad"]}, headers=auth_headers).status_code == 409
    r = client.post(f"/campaigns/{c.id}/rules/confirm", json={"keys": [key], "note": "bio ok"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "scored" and body["pending_count"] == 0
    assert body["blockers"][0]["confirmed"] and body["blockers"][0]["confirmation"]["note"] == "bio ok"


def test_enqueue_endpoint_refuses_pending_rules(s, client, auth_headers):
    db, made = s
    c = _camp(db, made, rs=_rs())
    r = client.post(f"/campaigns/{c.id}/enqueue", headers=auth_headers)
    assert r.status_code == 409 and "rules" in r.json()["detail"]
