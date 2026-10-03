"""#50 — golden tests with the real briefs/RuleSets of campaigns #6 (ForgeGUI),
#13 (Stranger Than Heaven) and #18 (Boxabl). Includes cases that MUST fail."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.publish_copy import platform_copy
from app.services.rules.enforcement import assign_enforcement, blocking_items
from app.services.rules.extract import reclassify
from app.services.rules.render_spec import build_render_spec
from app.services.rules.schema import load_ruleset
from app.services.rules.verifier import verify

FX = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _rsd(i):
    return json.loads((FX / f"ruleset_{i}.json").read_text())


def _camp(i, conf=None):
    meta = {"ruleset": _rsd(i)}
    if conf:
        meta["rules_confirmations"] = conf
    return SimpleNamespace(id=i, name=f"[whop] · $1 · $2 · Campaign {i}", source_provider="whop", source_metadata=meta)


ASSET = SimpleNamespace(extra_metadata={"transcription": {"language": "en", "segments": [
    {"start": 100.0, "end": 104.0, "text": "this boxabl casita folds out in an hour",
     "words": [{"start": 100.0 + k * 0.5, "end": 100.4 + k * 0.5, "word": w}
               for k, w in enumerate("this boxabl casita folds out in an hour".split())]},
    {"start": 104.0, "end": 125.0, "text": "and forgegui builds the whole roblox UI for you"},
]}})


def _cand(caption, ost=""):
    return SimpleNamespace(start_time=100.0, end_time=125.0, reasoning="",
                           extra_metadata={"title": "Clip", "caption": caption, "on_screen_text": ost})


def _worker_result(spec, *, width=1080, height=1920, audio=True, duration=25.0, logo=None, text_override=None):
    """Simulate what the Windows worker (#4) reports for this render_spec."""
    from app.services.rules.verifier import _alnum  # noqa: F401  (same normalisation)

    cap = spec["captions"]
    text = " ".join(s["text"] for s in cap["segments"])
    for d in cap["brand_dictionary"]:
        text = " ".join(d if w.lower() == d.lower() else w for w in text.split())
    if text_override is not None:
        text = text_override
    wm = spec["watermark"]
    return {
        "render_spec_version": 2,
        "probe": {"width": width, "height": height, "fps": 30.0, "has_audio": audio, "duration": duration},
        "applied": {
            "captions": {"applied": cap["enabled"], "events": 6 if cap["enabled"] else 0, "text": text},
            "on_screen_text": {"applied": spec["on_screen_text"]["enabled"],
                               "texts": [{"text": i["text"]} for i in spec["on_screen_text"]["items"]]},
            "watermark": {"applied": wm["enabled"] if logo is None else logo, "position": wm["position"]},
        },
    }


def _run(i, cand, conf=None, **wk):
    camp = _camp(i, conf)
    rs = load_ruleset(camp.source_metadata)
    spec = build_render_spec(camp, ASSET, cand)
    res = _worker_result(spec, **wk)
    title, desc, _ = platform_copy(camp, None, cand, "youtube")
    rep = verify(rs, result=res, required=spec["render_spec"]["required"],
                 duration_window=(rs.duration.min_s, rs.duration.max_s), clip_duration=25.0,
                 tx_language="en", copies={"youtube": (title, desc)})
    return rep, spec, desc


# --- gate --------------------------------------------------------------------

def test_gate_blockers_golden():
    k = {i: blocking_items(assign_enforcement(reclassify(load_ruleset({"ruleset": _rsd(i)})))) for i in (6, 13, 18)}
    assert {b["kind"] for b in k[6]} == {"human"}
    assert sum(1 for b in k[6] if b["rule"].startswith("account:")) >= 10 and any(b["rule"] == "logo" for b in k[6])
    assert [b["kind"] for b in k[13] if b["kind"] == "unsupported"] == ["unsupported"]
    assert "f.io" in [b for b in k[13] if b["kind"] == "unsupported"][0]["text"]
    assert sorted(b["rule"] for b in k[18]) == ["logo", "pre_approval"]


# --- #18 Boxabl ------------------------------------------------------------------

LOGO = {"human:logo_file": {"note": "logo https://cdn.example.com/boxabl.png", "by": "jesus", "at": "x"}}


def test_18_pass_with_logo_url_confirmed():
    rep, spec, desc = _run(18, _cand("This tiny home unfolds in minutes."), conf=LOGO)
    assert spec["captions"]["brand_dictionary"] == ["BOXABL"]
    assert [i["text"] for i in spec["on_screen_text"]["items"]] == ["BOXABL"]
    assert spec["watermark"]["url"] == "https://cdn.example.com/boxabl.png"
    assert "@boxabl" in desc
    assert rep["status"] == "pass", rep["failed"]
    assert {"hook", "edit"} <= set(rep["review"])


def test_18_must_fail_without_logo_file():
    rep, _, _ = _run(18, _cand("This tiny home unfolds in minutes."))
    assert rep["failed"] == ["logo"]


def test_18_must_fail_brand_spelling_and_prohibited():
    rep, _, _ = _run(18, _cand("Kanye would love this."), conf=LOGO, text_override="this Boxabl casita")
    assert "captions.brand_dictionary" in rep["failed"]
    # the prohibited sentence is dropped from the copy, so only the subtitle check fails on 'Kanye'
    rep2, _, desc = _run(18, _cand("Kanye would love this. Great house."), conf=LOGO,
                         text_override="kanye BOXABL casita")
    assert "kanye" not in desc.lower() and "prohibitions.terms" in rep2["failed"]


# --- #6 ForgeGUI ------------------------------------------------------------------

LOGO6 = {"human:logo_file": {"note": "https://cdn.example.com/forge.png", "by": "jesus", "at": "x"}}


def test_6_pass_and_copy_order():
    rep, spec, desc = _run(6, _cand("I used ForgeGUI to build this UI."), conf=LOGO6)
    assert desc.splitlines()[-1].startswith("#forgeguipartner #robloxdev #roblox")
    assert any(i["text"] == "ForgeGUI" for i in spec["on_screen_text"]["items"])
    assert rep["status"] == "pass", rep["failed"]
    assert "copy.pinned_comment" in rep["review"]


@pytest.mark.parametrize("desc,rule", [
    ("I used ForgeGUI\n#roblox #robloxdev #forgeguipartner", "copy.hashtags"),
    ("#forgeguipartner #robloxdev #roblox\nI used ForgeGUI", "copy.hashtags"),
    ("I used it\n#forgeguipartner #robloxdev #roblox", "copy.must_mention_any"),
])
def test_6_copy_must_fail(desc, rule):
    rs = load_ruleset({"ruleset": _rsd(6)})
    rep = verify(rs, result={"render_spec_version": 2, "probe": {"width": 1080, "height": 1920, "has_audio": True,
                                                                 "duration": 30}, "applied": {}},
                 required={}, duration_window=(15, 45), clip_duration=30, tx_language="en",
                 copies={"youtube": ("t", desc)})
    assert rule in rep["failed"]


@pytest.mark.parametrize("kw,rule", [
    ({"width": 1920, "height": 1080}, "aspect"),
    ({"audio": False}, "audio"),
    ({"duration": 60.0}, "duration"),
    ({"logo": False}, "logo"),
])
def test_6_render_must_fail(kw, rule):
    rep, _, _ = _run(6, _cand("I used ForgeGUI to build this UI."), conf=LOGO6, **kw)
    assert rule in rep["failed"]


# --- #13 Stranger Than Heaven (parked: frame.io) -------------------------------------

def test_13_exact_caption_ad_own_line_and_original_audio():
    rep, spec, desc = _run(13, _cand("anything"))
    rs = load_ruleset({"ruleset": _rsd(13)})
    assert desc.startswith(rs.copy_rules.exact_caption.strip())
    assert "#Ad" in [ln.strip() for ln in desc.splitlines()]
    assert "@SEGA_West" in desc
    # logo is a Drive 'view' link in the brief → direct download URL for the worker
    assert spec["watermark"]["url"].startswith("https://drive.google.com/uc?export=download&id=")
    assert rep["status"] == "pass", rep["failed"]
    assert any(c["rule"] == "audio.original_only" and c["status"] == "pass" for c in rep["checks"])


def test_13_must_fail_altered_caption_and_buried_ad():
    rs = load_ruleset({"ruleset": _rsd(13)})
    base = dict(result={"render_spec_version": 2, "probe": {"width": 1080, "height": 1920, "has_audio": True,
                                                            "duration": 30}, "applied": {}},
                required={}, duration_window=(10, 45), clip_duration=30, tx_language="en")
    rep = verify(rs, copies={"youtube": ("t", "Play STRANGER THAN HEAVEN now #Ad @SEGA_West")}, **base)
    assert {"copy.exact_caption", "copy.ftc"} <= set(rep["failed"])
    rep = verify(rs, **{**base, "tx_language": "es"}, copies={"youtube": ("t", "x")})
    assert "language" in rep["failed"]
