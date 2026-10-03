"""#41 — render_spec v2 built from the RuleSet (same keys as the worker)."""
from types import SimpleNamespace

from app.services.rules.enforcement import assign_enforcement
from app.services.rules.render_spec import build_render_spec, clip_segments
from app.services.rules.schema import RuleSet


def _asset():
    return SimpleNamespace(extra_metadata={"transcription": {"segments": [
        {"start": 8.0, "end": 12.0, "text": "before the clip starts here",
         "words": [{"start": 8.0, "end": 9.0, "word": "before"}, {"start": 9.5, "end": 10.5, "word": "boxabl"},
                   {"start": 11.0, "end": 12.0, "word": "casita"}]},
        {"start": 12.0, "end": 20.0, "text": "one two three four"},
        {"start": 50.0, "end": 55.0, "text": "outside"},
    ]}})


def _camp(rs, conf=None):
    meta = {"ruleset": assign_enforcement(rs).dump()}
    if conf:
        meta["rules_confirmations"] = conf
    return SimpleNamespace(id=1, source_metadata=meta)


def test_clip_segments_shift_and_trim():
    segs = clip_segments(_asset(), 10.0, 30.0)
    assert segs[0]["words"][0] == {"start": 0.0, "end": 0.5, "word": "boxabl"}
    assert segs[0]["text"] == "boxabl casita"
    assert segs[1] == {"start": 2.0, "end": 10.0, "text": "one two three four"}
    assert len(segs) == 2


def test_spec_logo_from_confirmation_and_text():
    rs = RuleSet()
    rs.captions.required = True
    rs.captions.brand_dictionary = ["BOXABL"]
    rs.logo.required = True
    rs.logo.timing = "end"
    rs.on_screen_text.required = True
    rs.on_screen_text.must_include = ["@boxabl"]
    cand = SimpleNamespace(start_time=10.0, end_time=30.0, extra_metadata={"on_screen_text": "Tiny home in 1 hour"})
    out = build_render_spec(_camp(rs, {"human:logo_file": {"note": "logo: https://cdn.x/logo.png."}}), _asset(), cand)
    assert out["captions"]["enabled"] and out["captions"]["brand_dictionary"] == ["BOXABL"]
    assert out["watermark"] == {"enabled": True, "url": "https://cdn.x/logo.png", "position": "top_right",
                                "width": 220, "start": 17.0, "end": 20.0}
    texts = [i["text"] for i in out["on_screen_text"]["items"]]
    assert texts == ["Tiny home in 1 hour", "@boxabl"]
    req = out["render_spec"]["required"]
    assert req["logo"] and req["logo_url_available"] and req["captions"] and req["on_screen_must_include"] == ["@boxabl"]


def test_spec_logo_required_without_url_is_flagged():
    rs = RuleSet()
    rs.logo.required = True
    cand = SimpleNamespace(start_time=0.0, end_time=20.0, extra_metadata={})
    out = build_render_spec(_camp(rs), SimpleNamespace(extra_metadata={}), cand)
    assert out["watermark"]["enabled"] is False
    assert out["render_spec"]["required"]["logo"] and not out["render_spec"]["required"]["logo_url_available"]
    assert out["captions"]["enabled"] is False  # no transcript → nothing to burn
