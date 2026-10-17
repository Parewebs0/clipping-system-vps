"""Whop organization logo downloaded on campaign upsert. No network."""
from __future__ import annotations

import base64
import logging
from types import SimpleNamespace

import httpx
import pytest

pytestmark = pytest.mark.no_db

from app.config import settings
from app.services.discovery.models import DiscoveredCampaign
from app.services.discovery.upsert import upsert_campaign
from app.services.logo_autofetch import apply_org_logo
from app.services.logo_store import MAX_LOGO_BYTES, PNG_MAGIC, logo_file_path, store_logo_png
from app.services.rules.enforcement import assign_enforcement
from app.services.rules.render_spec import build_render_spec
from app.services.rules.schema import RuleSet
from app.services.rules.verifier import verify

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_OTHER = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>'
_REL = "organizations/11111111-1111-1111-1111-111111111111/logo/22222222-2222-2222-2222-222222222222.abc123.png"
_ABS = "https://cdn.example.test/brand/logo.png"
_DETAIL = "https://whop.com/experiences/exp/campaigns/camp"
_BASE = "https://assets.example.test/bucket/"


class _Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self, chunk_size=65536):
        body = self._body
        step = chunk_size or len(body) or 1
        for i in range(0, len(body), step):
            yield body[i:i + step]


class _HTTP:
    def __init__(self):
        self.queue = []
        self.calls = []

    def install(self, monkeypatch):
        client = self

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream(self, method, url):
                client.calls.append((method, url))
                item = client.queue.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item

        monkeypatch.setattr("app.services.logo_autofetch.httpx.Client", _Client)
        return self

    def png(self, body=_PNG, url_name="logo.png"):
        self.queue.append(_Resp(200, body, {"content-type": "image/png"}))
        return self


@pytest.fixture(autouse=True)
def _capture_logs(caplog):
    caplog.set_level(logging.INFO)


@pytest.fixture
def logos(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "logo_dir", str(tmp_path / "logos"))
    monkeypatch.setenv("WHOP_PUBLIC_ASSET_BASE", _BASE)
    return tmp_path / "logos"


@pytest.fixture
def http(monkeypatch):
    return _HTTP().install(monkeypatch)


def _ruleset(*, required=True, url=None):
    rs = RuleSet()
    rs.logo.required = required
    if url:
        rs.logo.url = url
    return assign_enforcement(rs).dump()


def _campaign(*, required=True, logo_url=None, overrides=None, raw_url=_REL):
    meta = {"ruleset": _ruleset(required=required, url=logo_url)}
    if overrides:
        meta["rules_overrides"] = dict(overrides)
    if raw_url is not None:
        meta["discovered"] = {"raw": {"organization_logo_url": raw_url}}
    return SimpleNamespace(
        id=18,
        name="[whop] Boxabl",
        source_url=_DETAIL,
        source_metadata=meta,
        prize_pool_usd=None,
        assets_count=0,
    )


def _discovered(raw_url=_REL):
    raw = {} if raw_url is None else {"organization_logo_url": raw_url}
    return DiscoveredCampaign(
        provider="whop",
        external_id="exp/camp",
        detail_url=_DETAIL,
        name="Boxabl",
        raw=raw,
    )


class _DB:
    """Just enough session for the update branch of upsert_campaign."""

    def __init__(self, existing):
        self.existing = existing
        self.commits = 0
        self.rollbacks = 0
        self._n = 0
        self.fail_on_commit = None

    def execute(self, _stmt):
        self._n += 1
        n = self._n
        existing = self.existing

        class _Result:
            def scalar_one_or_none(_self):
                return existing if n % 2 == 1 else None

            def scalar_one(_self):
                return 0

        return _Result()

    def commit(self):
        self.commits += 1
        if self.fail_on_commit is not None and self.commits == self.fail_on_commit:
            raise RuntimeError("db down")

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, _obj):
        pass


def _upsert(db, raw_url=_REL):
    return upsert_campaign(db, _discovered(raw_url), status="queued")


def _overrides(camp):
    return (camp.source_metadata or {}).get("rules_overrides") or {}


