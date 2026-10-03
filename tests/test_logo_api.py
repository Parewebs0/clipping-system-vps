"""#65 — logo upload auth, a single confirmation, and no file left on 409."""
import base64
import uuid

import pytest
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.models.campaign import Campaign
from app.services.rules.enforcement import assign_enforcement
from app.services.rules.gate import GateError
from app.services.rules.schema import RuleSet

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def logo_dir(tmp_path, monkeypatch):
    path = tmp_path / "logos"
    monkeypatch.setattr(settings, "logo_dir", str(path))
    return path


@pytest.fixture
def write_token(monkeypatch):
    monkeypatch.setattr(settings, "api_write_token", "write-secret-test", raising=False)
    return {"Authorization": "Bearer write-secret-test"}


@pytest.fixture
def campaigns():
    db = SessionLocal()
    made = []
    yield db, made
    db.rollback()
    if made:
        db.execute(text("DELETE FROM campaigns WHERE id = ANY(:ids)"), {"ids": made})
        db.commit()
    db.close()


def _camp(db, made, *, logo=True, pre=True):
    rs = RuleSet()
    rs.logo.required = logo
    rs.pre_approval.required = pre
    rs = assign_enforcement(rs)
    c = Campaign(
        name=f"logo-{uuid.uuid4().hex[:8]}",
        status="needs_review",
        source_provider="manual",
        source_metadata={"ruleset": rs.dump()},
    )
    db.add(c)
    db.commit()
    made.append(c.id)
    return c


def _upload(client, campaign_id, headers):
    return client.post(
        f"/campaigns/{campaign_id}/rules/logo",
        files={"file": ("logo.png", _PNG, "image/png")},
        headers=headers,
    )


def test_logo_upload_requires_the_write_token(client, auth_headers, write_token, logo_dir, campaigns):
    db, made = campaigns
    c = _camp(db, made)
    missing = client.post(
        f"/campaigns/{c.id}/rules/logo",
        files={"file": ("logo.png", _PNG, "image/png")},
    )
    assert missing.status_code in {401, 403}
    assert _upload(client, c.id, auth_headers).status_code == 403
    assert _upload(client, c.id, {"Authorization": "Bearer nope"}).status_code == 401
    assert list(logo_dir.glob("*.png")) == []


def test_upload_confirms_only_the_logo_blocker(client, auth_headers, write_token, logo_dir, campaigns):
    db, made = campaigns
    c = _camp(db, made)
    r = _upload(client, c.id, write_token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["logo_url"] == f"/worker/campaigns/{c.id}/logo"
    by_key = {b["key"]: b for b in body["blockers"]}
    assert by_key["human:logo_file"]["confirmed"] is True
    assert by_key["human:pre_approval"]["confirmed"] is False
    assert (logo_dir / f"{c.id}.png").read_bytes() == _PNG

    denied = client.get(f"/worker/campaigns/{c.id}/logo")
    assert denied.status_code in {401, 403}
    assert client.get(f"/worker/campaigns/{c.id}/logo", headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.get(f"/worker/campaigns/{c.id}/logo", headers=auth_headers)
    assert ok.status_code == 200
    assert ok.content == _PNG
    assert ok.headers["content-type"].startswith("image/png")


def test_logo_get_is_404_when_missing(client, auth_headers, logo_dir, campaigns):
    db, made = campaigns
    c = _camp(db, made, logo=False, pre=False)
    r = client.get(f"/worker/campaigns/{c.id}/logo", headers=auth_headers)
    assert r.status_code == 404


def test_confirm_conflict_does_not_leave_a_png(client, write_token, logo_dir, campaigns, monkeypatch):
    db, made = campaigns
    c = _camp(db, made)

    def _boom(*_a, **_k):
        raise GateError("unknown blocker key(s): human:logo_file")

    monkeypatch.setattr("app.services.rules.gate.confirm", _boom)
    r = _upload(client, c.id, write_token)
    assert r.status_code == 409
    assert list(logo_dir.glob("*.png")) == []
    assert list(logo_dir.glob("*.tmp")) == []
    db.expire_all()
    fresh = db.get(Campaign, c.id)
    overrides = (fresh.source_metadata or {}).get("rules_overrides") or {}
    assert "logo_url" not in overrides
    assert "human:logo_file" not in ((fresh.source_metadata or {}).get("rules_confirmations") or {})
