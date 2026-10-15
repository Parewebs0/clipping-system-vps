"""MOLI-49: render_spec caption style, decider keywords/emojis, hook entrance."""

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_db

from app.services.rules.render_spec import build_render_spec, clip_segments
from app.services.rules.schema import RuleSet


def _camp(rs, overrides=None):
    meta = {"ruleset": rs.dump()}
    if overrides:
        meta["rules_overrides"] = overrides
    return SimpleNamespace(id=1, source_metadata=meta)


def _asset():
    return SimpleNamespace(extra_metadata={"transcription": {
        "words": [
            {"start": 12.2, "end": 12.6, "word": "folds", "speaker": "SPEAKER_01"},
            {"start": 12.7, "end": 13.1, "word": "out"},
        ],
        "segments": [
            {"start": 10.0, "end": 12.0, "text": "boxabl casita", "speaker": "SPEAKER_00",
             "words": [{"start": 10.2, "end": 10.8, "word": "boxabl"},
                       {"start": 11.0, "end": 11.6, "word": "casita"}]},
            {"start": 12.0, "end": 14.0, "text": "folds out"},
        ],
    }})


def test_default_style_keeps_classic_and_does_not_force_font_or_case():
    cand = SimpleNamespace(start_time=10.0, end_time=14.0, extra_metadata={})
    out = build_render_spec(_camp(RuleSet()), _asset(), cand)
    style = out["captions"]["style"]
    assert style["preset"] == "classic"
    assert style["emoji"] is False
    assert style["censor"] == {"words": [], "beep": False}
    assert style["highlight_color"] == "&H0000FFFF"
    assert "uppercase" not in style and "font" not in style
    assert out["captions"]["keywords"] == [] and out["captions"]["emojis"] == []
    assert out["on_screen_text"]["entrance"] is False
    assert out["render_spec"]["version"] == 3


def test_ruleset_and_overrides_select_preset_emoji_and_censor():
    rs = RuleSet()
    rs.captions.preset = "karaoke"
    rs.captions.emoji = True
    rs.captions.censor_words = ["damn"]
    rs.captions.censor_beep = True
    rs.captions.uppercase = True
    rs.on_screen_text.entrance = True
    camp = _camp(rs, {"captions_style": {"preset": "pop", "font": "Anton", "emoji": False,
                                         "censor": {"words": ["heck"], "beep": False}},
                      "on_screen_entrance": False})
    cand = SimpleNamespace(
        start_time=10.0, end_time=14.0,
        extra_metadata={
            "on_screen_text": "Tiny home",
            "keywords": ["casita", "folds"],
            "emojis": [{"emoji": "🔥", "keyword": "casita"}, {"emoji": "nope"}],
        },
    )
    out = build_render_spec(camp, _asset(), cand)
    style = out["captions"]["style"]
    assert style["preset"] == "pop"
    assert style["font"] == "Anton"
    assert style["uppercase"] is True
    assert style["emoji"] is False
    assert style["censor"] == {"words": ["heck"], "beep": False}
    assert out["captions"]["keywords"] == ["casita", "folds"]
    assert out["captions"]["emojis"] == [{"emoji": "🔥", "keyword": "casita"}]
    hook = out["on_screen_text"]["items"][0]
    assert hook["text"] == "Tiny home" and "entrance" not in hook
    assert out["on_screen_text"]["entrance"] is False


def test_keywords_skip_blanks_then_keep_three():
    cand = SimpleNamespace(
        start_time=10.0, end_time=14.0,
        extra_metadata={"keywords": ["", "casita", "x" * 50, "folds", "extra", "nope"]},
    )
    out = build_render_spec(_camp(RuleSet()), _asset(), cand)
    assert out["captions"]["keywords"] == ["casita", "folds", "extra"]


def test_hook_entrance_is_only_on_the_decider_line():
    rs = RuleSet()
    rs.on_screen_text.entrance = True
    rs.on_screen_text.must_include = ["BOXABL"]
    cand = SimpleNamespace(start_time=0.0, end_time=8.0, extra_metadata={"on_screen_text": "Look"})
    out = build_render_spec(_camp(rs), SimpleNamespace(extra_metadata={}), cand)
    items = out["on_screen_text"]["items"]
    assert items[0]["text"] == "Look" and items[0]["entrance"] is True
    assert items[1]["text"] == "BOXABL" and "entrance" not in items[1]


def test_flat_whisperx_words_and_speakers_are_shifted_into_the_clip():
    segs = clip_segments(_asset(), 10.0, 14.0)
    assert segs[0]["words"][0] == {"start": 0.2, "end": 0.8, "word": "boxabl", "speaker": "SPEAKER_00"}
    # The second segment has no nested words; timings come from transcription.words.
    assert segs[1]["words"][0] == {"start": 2.2, "end": 2.6, "word": "folds", "speaker": "SPEAKER_01"}
    assert segs[1]["words"][1] == {"start": 2.7, "end": 3.1, "word": "out"}
    assert "speaker" not in segs[1]


def test_absolute_emoji_times_shift_into_the_clip():
    cand = SimpleNamespace(
        start_time=10.0, end_time=14.0,
        extra_metadata={"emojis": [{"emoji": "🎉", "start": 11.5, "end": 12.5}]},
    )
    out = build_render_spec(_camp(RuleSet()), _asset(), cand)
    assert out["captions"]["emojis"] == [{"emoji": "🎉", "start": 1.5, "end": 2.5}]