def test_upsert_stores_png_and_worker_url(logos, http):
    http.png()
    camp = _campaign()
    db = _DB(camp)
    saved = _upsert(db)
    assert saved is camp
    assert logo_file_path(18).read_bytes() == _PNG
    overrides = _overrides(camp)
    assert overrides["logo_url"] == "/worker/campaigns/18/logo"
    assert overrides["logo_source"] == "whop_org"
    assert overrides["logo_whop_url"] == _REL
    assert "human:logo_file" not in (camp.source_metadata.get("rules_confirmations") or {})
    assert http.calls == [("GET", _BASE + _REL)]
    assert db.commits == 2
    cand = SimpleNamespace(start_time=0.0, end_time=10.0, extra_metadata={})
    spec = build_render_spec(camp, SimpleNamespace(extra_metadata={}), cand)
    assert spec["render_spec"]["required"]["logo"] is True
    assert spec["render_spec"]["required"]["logo_url_available"] is True
    assert spec["watermark"]["url"] == "/worker/campaigns/18/logo"


def test_absolute_whop_url_is_used_as_is(logos, http):
    http.png()
    camp = _campaign(raw_url=_ABS)
    assert apply_org_logo(camp) is True
    assert http.calls == [("GET", _ABS)]
    assert _overrides(camp)["logo_source"] == "whop_org"


def test_svg_is_rasterized_when_cairo_is_available(logos, http):
    try:
        import cairosvg
        cairosvg.svg2png(bytestring=_SVG)
    except Exception:
        pytest.skip("cairosvg/libcairo not available")
    http.queue.append(_Resp(200, _SVG, {"content-type": "image/svg+xml"}))
    camp = _campaign(raw_url="https://cdn.example.test/mark.svg")
    assert apply_org_logo(camp) is True
    assert logo_file_path(18).read_bytes().startswith(PNG_MAGIC)
    assert _overrides(camp)["logo_source"] == "whop_org"


def test_manual_logo_is_not_replaced(logos, http):
    store_logo_png(18, _OTHER)
    camp = _campaign(overrides={"logo_url": "/worker/campaigns/18/logo", "logo_source": "manual"})
    db = _DB(camp)
    _upsert(db)
    assert http.calls == []
    assert logo_file_path(18).read_bytes() == _OTHER
    assert _overrides(camp)["logo_source"] == "manual"
    assert db.commits == 1


def test_legacy_logo_url_without_source_is_kept(logos, http):
    store_logo_png(18, _OTHER)
    camp = _campaign(overrides={"logo_url": "/worker/campaigns/18/logo"})
    assert apply_org_logo(camp) is False
    assert http.calls == []
    assert logo_file_path(18).read_bytes() == _OTHER


def test_manual_upload_after_auto_is_not_overwritten(logos, http):
    http.png()
    camp = _campaign()
    db = _DB(camp)
    _upsert(db)
    # What POST /campaigns/{id}/rules/logo writes: new bytes, source manual.
    store_logo_png(18, _OTHER)
    overrides = dict(_overrides(camp))
    overrides["logo_source"] = "manual"
    overrides["logo_url"] = "/worker/campaigns/18/logo"
    overrides.pop("logo_whop_url", None)
    camp.source_metadata = {**camp.source_metadata, "rules_overrides": overrides}
    http.png()
    _upsert(db, raw_url="organizations/other/logo/new.hash.png")
    assert len(http.calls) == 1
    assert logo_file_path(18).read_bytes() == _OTHER
    assert _overrides(camp)["logo_source"] == "manual"


def test_two_upserts_download_once(logos, http):
    http.png()
    camp = _campaign()
    db = _DB(camp)
    _upsert(db)
    _upsert(db)
    assert len(http.calls) == 1
    assert db.commits == 3  # save+logo, then save only


def test_changed_whop_url_downloads_again(logos, http):
    http.png()
    http.queue.append(_Resp(200, _OTHER, {"content-type": "image/png"}))
    camp = _campaign()
    db = _DB(camp)
    _upsert(db)
    _upsert(db, raw_url="organizations/other/logo/new.hash.png")
    assert len(http.calls) == 2
    assert http.calls[1][1].endswith("organizations/other/logo/new.hash.png")
    assert logo_file_path(18).read_bytes() == _OTHER
    assert _overrides(camp)["logo_whop_url"].endswith("new.hash.png")


