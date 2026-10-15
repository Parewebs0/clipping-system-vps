"""MOLI-54 — branding API: upload font/media, templates, worker routes."""
import base64
import struct
import uuid

import pytest
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.models.campaign import Campaign
from app.services.rules.enforcement import assign_enforcement
from app.services.rules.schema import RuleSet

_MIN_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _minimal_ttf() -> bytes:
    return struct.pack(">4sHHHH", b"\x00\x01\x00\x00", 0, 16, 0, 0)


@pytest.fixture
def brand_dir(tmp_path, monkeypatch):
    path = tmp_path / "branding"
    monkeypatch.setattr(settings, "branding_dir", str(path))
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


def _camp(db, made):
    rs = RuleSet()
    rs.captions.brand_dictionary = ["BOXABL"]
    rs = assign_enforcement(rs)
    c = Campaign(
        name=f"branding-{uuid.uuid4().hex[:8]}",
        status="needs_review",
        source_provider="manual",
        source_metadata={"ruleset": rs.dump()},
    )
    db.add(c)
    db.commit()
    made.append(c.id)
    return c


def test_branding_requires_auth(client, auth_headers, write_token, brand_dir, campaigns):
    db, made = campaigns
    c = _camp(db, made)
    assert client.get(f"/campaigns/{c.id}/branding").status_code in {401, 403}
    # worker media route auth
    assert client.get(f"/worker/campaigns/{c.id}/branding/intro").status_code in {401, 403}
    r = client.get(f"/campaigns/{c.id}/branding", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["branding"] == {}
    assert r.json()["templates"] == []


def test_put_template_and_get_effective(client, auth_headers, write_token, brand_dir, campaigns):
    db, made = campaigns
    c = _camp(db, made)
    r = client.put(f"/campaigns/{c.id}/branding", json={
        "name": "pro", "font_name": "Brand", "caption_color": "&H00FFFFFF", "max_words": 5,
    }, headers=write_token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["templates"][0]["name"] == "pro"
    assert body["templates"][0]["max_words"] == 5
    # read endpoint accepts the plain token
    ok = client.get(f"/campaigns/{c.id}/branding", headers=auth_headers)
    assert ok.status_code == 200


def test_put_template_requires_write_token(client, auth_headers, write_token, brand_dir, campaigns):
    db, made = campaigns
    c = _camp(db, made)
    r = client.put(f"/campaigns/{c.id}/branding", json={"name": "pro"}, headers=auth_headers)
    assert r.status_code == 403


def test_font_upload_and_worker_route(client, auth_headers, write_token, brand_dir, campaigns):
    db, made = campaigns
    c = _camp(db, made)
    r = client.post(f"/campaigns/{c.id}/branding/font",
                    files={"file": ("brand.ttf", _minimal_ttf(), "font/ttf")},
                    headers=write_token)
    assert r.status_code == 200, r.text
    url = r.json()["font_url"]
    assert url == f"/worker/campaigns/{c.id}/fonts/font.ttf"
    # worker route with the read token
    got = client.get(url, headers=auth_headers)
    assert got.status_code == 200
    assert got.content == _minimal_ttf()
    # branding now exposes the font
    body = client.get(f"/campaigns/{c.id}/branding", headers=auth_headers).json()
    assert body["branding"]["font_url"] == url
    assert body["assets"]["font"] is True
    # rejected: not a real font
    bad = client.post(f"/campaigns/{c.id}/branding/font",
                      files={"file": ("fake.ttf", b"nope", "font/ttf")},
                      headers=write_token)
    assert bad.status_code == 400


def test_intro_outro_upload_and_worker_route(client, auth_headers, write_token, brand_dir, campaigns):
    db, made = campaigns
    c = _camp(db, made)
    for side in ("intro", "outro"):
        r = client.post(f"/campaigns/{c.id}/branding/{side}?max_seconds=2.5",
                        files={"file": ("card.png", _MIN_PNG, "image/png")},
                        headers=write_token)
        assert r.status_code == 200, r.text
        assert r.json()["url"] == f"/worker/campaigns/{c.id}/branding/{side}"
        assert r.json()["max_seconds"] == 2.5
        got = client.get(f"/worker/campaigns/{c.id}/branding/{side}", headers=auth_headers)
        assert got.status_code == 200
        assert got.content == _MIN_PNG
    body = client.get(f"/campaigns/{c.id}/branding", headers=auth_headers).json()
    assert body["branding"]["intro"]["max_seconds"] == 2.5
    assert body["assets"]["outro"] is True
    # unknown side → 404 at the worker route
    miss = client.get(f"/worker/campaigns/{c.id}/branding/intro2", headers=auth_headers)
    assert miss.status_code == 404


def test_worker_font_404_when_missing(client, auth_headers, brand_dir, campaigns):
    db, made = campaigns
    c = _camp(db, made)
    r = client.get(f"/worker/campaigns/{c.id}/fonts/font.ttf", headers=auth_headers)
    assert r.status_code == 404