"""MOLI-54 — brand templates: resolution, storage, render_spec and gates."""
import base64
import io
import struct
import uuid

import pytest

from app.config import settings
from app.services.rules import branding as B
from app.services.rules.branding import BrandingError
from app.services.rules.render_spec import build_render_spec
from app.services.rules.schema import RuleSet

# --- fixtures -----------------------------------------------------------------

_MIN_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _minimal_ttf() -> bytes:
    """A structurally valid (empty) TTF: header + 0 tables. Enough for magic checks."""
    # sfnt version 1.0, numTables=0, then searchRange/entrySelector/rangeShift
    return struct.pack(">4sHHHH", b"\x00\x01\x00\x00", 0, 16, 0, 0)


class _Camp:
    def __init__(self, meta=None, cid=1):
        self.id = cid
        self.source_metadata = meta or {}


@pytest.fixture
def brand_dir(tmp_path, monkeypatch):
    path = tmp_path / "branding"
    monkeypatch.setattr(settings, "branding_dir", str(path))
    return path


# --- storage: font ------------------------------------------------------------

def test_store_font_rejects_a_fake_ttf(brand_dir):
    with pytest.raises(BrandingError):
        B.store_font(1, b"not a font", "font.ttf")


def test_store_font_roundtrip_and_swap(brand_dir):
    ttf = _minimal_ttf()
    p = B.store_font(7, ttf, "brand.ttf")
    assert p.read_bytes() == ttf
    assert B.font_file_path(7) == p
    # storing an OTF removes the previous TTF (one font per campaign)
    otf = b"OTTO" + b"\x00" * 8
    p2 = B.store_font(7, otf, "brand.otf")
    assert p2.suffix == ".otf"
    assert B.font_file_path(7) == p2


def test_store_font_rejects_bad_name(brand_dir):
    with pytest.raises(BrandingError):
        B.store_font(1, _minimal_ttf(), "x.ttf.exe")
    with pytest.raises(BrandingError):
        B.store_font(1, _minimal_ttf(), "x y.ttf")  # space breaks the [A-Za-z0-9_-] stem rule


# --- storage: intro/outro ------------------------------------------------------

def test_store_media_image_roundtrip(brand_dir):
    p = B.store_media(3, "intro", _MIN_PNG, "card.png")
    assert p.name == "intro.png"
    assert B.media_file_path(3, "intro") == p
    assert B.media_file_path(3, "outro") is None


def test_store_media_rejects_html_as_mp4(brand_dir):
    with pytest.raises(BrandingError):
        B.store_media(3, "outro", b"<!DOCTYPE html>", "clip.mp4")


def test_store_media_rejects_unknown_side(brand_dir):
    with pytest.raises(BrandingError):
        B.store_media(3, "watermark", _MIN_PNG, "w.png")


# --- templates ----------------------------------------------------------------

def test_set_template_cap_of_four(brand_dir):
    c = _Camp()
    for i in range(4):
        B.set_template(c, {"name": f"t{i}"})
    assert len(brand_dir is not None and c.source_metadata["rules_overrides"]["branding"]) == 4
    with pytest.raises(BrandingError):
        B.set_template(c, {"name": "t5"})


def test_set_template_replaces_same_name(brand_dir):
    c = _Camp()
    B.set_template(c, {"name": "main", "max_words": 3})
    B.set_template(c, {"name": "main", "max_words": 2})
    assert len(c.source_metadata["rules_overrides"]["branding"]) == 1
    assert c.source_metadata["rules_overrides"]["branding"][0]["max_words"] == 2


def test_set_template_bad_name(brand_dir):
    c = _Camp()
    with pytest.raises(BrandingError):
        B.set_template(c, {"name": "a b/c"})
    with pytest.raises(BrandingError):
        B.set_template(c, {"name": ""})


# --- render_spec ----------------------------------------------------------------

# MOLI-49 caption presets always ride along. Branding only overrides position
# and max_words; the preset defaults stay in the same style dict.
_CAPTION_STYLE_DEFAULTS = {
    "preset": "classic",
    "highlight_color": "&H0000FFFF",
    "emoji": False,
    "censor": {"words": [], "beep": False},
}


def test_render_spec_without_branding_is_unchanged():
    """No branding configured → empty branding block, caption defaults intact."""
    rs = RuleSet()
    rs.captions.brand_dictionary = ["BOXABL"]
    campaign = _Camp({"ruleset": rs.dump()})
    asset = type("A", (), {"extra_metadata": {"transcription": {"segments": []}}})()
    cand = type("C", (), {"start_time": 5.0, "end_time": 15.0, "extra_metadata": {}})()
    spec = build_render_spec(campaign, asset, cand)
    assert spec["branding"] == {}
    assert spec["captions"]["style"] == {
        "position": "bottom",
        "max_words": 4,
        **_CAPTION_STYLE_DEFAULTS,
    }


def test_render_spec_branding_overrides_style(brand_dir):
    rs = RuleSet()
    rs.captions.brand_dictionary = ["BOXABL"]
    campaign = _Camp({
        "ruleset": rs.dump(),
        "rules_overrides": {"branding": {
            "position": "top", "max_words": 5,
            "font_url": "/worker/campaigns/9/fonts/brand.ttf", "font_name": "Brand",
            "intro": {"url": "/worker/campaigns/9/branding/intro", "max_seconds": 2.0},
        }},
    }, cid=9)
    asset = type("A", (), {"extra_metadata": {"transcription": {"segments": []}}})()
    cand = type("C", (), {"start_time": 5.0, "end_time": 15.0, "extra_metadata": {}})()
    spec = build_render_spec(campaign, asset, cand)
    assert spec["captions"]["style"] == {
        "position": "top",
        "max_words": 5,
        **_CAPTION_STYLE_DEFAULTS,
    }
    br = spec["branding"]
    assert br["font_name"] == "Brand"
    assert br["intro"]["max_seconds"] == 2.0


def test_render_spec_branding_serves_stored_files(brand_dir):
    """A stored font/intro gets its worker URL auto-filled in the spec."""
    ttf = _minimal_ttf()
    B.store_font(11, ttf, "brand.ttf")   # stored canonical as font.ttf
    B.store_media(11, "outro", _MIN_PNG, "card.png")
    rs = RuleSet()
    campaign = _Camp({"ruleset": rs.dump()}, cid=11)
    asset = type("A", (), {"extra_metadata": {"transcription": {"segments": []}}})()
    cand = type("C", (), {"start_time": 0.0, "end_time": 10.0, "extra_metadata": {}})()
    spec = build_render_spec(campaign, asset, cand)
    assert spec["branding"]["font_url"] == "/worker/campaigns/11/fonts/font.ttf"
    assert spec["branding"]["outro"]["url"] == "/worker/campaigns/11/branding/outro"


# --- resolver rules --------------------------------------------------------------

def test_resolve_prefers_override_and_never_merges_partial(brand_dir):
    rs = RuleSet()
    rs.branding.position = "center"  # RuleSet-level branding
    c = _Camp({"rules_overrides": {"branding": {"position": "top", "max_words": 6}}})
    out = B.resolve(c, rs)
    assert out["position"] == "top"
    assert "font_name" not in out  # partial override stays partial — no merge


def test_resolve_falls_back_to_ruleset_branding(brand_dir):
    rs = RuleSet()
    rs.branding.font_name = "Brand"
    rs.branding.font_url = "/worker/campaigns/2/fonts/brand.ttf"
    c = _Camp({})
    out = B.resolve(c, rs)
    assert out["font_name"] == "Brand"
    assert out["font_url"].endswith("/fonts/brand.ttf")