def test_failed_refresh_keeps_the_stored_logo(logos, http, caplog):
    http.png()
    camp = _campaign()
    db = _DB(camp)
    _upsert(db)
    http.queue.append(_Resp(404, b"", {"content-type": "text/plain"}))
    _upsert(db, raw_url="organizations/other/logo/missing.png")
    assert logo_file_path(18).read_bytes() == _PNG
    assert _overrides(camp)["logo_whop_url"] == _REL
    assert _overrides(camp)["logo_source"] == "whop_org"
    assert "http_404" in caplog.text


def test_no_url_keeps_the_campaign_and_logo_check_fails(logos, http, caplog):
    camp = _campaign(raw_url=None)
    db = _DB(camp)
    saved = _upsert(db, raw_url=None)
    assert saved is camp
    assert http.calls == []
    assert not logo_file_path(18).exists()
    assert "logo_url" not in _overrides(camp)
    assert "campaign_id=18" in caplog.text
    assert "no_organization_logo_url" in caplog.text
    logo = _logo_check(RuleSet.model_validate(camp.source_metadata["ruleset"]))
    assert logo["status"] == "fail"
    assert logo["detail"]


@pytest.mark.parametrize(
    "item,reason",
    [
        (_Resp(404, b"", {"content-type": "text/plain"}), "http_404"),
        (httpx.TimeoutException("timed out"), "timeout"),
        (_Resp(200, b"", {"content-type": "image/png", "content-length": str(MAX_LOGO_BYTES + 1)}), "larger_than_5mb"),
        (_Resp(200, b"x" * (MAX_LOGO_BYTES + 1), {"content-type": "image/png"}), "larger_than_5mb"),
        (_Resp(200, b"not an image", {"content-type": "application/octet-stream"}), "unsupported_format"),
    ],
)
def test_download_failures_do_not_break_upsert(logos, http, caplog, item, reason):
    http.queue.append(item)
    camp = _campaign()
    db = _DB(camp)
    saved = _upsert(db)
    assert saved is camp
    assert camp.source_metadata["ruleset"]["logo"]["required"] is True
    assert not logo_file_path(18).exists()
    assert "logo_url" not in _overrides(camp)
    assert "human:logo_file" not in (camp.source_metadata.get("rules_confirmations") or {})
    assert "campaign_id=18" in caplog.text
    assert reason in caplog.text
    assert db.rollbacks == 0


def test_campaign_without_logo_requirement_skips_download(logos, http):
    http.png()
    camp = _campaign(required=False)
    assert apply_org_logo(camp) is False
    assert http.calls == []
    assert not logo_file_path(18).exists()


def test_ruleset_image_url_is_not_replaced_by_the_org_logo(logos, http):
    http.png()
    camp = _campaign(logo_url="https://cdn.example.test/brand.png")
    assert apply_org_logo(camp) is False
    assert http.calls == []


def test_logo_save_failure_still_returns_the_campaign(logos, http, caplog):
    http.png()
    camp = _campaign()
    db = _DB(camp)
    db.fail_on_commit = 2
    saved = _upsert(db)
    assert saved is camp
    assert db.rollbacks == 1
    assert "campaign_id=18" in caplog.text
    assert "save_failed" in caplog.text


def _logo_check(rs):
    result = {
        "render_spec_version": 2,
        "probe": {"width": 1080, "height": 1920, "fps": 30.0, "has_audio": True, "duration": 30.0},
        "applied": {
            "watermark": {"applied": False},
            "captions": {"applied": False},
            "on_screen_text": {"applied": False},
        },
    }
    report = verify(
        rs,
        result=result,
        required={"width": 1080, "height": 1920},
        duration_window=(15, 45),
        clip_duration=30.0,
        tx_language=None,
        copies={},
    )
    return next(c for c in report["checks"] if c["rule"] == "logo")
