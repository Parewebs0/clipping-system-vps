"""#41 — render_spec v2 built from the RuleSet (same keys as the worker)."""
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_db

from app.services.rules.enforcement import assign_enforcement
from app.services.rules.render_spec import DEFAULT_DECIDER, build_render_spec, clip_segments
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


def test_as_cta_extends_partial_logo_window_and_keeps_end_card_text():
    rs = RuleSet()
    rs.logo.required = True
    rs.logo.url = "https://cdn.x/logo.png"
    rs.logo.timing = "start"
    rs.logo.min_seconds = 3
    rs.logo.as_cta = True
    rs.logo.cta_text = "Visit boxabl.com"
    cand = SimpleNamespace(start_time=0.0, end_time=20.0, extra_metadata={})
    out = build_render_spec(_camp(rs), SimpleNamespace(extra_metadata={}), cand)
    assert out["watermark"]["start"] == 0.0 and out["watermark"]["end"] == 20.0
    cta = out["on_screen_text"]["items"][-1]
    assert cta["text"] == "Visit boxabl.com" and cta["start"] == 17.0 and cta["end"] == 20.0
    rs.logo.timing = "full"
    full = build_render_spec(_camp(rs), SimpleNamespace(extra_metadata={}), cand)
    assert full["watermark"]["start"] is None and full["watermark"]["end"] is None


def test_logo_override_is_preferred_over_ruleset_url():
    rs = RuleSet()
    rs.logo.required = True
    rs.logo.url = "https://cdn.x/old.png"
    camp = _camp(rs)
    camp.source_metadata["rules_overrides"] = {"logo_url": "/worker/campaigns/1/logo"}
    cand = SimpleNamespace(start_time=0.0, end_time=10.0, extra_metadata={})
    out = build_render_spec(camp, SimpleNamespace(extra_metadata={}), cand)
    assert out["watermark"]["url"] == "/worker/campaigns/1/logo"


def test_audio_policy_bumps_spec_to_v3():
    cand = SimpleNamespace(start_time=0.0, end_time=10.0, extra_metadata={})
    asset = SimpleNamespace(extra_metadata={})
    rs = RuleSet()
    out = build_render_spec(_camp(rs), asset, cand)
    assert out["render_spec"]["version"] == 3
    assert out["audio"] == {
        "original_only": False, "normalize": True, "target_lufs": -14, "true_peak_db": -1,
        "enhance": {"engine": "none", "strength": 1.0},
    }
    rs.audio.original_only = True
    rs.audio.no_added_music = True
    skipped = build_render_spec(_camp(rs), asset, cand)
    assert skipped["audio"]["original_only"] is True
    assert skipped["audio"]["normalize"] is False
    assert skipped["audio"]["enhance"] == {"engine": "none", "strength": 1.0}
    assert skipped["render_spec"]["version"] == 3


def test_audio_enhance_is_off_by_default_and_overridable():
    cand = SimpleNamespace(start_time=0.0, end_time=10.0, extra_metadata={})
    asset = SimpleNamespace(extra_metadata={})
    camp = _camp(RuleSet())
    camp.source_metadata["audio"] = {"enhance": {"engine": "deepfilter", "strength": 0.4}}
    out = build_render_spec(camp, asset, cand)
    assert out["audio"]["enhance"] == {"engine": "deepfilter", "strength": 0.4}
    assert out["audio"]["normalize"] is True
    camp.source_metadata["rules_overrides"] = {"audio_enhance": {"engine": "resemble", "strength": 2}}
    overridden = build_render_spec(camp, asset, cand)
    assert overridden["audio"]["enhance"] == {"engine": "resemble", "strength": 1.0}
    camp.source_metadata["rules_overrides"] = {"audio_enhance": {"engine": "nope", "strength": "x"}}
    fallback = build_render_spec(camp, asset, cand)
    assert fallback["audio"]["enhance"] == {"engine": "none", "strength": 1.0}
    rs = RuleSet()
    rs.audio.original_only = True
    forced = _camp(rs)
    forced.source_metadata["audio"] = {"enhance": {"engine": "deepfilter", "strength": 1}}
    skipped = build_render_spec(forced, asset, cand)
    assert skipped["audio"]["original_only"] is True
    assert skipped["audio"]["enhance"] == {"engine": "none", "strength": 1.0}


