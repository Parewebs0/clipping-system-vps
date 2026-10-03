"""#35 — single source of truth: RuleSet drives spec, QA rules, windows, decider brief."""
from types import SimpleNamespace

from app.services.candidate_lifecycle import _build_spec_for_campaign
from app.services.rules.enforcement import assign_enforcement
from app.services.rules.runtime import decider_brief, duration_window, effective_spec, qa_rules, segment_text
from app.services.rules.schema import RuleSet


def _camp(rs=None, spec=None, instructions="Hook in the first 2 seconds", provider="whop"):
    meta = {"ruleset": assign_enforcement(rs).dump()} if rs is not None else {}
    return SimpleNamespace(source_metadata=meta, spec=spec or {}, source_instructions=instructions,
                           source_provider=provider)


def test_regex_parser_no_longer_used_for_approval():
    # The old parser read "2 seconds" from this text → window 2–12 s (#18 bug).
    rs = RuleSet()
    rs.hook.required, rs.hook.max_seconds = True, 2
    spec = _build_spec_for_campaign(_camp(rs))
    assert (spec.duration_min, spec.duration_max) == (15.0, 45.0)


def test_ruleset_values_win():
    rs = RuleSet()
    rs.duration.min_s, rs.duration.max_s = 10.0, 60.0
    rs.captions.required = True
    rs.logo.required, rs.logo.url = True, "https://x/logo.png"
    rs.language.code = "en"
    rs.prohibitions.terms = ["Kanye"]
    s = effective_spec(_camp(rs))
    assert (s.duration_min, s.duration_max, s.captions_required, s.watermark_url, s.language) == \
        (10.0, 60.0, True, "https://x/logo.png", "en")
    assert s.exclude_keywords == ["Kanye"]
    assert duration_window(_camp(rs)) == (10.0, 60.0)


def test_fallback_to_spec_then_defaults():
    s = effective_spec(_camp(spec={"duration_min": 20, "duration_max": 40, "format": "9:16", "language": "es"},
                             provider="manual"))
    assert (s.duration_min, s.duration_max, s.language) == (20.0, 40.0, "es")
    s = effective_spec(_camp(provider="whop"))
    assert s.duration_max > s.duration_min


def test_qa_rules_filled():
    r = qa_rules(_camp(RuleSet()))
    assert r == {"width": 1080, "height": 1920, "min_fps": 24.0, "require_audio": True, "codec": "h264",
                 "min_duration": 15.0, "max_duration": 45.0}


def test_segment_text_only_window():
    asset = SimpleNamespace(extra_metadata={"transcription": {"text": "all", "segments": [
        {"start": 0, "end": 5, "text": "intro Kanye"}, {"start": 10, "end": 20, "text": "the casita"}]}})
    assert segment_text(asset, 9, 21) == "the casita"
    assert "Kanye" in segment_text(asset, 0, 6)


def test_decider_brief_mentions_rules():
    rs = RuleSet()
    rs.hook.required, rs.hook.max_seconds = True, 2
    rs.prohibitions.terms = ["Kanye"]
    rs.on_screen_text.must_include = ["BOXABL"]
    b = decider_brief(_camp(rs))
    assert "first 2s" in b and "Kanye" in b and "BOXABL" in b
    assert decider_brief(_camp()) == ""