def test_cleanup_is_off_unless_the_campaign_opts_in():
    cand = SimpleNamespace(start_time=0.0, end_time=10.0, extra_metadata={})
    out = build_render_spec(_camp(RuleSet()), SimpleNamespace(extra_metadata={}), cand)
    assert out["cleanup"] == {
        "fillers": False,
        "max_pause_ms": None,
        "bad_takes": False,
        "filler_list": None,
    }
    camp = _camp(RuleSet())
    camp.source_metadata["cleanup"] = {
        "fillers": True,
        "max_pause_ms": 600,
        "bad_takes": "yes",
        "filler_list": ["eh", "o sea"],
    }
    opted = build_render_spec(camp, SimpleNamespace(extra_metadata={}), cand)
    assert opted["cleanup"] == {
        "fillers": True,
        "max_pause_ms": 600,
        "bad_takes": True,
        "filler_list": ["eh", "o sea"],
    }
    camp.source_metadata["cleanup"] = {"max_pause_ms": 0, "fillers": "nope", "filler_list": {"es": ["eh"]}}
    invalid = build_render_spec(camp, SimpleNamespace(extra_metadata={}), cand)
    assert invalid["cleanup"]["fillers"] is False
    assert invalid["cleanup"]["max_pause_ms"] is None
    assert invalid["cleanup"]["filler_list"] == {"es": ["eh"]}
    camp.source_metadata["cleanup"] = {"filler_list": {"es": "eh"}}
    as_phrase = build_render_spec(camp, SimpleNamespace(extra_metadata={}), cand)
    assert as_phrase["cleanup"]["filler_list"] == {"es": ["eh"]}


def test_reframe_defaults_to_center_and_keeps_output_size():
    cand = SimpleNamespace(start_time=0.0, end_time=10.0, extra_metadata={})
    out = build_render_spec(_camp(RuleSet()), SimpleNamespace(extra_metadata={}), cand)
    assert out["render_spec"]["version"] == 3
    assert out["reframe"]["mode"] == "center"
    assert out["reframe"]["layout"] == "single"
    assert out["reframe"]["smoothing"]["max_shift_frac"] == 0.05
    assert out["reframe"]["speakers"] == []
    assert out["render_spec"]["required"]["width"] == 1080
    assert out["render_spec"]["required"]["height"] == 1920


def test_reframe_override_speakers_and_aspect_size():
    rs = RuleSet()
    rs.aspect.ratio = "1:1"
    asset = SimpleNamespace(extra_metadata={"transcription": {"segments": [
        {"start": 8.0, "end": 14.0, "text": "hola", "speaker": "S0"},
        {"start": 14.0, "end": 20.0, "text": "adios", "speaker": "S1"},
        {"start": 40.0, "end": 42.0, "text": "fuera", "speaker": "S0"},
    ]}})
    camp = _camp(rs)
    camp.source_metadata["rules_overrides"] = {"reframe": {
        "mode": "auto", "layout": "split", "smoothing": 0.2, "sample_fps": 30,
    }}
    cand = SimpleNamespace(start_time=10.0, end_time=30.0, extra_metadata={})
    out = build_render_spec(camp, asset, cand)
    ref = out["reframe"]
    assert ref["mode"] == "auto" and ref["layout"] == "split"
    assert ref["smoothing"]["ema"] == 0.2
    assert ref["sample_fps"] == 10.0  # clamped to the 5–10 fps window
    assert ref["speakers"] == [
        {"start": 0.0, "end": 4.0, "speaker": "S0"},
        {"start": 4.0, "end": 10.0, "speaker": "S1"},
    ]
    assert out["captions"]["segments"][0]["speaker"] == "S0"
    assert (out["render_spec"]["required"]["width"], out["render_spec"]["required"]["height"]) == (1080, 1080)
    rs.aspect.ratio = "16:9"
    wide = build_render_spec(_camp(rs), SimpleNamespace(extra_metadata={}), cand)
    assert (wide["render_spec"]["required"]["width"], wide["render_spec"]["required"]["height"]) == (1920, 1080)


def test_spec_includes_decider_defaults():
    cand = SimpleNamespace(start_time=0.0, end_time=10.0, extra_metadata={})
    out = build_render_spec(_camp(RuleSet()), SimpleNamespace(extra_metadata={}), cand)
    assert out["decider"] == DEFAULT_DECIDER
    assert out["decider"]["chunk_chars"] == 4000
    assert out["decider"]["candidates_per_chunk"] == 3
    assert out["decider"]["top_n"] == 8
    camp = _camp(RuleSet())
    camp.source_metadata["decider"] = {"chunk_chars": 0, "candidates_per_chunk": 2, "top_n": 2}
    legacy = build_render_spec(camp, SimpleNamespace(extra_metadata={}), cand)
    assert legacy["decider"]["chunk_chars"] == 0
    assert legacy["decider"]["candidates_per_chunk"] == 2
    assert legacy["decider"]["top_n"] == 2


def test_spec_logo_required_without_url_is_flagged():
    rs = RuleSet()
    rs.logo.required = True
    cand = SimpleNamespace(start_time=0.0, end_time=20.0, extra_metadata={})
    out = build_render_spec(_camp(rs), SimpleNamespace(extra_metadata={}), cand)
    assert out["watermark"]["enabled"] is False
    assert out["render_spec"]["required"]["logo"] and not out["render_spec"]["required"]["logo_url_available"]
    assert out["captions"]["enabled"] is False  # no transcript → nothing to burn
